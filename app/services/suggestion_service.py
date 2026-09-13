import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.suggestion import Suggestion
from app.models.user import User
from app.recommendations import learning
from app.repositories.suggestion_repo import SuggestionRepository
from app.schemas.suggestion import RecommendationPreferenceCreate, SuggestionFeedback

logger = logging.getLogger(__name__)


class SuggestionService:
    #: Ya no hay `self.signal_repo`: las dos escrituras de señales de este servicio pasan
    #: por `learning.record_signal`, que es el único punto que valida el sujeto y normaliza
    #: el nombre. Tener el repositorio a mano acá era la puerta por la que se escribía
    #: salteándolo.
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = SuggestionRepository(db)

    def get_pending(self, user_id: int, household_id: int) -> list[Suggestion]:
        return self.repo.get_pending_for_user(user_id, household_id)

    def generate_for_user(self, user: User, limit: int = 10) -> list[Suggestion]:
        """Run the recommendation engine on demand for *user*."""
        from app.recommendations.engine import RecommendationEngine

        return RecommendationEngine().generate_for_user(self.db, user, limit=limit)

    def respond_to_suggestion(
        self,
        suggestion_id: int,
        feedback: SuggestionFeedback,
        user_id: int,
        household_id: int,
    ) -> Suggestion | None:
        """Guardar la respuesta a una sugerencia propia.

        `household_id` no está de adorno: sin él esto era `repo.get(suggestion_id)`, o
        sea que cualquier sesión válida podía responder por cualquier fila de la tabla.
        Ver `SuggestionRepository.get_owned`.
        """
        suggestion = self.repo.get_owned(suggestion_id, user_id, household_id)
        if not suggestion:
            return None

        suggestion.status = feedback.status
        suggestion.feedback_notes = feedback.feedback_notes
        suggestion.responded_at = datetime.now(timezone.utc)

        self._record_feedback_signal(suggestion, feedback, user_id)

        self.db.flush()
        self.db.commit()
        return suggestion

    #: Qué señal graba cada respuesta. `snoozed` no graba ninguna: hasta la 4.4 escribía
    #: `ignored_suggestion` con `value=0.0`, una fila que no entra ni en lo positivo ni en
    #: lo negativo —escrita y jamás leída—. Un "más tarde" es una supresión acotada en el
    #: tiempo, no una opinión sobre el sujeto, y donde vive es en `snoozed_until`
    #: (4.4.7), no en la tabla de aprendizaje.
    _FEEDBACK_SIGNALS: dict[str, tuple[str, float]] = {
        "accepted": ("accepted_suggestion", 1.0),
        "rejected": ("rejected_suggestion", -1.0),
        "dismissed": ("ignored_suggestion", -0.3),
    }

    #: `RecommendationPreference.item_type` es más fino que el vocabulario de sujetos
    #: (`food/recipe/exercise/cuisine/meal_type/ingredient`), y esa finura no le sirve al
    #: aprendizaje: una receta y un ingrediente se comparan contra los mismos candidatos
    #: de comida. `meal_type` es un hábito —"no desayuno" no habla de un alimento—.
    _PREFERENCE_SUBJECT_TYPES: dict[str, str] = {
        "food": "food",
        "ingredient": "food",
        "recipe": "food",
        "cuisine": "food",
        "exercise": "exercise",
        "meal_type": "habit",
    }

    def _record_feedback_signal(
        self, suggestion: Suggestion, feedback: SuggestionFeedback, user_id: int
    ) -> None:
        """Guardar lo que esta respuesta enseña, contra el sujeto de la sugerencia.

        Hasta la 4.4 esto grababa `entity_name=suggestion.title.lower()[:200]` y el
        `entity_type` salía de un mapa de categorías, así que lo aprendido era **la
        redacción**: rechazar *"Time to get moving!"* enseñaba sobre las palabras
        "moving", "boost" y "energy", y el scorer las cruzaba por bolsa de palabras contra
        candidatos de comida. El sujeto lo declara ahora el generador y viaja en la fila.
        """
        mapped = self._FEEDBACK_SIGNALS.get(feedback.status)
        if mapped is None:
            return

        if not suggestion.subject_type or not suggestion.subject_name:
            #: Sin sujeto no se graba nada. Las filas anteriores a la `0003` no lo tienen
            #: y no se puede derivar del título sin volver a cometer el error, así que
            #: responderlas no enseña —que es correcto— y queda anotado en el log en vez
            #: de en la tabla.
            logger.info(
                "Sugerencia %d respondida con %r sin sujeto: no se graba señal.",
                suggestion.id,
                feedback.status,
            )
            return

        signal_type, value = mapped
        learning.record_signal(
            self.db,
            user_id=user_id,
            signal_type=signal_type,
            subject_type=suggestion.subject_type,
            subject_name=suggestion.subject_name,
            value=value,
            source_type="explicit",
            source_entity_type="suggestion",
            source_entity_id=suggestion.id,
            #: El motivo de texto libre iba a `feedback_notes` y ahí moría. Guardarlo
            #: también en la señal es lo que permite que la 4.4.7 lo lea sin volver a
            #: buscar la sugerencia.
            context={"reason": feedback.feedback_notes} if feedback.feedback_notes else None,
        )

    def save_preference(
        self, user_id: int, data: RecommendationPreferenceCreate
    ) -> None:
        self.repo.upsert_preference(
            user_id=user_id,
            item_type=data.item_type,
            item_name=data.item_name,
            signal=data.preference_signal,
            strength=data.strength,
            notes=data.notes,
        )
        # Also record as an explicit signal
        #: El peso es la fuerza que declaró la persona, no un ±1 fijo. `strength` (0–1) ya
        #: viajaba en el schema y se guardaba en la preferencia, y la señal lo ignoraba: un
        #: "no me encanta" (0.3) pesaba lo mismo que un "no lo como" (1.0). Desde la 4.4.3
        #: el ajuste del scorer es la opinión por la evidencia que la sostiene, y esto es lo
        #: que hace que una opinión tibia entre como tibia en vez de como certeza.
        direction = -1.0 if data.preference_signal in ("dislikes", "impossible", "avoid") else 1.0
        value = direction * data.strength
        subject_type = self._PREFERENCE_SUBJECT_TYPES.get(data.item_type)
        if subject_type is None:
            #: `item_type` es texto libre de 40 caracteres que llega del NLP, y hasta la
            #: 4.4 se guardaba tal cual como `entity_type`: una preferencia de tipo
            #: "ingredient" escribía señales que el scorer —que compara con `"food"`—
            #: nunca iba a mirar. Ahora un tipo que no sabemos traducir no escribe una
            #: fila muerta; deja rastro acá.
            logger.info(
                "Preferencia de tipo %r sin sujeto equivalente: no se graba señal.",
                data.item_type,
            )
        else:
            learning.record_signal(
                self.db,
                user_id=user_id,
                signal_type="explicit_preference",
                subject_type=subject_type,
                subject_name=data.item_name,
                value=value,
                source_type="explicit",
            )
        self.db.commit()

    def get_user_preferences(self, user_id: int) -> list:
        return self.repo.get_user_preferences(user_id)
