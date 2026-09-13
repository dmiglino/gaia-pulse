"""Service layer for NLP ingestion: orchestrates parse → persist → confirm → execute."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.nlp import NLPIngestionEvent
from app.models.user import User
from app.nlp.intents import normalize_user_key
from app.repositories.user_repo import UserRepository
from app.schemas.body_metric import BodyMetricCreate
from app.schemas.meal import MealEventCreate, MealItemCreate, MealParticipantCreate
from app.schemas.pantry import PurchaseItem, PurchaseRequest, StockAdjustRequest
from app.schemas.suggestion import RecommendationPreferenceCreate
from app.schemas.workout import (
    WorkoutExerciseCreate,
    WorkoutParticipantCreate,
    WorkoutSessionCreate,
)
from app.services.body_metric_service import BodyMetricService
from app.services.meal_service import MealService
from app.services.pantry_service import PantryService
from app.services.suggestion_service import SuggestionService
from app.services.workout_service import WorkoutService

logger = logging.getLogger(__name__)

#: Un pesaje, un porcentaje de grasa o una medida de cintura son de **una** persona.
#: "los dos nos pesamos hoy, Rocío 60" no se puede anotar sin inventar el otro número,
#: así que un `user_key` que abarca a más de una persona no es un destino válido acá.
_SINGLE_PERSON_INTENTS = frozenset({"log_body_metric"})

#: Intents del hogar: el stock no es de nadie en particular.
_HOUSEHOLD_INTENTS = frozenset({"add_stock", "consume_stock"})

#: La clave que representa "la frase no nombró a nadie" en el mapa de destinos.
_SPEAKER_KEY = ""


def wrote_something(result: Any) -> bool:
    """¿Este resultado de intent escribió una fila?

    Hace falta porque la pantalla de resultado contaba `results | length`, que incluye
    los intents que se saltearon: un `mixed` solo mostraba un tilde verde y "1 cosa
    registrada" arriba de "nada que hacer con esto".
    """
    if not isinstance(result, dict):
        return False
    if result.get("unattributed") or result.get("skipped"):
        return False
    if any(result.get(key) for key in ("added", "consumed", "participants")):
        return True
    return bool(result.get("user") or result.get("preference_saved"))


class NLPService:
    def __init__(self, db: Session) -> None:
        self.db = db

    async def parse_and_save(
        self,
        user_id: int,
        text: str,
        input_type: str = "text",
        transcription: str | None = None,
    ) -> NLPIngestionEvent:
        """Parse natural language input and persist as a pending NLPIngestionEvent."""
        from app.nlp.parser import NLPParser

        user_repo = UserRepository(self.db)
        user = user_repo.get(user_id)
        speaking_user = normalize_user_key(user.name) if user else "user"

        parser = NLPParser()
        result = await parser.parse(text, speaking_user=speaking_user)

        event = NLPIngestionEvent(
            user_id=user_id,
            input_type=input_type,
            original_input=text,
            transcription=transcription,
            parsed_intent_json=[i.model_dump() for i in result.intents],
            parse_confidence=result.overall_confidence,
            parser_layer=result.parser_layer,
            status="pending_confirmation",
        )
        self.db.add(event)
        self.db.flush()
        self.db.commit()
        return event

    def get_event(self, event_id: int) -> NLPIngestionEvent | None:
        return self.db.get(NLPIngestionEvent, event_id)

    def household_user_map(self, household_id: int) -> dict[str, User]:
        """The `user_key` → person map that every intent is resolved against.

        Público porque la pantalla de confirmación tiene que mostrar *a quién* se le
        va a anotar cada intent, y la única forma de que el avatar del preview no
        mienta es que salga del mismo mapa que después usa `_execute_intent`.

        Si dos integrantes normalizan a la misma clave (dos "Diego", o "Ana" y "Ana
        María"), la clave se cae del mapa en vez de quedarse con uno de los dos: entre
        anotarle el dato a la persona equivocada y no anotarlo, no anotarlo es lo único
        que no falsea el registro de salud de nadie. La captura queda sin atribuir y la
        pantalla de confirmación lo dice.
        """
        users = UserRepository(self.db).get_household_users(household_id)
        by_key: dict[str, User] = {}
        collisions: set[str] = set()
        for user in users:
            key = normalize_user_key(user.name)
            if key in by_key:
                collisions.add(key)
            by_key[key] = user
        for key in collisions:
            by_key.pop(key, None)
        return by_key

    def resolve_intent_targets(
        self,
        intent: dict[str, Any],
        user_map: dict[str, User],
        acting_user: User | None,
    ) -> dict[str, list[User]]:
        """Clave cruda del intent → las personas a las que se le va a escribir.

        Es el **único** lugar donde se decide eso, y de ahí sale tanto lo que se
        escribe (`_execute_intent`) como lo que muestra el preview. Antes había dos
        resoluciones distintas: la plantilla buscaba la clave cruda y el servicio la
        normalizada, así que la pantalla de consentimiento podía prometer una
        atribución que la escritura no hacía.

        Una lista vacía significa "esta clave no se puede atribuir", y eso no se
        rellena con nadie: la clave `both` de un pesaje, o un nombre que no es del
        hogar, no se guardan. El fallback histórico era `next(iter(user_map.values()))`
        — el primero del hogar —, que le metía el dato de otra persona en el registro
        de salud de quien apareciera primero en la query.

        Un dict vacío significa "este intent no lleva persona": el stock es del hogar,
        y un `mixed` no llegó a decidir nada.
        """
        intent_type = intent.get("intent_type")
        if intent_type in _HOUSEHOLD_INTENTS:
            return {}

        if intent_type == "log_meal":
            keys = list((intent.get("items_per_user") or {}).keys())
        elif intent_type == "log_workout":
            keys = [k for k in (intent.get("participants") or []) if k]
        elif intent_type in ("log_body_metric", "update_preference"):
            key = intent.get("user_key")
            keys = [key] if key else []
        else:
            return {}

        if not keys:
            #: "me pesé hoy": la frase no nombra a nadie, y el dueño del dato es quien
            #: la escribió. Una comida sin `items_per_user` no tiene qué escribir.
            if intent_type == "log_meal" or acting_user is None:
                return {}
            return {_SPEAKER_KEY: [acting_user]}

        single = intent_type in _SINGLE_PERSON_INTENTS
        targets: dict[str, list[User]] = {}
        for key in keys:
            normalized = normalize_user_key(str(key))
            if normalized == "both":
                targets[key] = [] if single else list(user_map.values())
            else:
                person = user_map.get(normalized)
                targets[key] = [person] if person else []
        return targets

    @staticmethod
    def _flatten_targets(targets: dict[str, list[User]]) -> list[User]:
        people: list[User] = []
        for group in targets.values():
            for person in group:
                if person not in people:
                    people.append(person)
        return people

    def confirm_event(
        self,
        event_id: int,
        user_id: int,
        household_id: int,
        edited_intents: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Execute all confirmed intents and mark the event as confirmed."""
        event = self.db.get(NLPIngestionEvent, event_id)
        #: `status`: una captura se confirma **una** vez. Sin este chequeo, un segundo
        #: POST sobre el mismo id volvía a ejecutar todos los intents y escribía las
        #: filas de nuevo — un doble submit sin JS, o un "reenviar" del navegador,
        #: duplicaba la comida o el pesaje. La respuesta es la misma que para un id
        #: ajeno o inexistente, así que tampoco deja distinguir los tres casos.
        if not event or event.user_id != user_id or event.status != "pending_confirmation":
            return {"error": "Event not found or access denied", "results": []}

        intents = edited_intents or event.parsed_intent_json or []

        if not intents:
            event.status = "discarded"
            event.responded_at = datetime.now(timezone.utc)
            self.db.commit()
            return {
                "results": [],
                "notice": "Nothing to save — no intents were parsed.",
                "event_id": event_id,
            }

        user_map = self.household_user_map(household_id)
        acting_user = UserRepository(self.db).get(user_id)

        results: list[dict[str, Any]] = []
        for intent in intents:
            try:
                result = self._execute_intent(intent, user_id, household_id, user_map, acting_user)
                results.append(
                    {"status": "ok", "intent": intent.get("intent_type"), "result": result}
                )
            except Exception:
                #: El `str(e)` de la excepción se devolvía tal cual en el JSON de
                #: `/api/v1/nlp/confirm`, y un `ValidationError` de pydantic imprime el
                #: `input_value` — el peso, la comida, lo que la persona dictó — más los
                #: internos del modelo. Es el mismo mensaje crudo que se sacó de la
                #: pantalla; acá va al log, que es donde sirve para arreglarlo, y el
                #: cuerpo lleva un código estable.
                logger.exception(
                    "Intent execution failed: type=%s user_id=%s",
                    intent.get("intent_type"),
                    user_id,
                )
                results.append(
                    {
                        "status": "error",
                        "intent": intent.get("intent_type"),
                        "error": "execution_failed",
                    }
                )

        event.status = "confirmed" if not edited_intents else "edited_and_confirmed"
        event.responded_at = datetime.now(timezone.utc)
        self.db.commit()

        had_errors = any(r["status"] == "error" for r in results)
        return {
            "results": results,
            "event_id": event_id,
            "success": not had_errors,
            #: Cuántos intents escribieron algo de verdad. `len(results)` no sirve para
            #: eso: un intent salteado o sin atribuir también es un resultado.
            "saved_count": sum(
                1 for r in results if r["status"] == "ok" and wrote_something(r["result"])
            ),
        }

    def discard_event(self, event_id: int, user_id: int) -> bool:
        event = self.db.get(NLPIngestionEvent, event_id)
        #: El mismo chequeo de estado que `confirm_event`, y por la misma razón vista
        #: del otro lado: descartar una captura **ya confirmada** no borra las filas
        #: que escribió, así que la pantalla decía "descartado, no se guardó nada"
        #: sobre una comida que sí estaba en el registro de la otra persona. Y dejaba
        #: el `NLPIngestionEvent` — la única constancia de quién dictó una fila de
        #: salud, porque `body_metric_logs` no guarda autor — en `discarded` sobre una
        #: escritura que ocurrió.
        if not event or event.user_id != user_id or event.status != "pending_confirmation":
            return False
        event.status = "discarded"
        event.responded_at = datetime.now(timezone.utc)
        self.db.commit()
        return True

    def _execute_intent(
        self,
        intent: dict[str, Any],
        user_id: int,
        household_id: int,
        user_map: dict[str, User],
        acting_user: User | None = None,
    ) -> Any:
        intent_type = intent.get("intent_type")
        now = datetime.now(timezone.utc)
        if acting_user is None:
            acting_user = UserRepository(self.db).get(user_id)
        targets = self.resolve_intent_targets(intent, user_map, acting_user)

        if intent_type == "add_stock":
            svc = PantryService(self.db)
            items_raw = intent.get("items", [])
            items = [
                PurchaseItem(
                    food_name=i.get("food_name", ""),
                    quantity=i.get("quantity", 1),
                    unit=i.get("unit", "unit"),
                )
                for i in items_raw
                if i.get("food_name")
            ]
            if items:
                svc.process_purchase(household_id, user_id, PurchaseRequest(items=items))
            return {"added": len(items)}

        elif intent_type == "consume_stock":
            svc = PantryService(self.db)
            items_raw = intent.get("items", [])
            consumed = 0
            for i in items_raw:
                food_name = i.get("food_name", "")
                if not food_name:
                    continue
                svc.adjust_stock(
                    household_id,
                    user_id,
                    StockAdjustRequest(
                        food_name=food_name,
                        quantity=i.get("quantity", 1),
                        unit=i.get("unit", "unit"),
                        movement_type="consumption",
                    ),
                )
                consumed += 1
            return {"consumed": consumed}

        elif intent_type == "log_meal":
            svc = MealService(self.db)
            items_per_user = intent.get("items_per_user", {})
            #: Una fila por persona, no por clave: `both` se abre a todo el hogar (antes
            #: caía en el fallback y la cena compartida se guardaba solo a nombre del
            #: primero), y si `both` y un nombre se solapan, los ítems se acumulan en
            #: una sola participación en vez de duplicar a esa persona en la comida.
            items_by_user: dict[int, list[MealItemCreate]] = {}
            for user_key, items_list in items_per_user.items():
                items = [
                    MealItemCreate(
                        # FoodItemRef uses food_name; legacy dicts may still have 'name'
                        food_name=i.get("food_name") or i.get("name") or "",
                        quantity=i.get("qty") or i.get("quantity"),
                        unit=i.get("unit"),
                    )
                    for i in items_list
                ]
                for target_user in targets.get(user_key, []):
                    items_by_user.setdefault(target_user.id, []).extend(items)
            participants = [
                MealParticipantCreate(user_id=uid, items=items)
                for uid, items in items_by_user.items()
            ]
            if not items_per_user:
                return {"skipped": intent_type}
            if not participants:
                #: Nombres que no son del hogar, o una clave ambigua. No se elige a
                #: nadie por descarte: la comida no se guarda y la pantalla lo dice.
                return {"unattributed": True}
            svc.log_meal(
                household_id,
                MealEventCreate(
                    timestamp=intent.get("timestamp") or now,
                    meal_type=intent.get("meal_type", "other"),
                    context=intent.get("context", "home"),
                    participants=participants,
                ),
            )
            return {"participants": len(participants)}

        elif intent_type == "log_workout":
            svc = WorkoutService(self.db)
            target_users = self._flatten_targets(targets)
            if not target_users:
                return {"unattributed": True}

            exercises_raw = intent.get("exercises", [])
            participants = []
            for u in target_users:
                exercises = [
                    WorkoutExerciseCreate(
                        exercise_name=ex.get("name", ex) if isinstance(ex, dict) else str(ex),
                        muscle_group=ex.get("muscle_group") if isinstance(ex, dict) else None,
                    )
                    for ex in exercises_raw
                ]
                participants.append(WorkoutParticipantCreate(user_id=u.id, exercises=exercises))

            if participants:
                svc.log_workout(
                    household_id,
                    WorkoutSessionCreate(
                        timestamp_start=intent.get("timestamp") or now,
                        duration_minutes=intent.get("duration_minutes"),
                        workout_type=intent.get("workout_type"),
                        participants=participants,
                        source="text",
                    ),
                )
            return {"participants": len(participants)}

        elif intent_type == "log_body_metric":
            svc = BodyMetricService(self.db)
            #: Una medida corporal es de una sola persona. `both` acá no se reparte: la
            #: frase "los dos nos pesamos hoy, Rocío 60" traía un solo número y lo
            #: anotaba en el registro de salud del que hablaba. Sin destino único, no
            #: se escribe nada.
            people = self._flatten_targets(targets)
            if len(people) != 1:
                return {"unattributed": True}
            target_user = people[0]
            svc.log_metric(
                target_user.id,
                BodyMetricCreate(
                    timestamp=intent.get("timestamp") or now,
                    weight_kg=intent.get("weight_kg"),
                    body_fat_pct=intent.get("body_fat_pct"),
                    waist_cm=intent.get("waist_cm"),
                    sleep_hours=intent.get("sleep_hours"),
                ),
            )
            return {"user": target_user.display_name}

        elif intent_type == "update_preference":
            svc = SuggestionService(self.db)
            people = self._flatten_targets(targets)
            item_name = intent.get("item_name", "").strip()
            if not people or not item_name:
                return {"unattributed": True}
            for target_user in people:
                svc.save_preference(
                    target_user.id,
                    RecommendationPreferenceCreate(
                        item_type=intent.get("item_type", "exercise"),
                        item_name=item_name,
                        preference_signal=intent.get(
                            "preference_signal", intent.get("signal", "likes")
                        ),
                    ),
                )
            return {"preference_saved": len(people)}

        return {"skipped": intent_type}
