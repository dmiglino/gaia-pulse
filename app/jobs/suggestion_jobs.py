"""Background job that triggers recommendation engine for all active users."""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.clock import as_utc
from app.db.session import SessionLocal
from app.models.user import User
from app.repositories.household_repo import HouseholdRepository
from app.repositories.suggestion_repo import BehaviorSignalRepository, SuggestionRepository
from app.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)

#: Cuántas tarjetas puede dejar una corrida por entidad. El mismo número para la persona y
#: para la casa, y escrito una vez: estaba dos veces como `limit=5` literal, y son las dos
#: mitades de la misma pantalla —Home mezcla las personales con las del hogar—, así que un
#: número más alto en un lado se nota como una lista que se llenó de compras.
_SUGGESTIONS_PER_RUN = 5

#: Cuán ancha es la ventana en la que una tarjeta es barrible: un día, porque
#: `absence_sweep` corre una vez por día. No se deriva de `_SCHEDULE` porque ahí la
#: periodicidad está expresada como hora del día y no como intervalo; si el job pasara a
#: correr dos veces, esto y esa entrada tienen que moverse juntos, y por eso el número está
#: acá con su razón y no escrito en medio de la consulta.
_SWEEP_WINDOW_DAYS = 1


def run_suggestion_generation() -> None:
    """Generar las tarjetas de la corrida: primero las de cada persona, después las de la casa.

    Las dos recorridas son la misma corrida porque las dos mitades salen en la misma
    pantalla, y están separadas porque las sugerencias de despensa son **del hogar**
    (`scope_type="household"`, sin dueño): anidar el loop de hogares dentro del de personas
    generaría la lista de compras dos veces en una casa de dos, y las dos serían la misma
    lista con distinta hora.
    """
    logger.info("Running suggestion generation job")
    db = SessionLocal()
    try:
        #: Vía repositorio y no con un `select(User)` acá: `app/jobs/` armando
        #: consultas sobre un modelo es lo que la regla de capas de `AGENTS.md`
        #: reserva a los repositorios.
        users = UserRepository(db).list_active()

        # Import here to avoid circular deps at module load time
        from app.recommendations.engine import RecommendationEngine

        engine = RecommendationEngine()

        #: No hay guardia de "¿existe el hogar?": `User.household_id` es `nullable=False`
        #: con FK, así que un usuario activo sin hogar no es un estado que la base permita.
        #: El `db.get(Household, ...)` que había acá no podía dar falso nunca.
        for user in users:
            try:
                new_suggestions = engine.generate_for_user(db, user, limit=_SUGGESTIONS_PER_RUN)
                logger.info("Generated %d suggestions for user %d", len(new_suggestions), user.id)
            except Exception:
                #: El `rollback` es parte del reparto, no una precaución de más: sin él, lo
                #: que la corrida fallida dejó pendiente en la sesión lo commitea la
                #: siguiente entidad. Es el mismo reparto que `_sweep_user` ya hacía —una
                #: excepción de una no se lleva puesto lo de la otra— y acá faltaba.
                db.rollback()
                logger.exception("Error generating suggestions for user %d", user.id)

        #: Y después las casas. `HouseholdRepository.list_all()` y no un `select` acá por la
        #: regla de capas, y `list_all` y no la `get_all()` heredada porque esa corta en 100
        #: sin ordenar. Hasta la 4.5.7 este loop no existía: `generate_for_household` estaba
        #: escrito y filtrado, y no lo llamaba nadie — el generador de despensa y compras no
        #: llegaba a ninguna pantalla.
        for household in HouseholdRepository(db).list_all():
            try:
                household_suggestions = engine.generate_for_household(
                    db, household, limit=_SUGGESTIONS_PER_RUN
                )
                logger.info(
                    "Generated %d household suggestions for household %d",
                    len(household_suggestions),
                    household.id,
                )
            except Exception:
                db.rollback()
                logger.exception(
                    "Error generating household suggestions for household %d", household.id
                )

    except Exception:
        logger.exception("Error in suggestion_generation job")
    finally:
        db.close()


def run_absence_sweep() -> None:
    """Grabar como señal las sugerencias que pasaron su semana sin que nada las usara.

    Es el escritor de `learning.ABSENCE_SIGNAL_TYPE`, y existe como job porque el dato que
    lo produce es el paso del tiempo: nada llama a la app "siete días después de sugerir
    lentejas para contarle que no las comió". Un lector que dedujera la ausencia al vuelo
    tampoco alcanzaría —tendría que releer todas las tarjetas viejas en cada corrida del
    scorer, y la ausencia dejaría de existir el día que alguien archive la sugerencia—, así
    que la ausencia se materializa una vez, en una fila, como cualquier otra señal.

    Corre antes de la generación de la mañana a propósito (ver `scheduler._SCHEDULE`): lo
    que se aprendió anoche reordena las tarjetas de hoy y no las de mañana.
    """
    logger.info("Running absence sweep job")
    db = SessionLocal()
    try:
        users = UserRepository(db).list_active()
        for user in users:
            try:
                written = _sweep_user(db, user)
                if written:
                    logger.info("Recorded %d absence signals for user %d", written, user.id)
            except Exception:
                db.rollback()
                logger.exception("Error sweeping absences for user %d", user.id)
    except Exception:
        logger.exception("Error in absence_sweep job")
    finally:
        db.close()


def _sweep_user(db: Session, user: User) -> int:
    """Escribir las ausencias de *user* y devolver cuántas. Un commit por persona.

    El commit va acá y no afuera para que la excepción de una persona no se lleve puesto lo
    que ya se aprendió de la otra — el mismo reparto que usa el job de generación, donde el
    `try` por usuario existe justamente para eso.
    """
    from app.recommendations import learning

    cutoff = datetime.now(tz=UTC) - timedelta(days=learning.ABSENCE_GRACE_DAYS)
    #: La ventana se cierra un día antes del corte porque el job corre una vez por día (ver
    #: `scheduler._SCHEDULE`): así cada tarjeta cumple edad dentro de exactamente un barrido.
    #: El ancho tiene que ser el del intervalo del job y no un número aparte — más angosto
    #: deja tarjetas sin barrer, más ancho las barre dos veces.
    stale = SuggestionRepository(db).get_stale_pending(
        user.id,
        subject_types=learning.ABSENCE_SUBJECT_TYPES,
        created_before=cutoff,
        created_after=cutoff - timedelta(days=_SWEEP_WINDOW_DAYS),
    )
    if not stale:
        return 0

    #: Se leen las señales desde la tarjeta más vieja que se va a barrer, y no las últimas
    #: N: hay que poder ver tanto el acto que desmiente la ausencia como la ausencia que ya
    #: se grabó por esta misma tarjeta, y las dos son posteriores a que la tarjeta naciera.
    since = min(as_utc(s.created_at) for s in stale)
    signals = BehaviorSignalRepository(db).get_user_signals(user.id, limit=None, since=since)

    #: Segunda línea de defensa, no la primera: lo que hace que una tarjeta se cuente una
    #: sola vez es la ventana de `get_stale_pending`. Esto cubre el caso que la ventana no
    #: puede cubrir —dos barridos el mismo día— y no cuesta una consulta, porque estas filas
    #: ya hay que traerlas para el chequeo de "¿lo hizo igual?". Deliberadamente **no** es el
    #: mecanismo principal: si lo fuera, borrar la señal desde el panel volvería a hacer la
    #: tarjeta barrible, que es justamente el bug que la ventana viene a cerrar.
    swept = {
        int(signal.source_entity_id)
        for signal in signals
        if signal.signal_type == learning.ABSENCE_SIGNAL_TYPE
        and signal.source_entity_type == "suggestion"
        and signal.source_entity_id is not None
    }

    #: Cuándo fue la última vez que la persona hizo algo con cada sujeto. Se compara en
    #: Python y no en SQL porque los dos nombres tienen origen distinto:
    #: `Suggestion.subject_name` guarda el nombre como se muestra ("brócoli") y
    #: `BehaviorSignal.entity_name` el normalizado ("brocoli"), así que el único match que
    #: no miente es el de `learning.subject_key`.
    last_act: dict[tuple[str, str], datetime] = {}
    for signal in signals:
        if signal.signal_type not in learning.CONSUMPTION_SIGNAL_TYPES:
            continue
        if signal.created_at is None:
            continue
        key = learning.subject_key(signal.entity_type, signal.entity_name)
        acted_at = as_utc(signal.created_at)
        if key not in last_act or acted_at > last_act[key]:
            last_act[key] = acted_at

    written = 0
    for suggestion in stale:
        if suggestion.subject_type is None or suggestion.subject_name is None:
            continue
        if suggestion.id in swept:
            continue
        #: "Lo hizo igual" cuenta desde que nació la tarjeta, no desde el corte: comer
        #: lentejas al día siguiente de que se sugirieran es exactamente lo contrario de una
        #: ausencia, aunque hayan pasado seis días más sin volver a comerlas.
        key = learning.subject_key(suggestion.subject_type, suggestion.subject_name)
        last = last_act.get(key)
        if last is not None and last >= as_utc(suggestion.created_at):
            continue
        #: El tipo va como literal y no como `learning.ABSENCE_SIGNAL_TYPE` a propósito: es
        #: la misma forma que usan los otros cuatro escritores, y es lo que hace que
        #: `test_every_signal_type_the_reader_knows_has_a_writer` pueda encontrarlo. Ese
        #: test es el único mecanismo que atrapa un tipo que se lee y nadie escribe — el
        #: bug de `repeated_purchase` y de `rejected_activity`— y solo mira literales.
        recorded = learning.record_signal(
            db,
            user_id=user.id,
            signal_type="unused_suggestion",
            subject_type=suggestion.subject_type,
            subject_name=suggestion.subject_name,
            value=learning.ABSENCE_VALUE,
            source_type="inferred",
            source_entity_type="suggestion",
            source_entity_id=suggestion.id,
        )
        #: `record_signal` devuelve `None` cuando el nombre normalizado queda vacío (un
        #: sujeto que era solo puntuación). Contar solo lo que volvió es lo que hace que el
        #: número del log sea la cantidad de filas y no la de intentos.
        if recorded is not None:
            written += 1

    db.commit()
    return written
