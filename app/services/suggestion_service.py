import logging
from datetime import datetime, timedelta, timezone

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
        if feedback.status in self._SUPPRESSING_STATUSES:
            suggestion.snoozed_until = datetime.now(timezone.utc) + timedelta(
                days=self._SNOOZE_DAYS
            )

        self._record_feedback_signal(suggestion, feedback, user_id)

        self.db.flush()
        self.db.commit()
        return suggestion

    #: Qué señal graba cada respuesta. `snoozed` no graba ninguna: hasta la 4.4 escribía
    #: `ignored_suggestion` con `value=0.0`, una fila que no entra ni en lo positivo ni en
    #: lo negativo —escrita y jamás leída—. Un "más tarde" es una supresión acotada en el
    #: tiempo, no una opinión sobre el sujeto, y donde vive es en `snoozed_until`, que
    #: escribe `respond_to_suggestion` y lee el motor.
    _FEEDBACK_SIGNALS: dict[str, tuple[str, float]] = {
        "accepted": ("accepted_suggestion", 1.0),
        "rejected": ("rejected_suggestion", -1.0),
        "dismissed": ("ignored_suggestion", -0.3),
    }

    #: Las dos respuestas que además de opinar (o de no opinar) **callan** al sujeto por un
    #: rato. `snoozed` es supresión pura —no escribe señal—; `dismissed` escribe −0.3 *y*
    #: suprime. Comparten la primera mitad del gesto: las dos dicen "no ahora", y lo que las
    #: separa es lo que dicen además. `accepted` no está porque el candidato ya se cumplió, y
    #: `rejected` tampoco porque su supresión no es temporal: `learning.rejected_subjects` lo
    #: saca de la lista mientras el "no" conserve la mitad de su peso, que es mucho más.
    _SUPPRESSING_STATUSES: frozenset[str] = frozenset({"snoozed", "dismissed"})

    #: Cuánto dura ese silencio. Tiene un piso y un techo, y los dos importan: el job de
    #: sugerencias corre dos veces por día a hora local fija —7:40 y 18:40, ver
    #: `scheduler._SCHEDULE`—, así que una ventana más corta que el hueco entre dos corridas
    #: (11 h) sería invisible —la misma tarjeta volvería a escribirse con el score apenas más
    #: bajo, que es exactamente el bucle que la 4.4.7 viene a cortar—; y tiene que quedar
    #: **por debajo** de los 7 días de la penalización por diversidad
    #: (`scorer._RECENT_SUGGESTION_DAYS`), que
    #: así queda como el escalón siguiente: primero el sujeto no aparece, después aparece pero
    #: más abajo, y al final vuelve a competir de igual a igual.
    #:
    #: No hace falta ningún job que "resucite" nada: la fila se queda en `snoozed`, y cuando
    #: `snoozed_until` queda en el pasado el sujeto se destraba solo porque la condición se
    #: evalúa contra el reloj en cada corrida.
    _SNOOZE_DAYS = 3

    #: Cuántos sujetos como máximo se aprenden de un motivo. Un motivo de verdad nombra una
    #: cosa, a veces dos —"no nos gusta el brócoli", "hoy no, y el yoga tampoco"—; cinco ya es
    #: holgado. El tope no está por desconfianza de la persona sino porque el largo del texto
    #: no acota el trabajo: 500 caracteres alcanzan para nombrar decenas de alimentos del
    #: catálogo, y sin tope una sola respuesta escribía decenas de filas en `behavior_signals`
    #: —una tabla sin poda— repetible a la velocidad de un POST. Y una frase que nombra veinte
    #: cosas no es una preferencia sobre veinte cosas: es otra clase de texto, y quedarse con
    #: los primeros cinco es preferible a aprender de todo.
    _MAX_MINED_SUBJECTS = 5

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

        signal_type, value = mapped

        #: El motivo se lee **antes** del chequeo de sujeto y no al final, porque las dos
        #: cosas son independientes: una fila anterior a la `0003` no tiene sujeto propio
        #: que grabar, pero si la persona escribió "no nos gusta el brócoli" el brócoli sí
        #: se puede aprender. Al final del método, esas respuestas no enseñaban nada.
        self._record_reason_signals(suggestion, feedback, user_id, value)

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
            #: El motivo **no** se copia acá, igual que en las señales minadas de abajo.
            #: Copiarlo parecía gratis —una sola fila, no una por sujeto— pero la razón para
            #: no hacerlo no es la multiplicidad sino la retención: `behavior_signals` no
            #: tiene job de poda, así que la frase que la persona escribió se quedaba para
            #: siempre en una segunda tabla y borrar `feedback_notes` no la borraba. Con
            #: `source_entity_id` apuntando a la fila, el texto sigue siendo alcanzable
            #: desde la señal y vive en un solo lugar, que es donde se puede borrar.
            context={"reason_written": True} if feedback.feedback_notes else None,
        )

    def _record_reason_signals(
        self,
        suggestion: Suggestion,
        feedback: SuggestionFeedback,
        user_id: int,
        value: float,
    ) -> None:
        """Aprender de lo que la persona **escribió** al responder, no solo del botón.

        *"No, hoy no me gusta el brócoli"* sobre una sugerencia titulada "Cená algo verde"
        enseñaba, hasta acá, que no gustan las cosas verdes: el sujeto de la señal es el de
        la tarjeta, y el motivo moría en `feedback_notes`. Lo que se hace ahora es buscar en
        esa frase los nombres que la app conoce —el catálogo de alimentos y las actividades
        del parser de reglas— y grabar una señal por cada uno.

        Tres decisiones que conviene tener escritas:

        - **El peso es el mismo que el de la respuesta** (−1.0 un rechazo, −0.3 un descarte)
          en vez de una perilla nueva: un "no" tibio nombrando el brócoli es un "no" tibio al
          brócoli. Y con `explicit_preference`, que es lo que ya escribe una preferencia
          declarada y dura 90 días: la persona lo dijo con palabras, no se lo dedujo de una
          semana. El signo sale de la respuesta, así que un motivo escrito junto a un "sí"
          enseña en positivo — la tarjeta solo pide motivo en las negativas, pero la ruta
          JSON lo admite en todas y no hay razón para leer un elogio como una queja.
        - **No filtra nada.** `learning.rejected_subjects` solo mira `rejected_suggestion`,
          así que un sujeto minado de una frase baja el score y nunca veta un candidato. Es
          deliberado: esto es una lectura de texto libre, y una lectura puede equivocarse —el
          costo de equivocarse ordenando es que algo salga tercero, el de equivocarse
          filtrando es que no salga nunca y nadie entienda por qué—.
        - **El sujeto de la propia tarjeta se saltea**, porque ya lo grabó el bloque de
          arriba: nombrarlo en el motivo no lo hace pesar el doble. Si la fila no tiene
          sujeto —las anteriores a la `0003`— no hay nada que saltear y se mina todo.
        - **`snoozed` no mina**, porque quien llama no le pasa ningún valor: es la única
          respuesta que no tiene signo. Y eso es correcto, no un descuido de la
          implementación: "más tarde" es una afirmación sobre *el momento*, no sobre la
          cosa, y un motivo escrito al lado de un "más tarde" ("hoy no, estamos con lo de
          ayer") explica la demora. Leerlo como un veto al alimento que nombre sería
          inventar una opinión que la persona no dio. El formulario de la tarjeta manda
          `rejected` justamente para no depender de esa distinción.

        Y dos límites que conviene tener escritos en vez de descubrir:

        - **El signo es uno para toda la frase**, así que *"no nos gusta el brócoli,
          preferimos el pollo"* graba −1.0 para los dos. Separar los dos sentimientos pide
          leer el alcance de la negación, que es exactamente la interpretación que este
          matcher no hace. Se aguanta porque lo minado **ordena y no filtra**: el pollo baja
          un puesto y vuelve a subir con el primer acto que lo confirme —una compra, una
          comida—, que es una señal más confiable que la sintaxis de una queja.
        - **El vocabulario de actividades es de claves en inglés** (`rules._EXERCISE_MAP`),
          así que de una frase en castellano solo salen los nombres que se escriben igual en
          los dos idiomas —los préstamos, que son varios: "yoga", "pilates", "spinning",
          "crossfit", "cardio", "running", "hiit", "zumba"—. Lo que no aparece es lo que la
          casa escribiría en castellano y el mapa no tiene: *"odio correr"*, *"caminar"*,
          *"pesas"* no enseñan nada hoy. Ensancharlo toca el parser de capturas y no solo
          esto, y la 4.5 reemplaza esa lista fija por `ExerciseType` de todos modos: ahí es
          donde corresponde, con los nombres que la base ya tiene.
        """
        reason = (feedback.feedback_notes or "").strip()
        if not reason:
            return

        own_subject = (
            learning.subject_key(str(suggestion.subject_type), str(suggestion.subject_name))
            if suggestion.subject_type and suggestion.subject_name
            else None
        )
        mined = [
            subject
            for subject in learning.subjects_in_text(
                reason, foods=learning.food_vocabulary(self.db)
            )
            if subject != own_subject
        ]
        if len(mined) > self._MAX_MINED_SUBJECTS:
            logger.info(
                "Motivo de la sugerencia %d: %d sujetos nombrados, se graban los primeros %d.",
                suggestion.id,
                len(mined),
                self._MAX_MINED_SUBJECTS,
            )
            mined = mined[: self._MAX_MINED_SUBJECTS]

        for subject_type, subject_name in mined:
            learning.record_signal(
                self.db,
                user_id=user_id,
                signal_type="explicit_preference",
                subject_type=subject_type,
                subject_name=subject_name,
                value=value,
                source_type="explicit",
                source_entity_type="suggestion",
                source_entity_id=suggestion.id,
                #: El motivo **no** se copia acá, y es a propósito: queda una sola vez en
                #: `suggestions.feedback_notes`, y `source_entity_id` apunta a esa fila. Con
                #: la copia, una frase que nombra media docena de cosas escribía media docena
                #: de copias de la misma oración personal en una segunda tabla que nadie poda
                #: —así que borrar el motivo de la sugerencia no lo borraba—. Lo que queda es
                #: de dónde salió la señal, que es lo que no se puede reconstruir.
                context={"mined_from": "feedback_notes"},
            )
            #: A DEBUG y no a INFO: producción corre en INFO, y el sujeto con su signo *es*
            #: la preferencia alimentaria de la casa. La convención del repo es esa —los
            #: INFO llevan cuentas e ids, el contenido va a DEBUG— y acá vale doblemente,
            #: porque quien lee los logs no necesita permiso sobre la base para leerlos.
            logger.debug(
                "Motivo de la sugerencia %d: aprendido %s=%r con valor %+.1f.",
                suggestion.id,
                subject_type,
                subject_name,
                value,
            )
        if mined:
            logger.info(
                "Motivo de la sugerencia %d: %d sujeto(s) aprendido(s).",
                suggestion.id,
                len(mined),
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
