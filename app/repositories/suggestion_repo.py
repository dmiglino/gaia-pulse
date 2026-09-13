from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.models.signal import BehaviorSignal
from app.models.suggestion import RecommendationPreference, Suggestion
from app.repositories.base import BaseRepository


def _visible_to(user_id: int, household_id: int) -> ColumnElement[bool]:
    """Las sugerencias que *esta* persona puede ver.

    El filtro era ``or_(scope_user_id == user_id, household_id == household_id)``, y
    `engine._make_user_suggestion` escribe **las dos** columnas en una sugerencia
    personal (`household_id=user.household_id`, `scope_user_id=user.id`). O sea que la
    cláusula de hogar matcheaba las sugerencias personales del otro integrante: Diego
    abría `/suggestions/` y leía las de Rocío, con su análisis de sangre y sus hábitos
    adentro del texto.

    Una sugerencia es de todo el hogar cuando **no** nombra a nadie
    (`scope_user_id IS NULL`, que es lo que escribe `_make_household_suggestion`). Con
    eso la regla 4 de `AGENTS.md` vuelve a cumplirse: todo dato personal se filtra por
    el usuario que pregunta, incluso entre dos personas de la misma casa.
    """
    return or_(
        Suggestion.scope_user_id == user_id,
        and_(
            Suggestion.household_id == household_id,
            Suggestion.scope_user_id.is_(None),
        ),
    )


class SuggestionRepository(BaseRepository[Suggestion]):
    def __init__(self, db: Session) -> None:
        super().__init__(Suggestion, db)

    def get_pending_for_user(self, user_id: int, household_id: int) -> list[Suggestion]:
        stmt = (
            select(Suggestion)
            .where(
                Suggestion.status == "pending",
                _visible_to(user_id, household_id),
            )
            .order_by(Suggestion.priority.desc(), Suggestion.created_at.desc())
            .limit(20)
        )
        return list(self.db.scalars(stmt).all())

    def get_owned(
        self, suggestion_id: int, user_id: int, household_id: int
    ) -> Suggestion | None:
        """Una sugerencia, solo si es de quien pregunta.

        `respond_to_suggestion` hacía `self.repo.get(suggestion_id)` y nada más: con
        cualquier sesión válida se podía aceptar, descartar o rechazar **cualquier**
        fila de la tabla, incluida la de otro hogar. Y peor que cambiarle el estado a
        alguien ajeno: la señal de comportamiento se grababa contra quien apretaba el
        botón, así que el título de una sugerencia de otra persona entraba a su modelo
        de aprendizaje.

        Devuelve `None` tanto si no existe como si no le corresponde — a propósito: la
        respuesta no tiene que dejar distinguir un id inexistente de uno ajeno.

        Y solo si sigue pendiente. Sin eso, dos clics rápidos en "Buena idea" entraban
        los dos al servicio y grababan **dos** `BehaviorSignal` sobre la misma entidad,
        con lo que un doble clic pesaba el doble en el scorer. El primer swap reemplaza
        la tarjeta, pero la segunda request ya salió. Como responder algo ya respondido
        es justo lo que "esa sugerencia ya no está disponible" describe, el filtro va
        acá y la ruta no necesita una rama nueva.
        """
        stmt = select(Suggestion).where(
            Suggestion.id == suggestion_id,
            Suggestion.status == "pending",
            _visible_to(user_id, household_id),
        )
        return self.db.scalar(stmt)

    #: Acá había un `get_recent_suggestions` sin ningún llamador, con la misma fuga que
    #: `get_pending_for_user`: el motor tiene su propia copia de esa pregunta en
    #: `engine._get_recent_suggestions_for_user`. Dos implementaciones de la misma
    #: consulta, una sin usar, es exactamente cómo se filtró la primera vez, así que se
    #: borró en lugar de arreglarse por duplicado.

    def get_user_preferences(self, user_id: int) -> list[RecommendationPreference]:
        stmt = select(RecommendationPreference).where(
            RecommendationPreference.user_id == user_id
        )
        return list(self.db.scalars(stmt).all())

    def upsert_preference(
        self,
        user_id: int,
        item_type: str,
        item_name: str,
        signal: str,
        strength: float = 1.0,
        notes: str | None = None,
    ) -> RecommendationPreference:
        stmt = select(RecommendationPreference).where(
            RecommendationPreference.user_id == user_id,
            RecommendationPreference.item_type == item_type,
            RecommendationPreference.item_name == item_name.lower(),
        )
        pref = self.db.scalar(stmt)
        if pref is None:
            pref = RecommendationPreference(
                user_id=user_id,
                item_type=item_type,
                item_name=item_name.lower(),
                preference_signal=signal,
                strength=strength,
                notes=notes,
            )
            self.db.add(pref)
        else:
            pref.preference_signal = signal
            pref.strength = strength
            if notes:
                pref.notes = notes
            pref.updated_at = datetime.now(UTC)
        self.db.flush()
        return pref


class BehaviorSignalRepository(BaseRepository[BehaviorSignal]):
    def __init__(self, db: Session) -> None:
        super().__init__(BehaviorSignal, db)

    def get_user_signals(
        self,
        user_id: int,
        signal_type: str | None = None,
        entity_type: str | None = None,
        limit: int = 200,
    ) -> list[BehaviorSignal]:
        stmt = (
            select(BehaviorSignal)
            .where(BehaviorSignal.user_id == user_id)
            .order_by(BehaviorSignal.created_at.desc())
            .limit(limit)
        )
        if signal_type:
            stmt = stmt.where(BehaviorSignal.signal_type == signal_type)
        if entity_type:
            stmt = stmt.where(BehaviorSignal.entity_type == entity_type)
        return list(self.db.scalars(stmt).all())

    def record(
        self,
        user_id: int,
        signal_type: str,
        entity_type: str,
        entity_name: str,
        value: float = 1.0,
        source_type: str = "implicit",
        entity_id: int | None = None,
        source_entity_type: str | None = None,
        source_entity_id: int | None = None,
        context: dict | None = None,
    ) -> BehaviorSignal:
        signal = BehaviorSignal(
            user_id=user_id,
            signal_type=signal_type,
            entity_type=entity_type,
            entity_name=entity_name.lower(),
            entity_id=entity_id,
            value=value,
            source_type=source_type,
            source_entity_type=source_entity_type,
            source_entity_id=source_entity_id,
            context_json=context,
        )
        self.db.add(signal)
        self.db.flush()
        return signal
