"""Los jobs de notificación vistos de punta a punta: qué escriben y qué se callan.

`tests/test_notifications.py` prueba el chequeo por sujeto contra el repositorio. Acá
se prueba lo que la persona ve: que la leche baja no genere un aviso por corrida, que
un ítem que se vació sí vuelva a hablar, y que una despensa recién cargada no se
convierta en una pared de notificaciones el primer día.

Y la otra mitad de ese ciclo, que es la que agrega la 4.3: que el aviso **se vaya** cuando
su sujeto sale del conjunto, y que con eso la marca de agua de `priority` se reinicie en la
recuperación en vez de esperar a que venza la ventana de siete días.
"""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.jobs import notification_jobs
from app.models.body_metric import BodyMetricLog
from app.models.food import FoodItem
from app.models.household import Household
from app.models.meal import MealEvent, MealParticipant
from app.models.notification import Notification
from app.models.pantry import PantryStock
from app.models.user import User
from app.models.workout import WorkoutParticipant, WorkoutSession


@pytest.fixture(autouse=True)
def _jobs_can_speak(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    """Saca del medio las dos cosas que no se están probando acá.

    La franja de silencio tiene sus propios tests en `test_clock.py`, y el job cierra
    su sesión en el `finally` — que acá es la sesión del test, que todavía tiene que
    poder leer lo que se escribió.
    """
    monkeypatch.setattr(notification_jobs, "is_quiet_hours", lambda: False)
    monkeypatch.setattr(notification_jobs, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)


def _low_item(db: Session, household: Household, name: str, quantity: float) -> PantryStock:
    """Un ítem de despensa por debajo de su umbral de 10."""
    food = FoodItem(canonical_name=name, category="other", base_unit="unit")
    db.add(food)
    db.flush()
    stock = PantryStock(
        household_id=household.id,
        food_item_id=food.id,
        current_quantity=quantity,
        unit="unit",
        low_stock_threshold=10,
    )
    db.add(stock)
    db.flush()
    return stock


def _workout(db: Session, household: Household, user: User, *, days_ago: int) -> WorkoutSession:
    """Una sesión de entrenamiento con *user* como participante."""
    session = WorkoutSession(
        household_id=household.id,
        timestamp_start=datetime.now(UTC) - timedelta(days=days_ago),
        workout_type="gym",
    )
    db.add(session)
    db.flush()
    db.add(WorkoutParticipant(workout_session_id=session.id, user_id=user.id))
    db.flush()
    return session


def _meal(db: Session, household: Household, user: User, *, days_ago: int) -> MealEvent:
    """Una comida del hogar con *user* como participante."""
    event = MealEvent(
        household_id=household.id,
        timestamp=datetime.now(UTC) - timedelta(days=days_ago),
        meal_type="dinner",
    )
    db.add(event)
    db.flush()
    db.add(MealParticipant(meal_event_id=event.id, user_id=user.id))
    db.flush()
    return event


def _sleep_log(
    db: Session,
    user: User,
    *,
    days_ago: int,
    hours: float | None,
    weight: float | None = 70,
) -> BodyMetricLog:
    """Una medición corporal de *user*, con o sin horas de sueño y con o sin peso.

    Las dos columnas son opcionales y las dos ausencias las leen por separado, así que el
    helper tiene que poder escribir las cuatro combinaciones: sin eso no hay forma de
    escribir "anotó que durmió y no se pesó", que es un registro que la app produce sola.
    """
    log = BodyMetricLog(
        user_id=user.id,
        timestamp=datetime.now(UTC) - timedelta(days=days_ago),
        weight_kg=weight,
        sleep_hours=hours,
    )
    db.add(log)
    db.flush()
    return log


def _assert_no_silence_by_accident(caplog: pytest.LogCaptureFixture) -> None:
    """Que el job haya callado porque no tenía nada que decir, y no porque explotó.

    Cada job termina en `except Exception: logger.exception(...)`, así que un test cuya
    única afirmación es `== []` pasa igual con un typo en un helper de mensajes o con una
    firma de repositorio que cambió: cero notificaciones es también lo que deja una
    excepción en la primera línea. Los tests positivos no necesitan esto —una fila con el
    título correcto no la escribe un job roto—; los negativos sí.
    """
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors == [], f"el job falló en vez de callarse: {errors}"


def _notifications(db: Session, category: str) -> list[Notification]:
    return (
        db.query(Notification)
        .filter(Notification.category == category)
        .order_by(Notification.id)
        .all()
    )


class TestLowStockJob:
    def test_one_notification_per_item_and_not_one_more_tomorrow(
        self, db: Session, household: Household
    ) -> None:
        """El aviso de despensa dejó de ser un resumen que se repite.

        Era una sola notificación por hogar con "Running low on: leche, huevos" y un
        cooldown por categoría de 6 h contra un job que corre cada 24: la leche baja
        desde hace tres semanas producía un aviso por día, siempre el mismo, y sin
        nada que tocar. Ahora cada ítem es un sujeto, y un sujeto se anuncia una vez.
        """
        milk = _low_item(db, household, "milk", quantity=8)
        eggs = _low_item(db, household, "eggs", quantity=2)

        notification_jobs.run_low_stock_notifications()
        first_run = _notifications(db, "low_stock")

        assert {n.related_entity_id for n in first_run} == {milk.id, eggs.id}
        assert {n.related_entity_type for n in first_run} == {"pantry_stock"}
        #: Del hogar, no de una persona: la despensa es de los dos.
        assert {n.user_id for n in first_run} == {None}

        notification_jobs.run_low_stock_notifications()
        assert [n.id for n in _notifications(db, "low_stock")] == [n.id for n in first_run]

    def test_it_speaks_again_only_when_the_item_gets_emptier(
        self, db: Session, household: Household
    ) -> None:
        """Escalada: no repetir no es callarse para siempre."""
        milk = _low_item(db, household, "milk", quantity=8)

        notification_jobs.run_low_stock_notifications()
        assert [n.priority for n in _notifications(db, "low_stock")] == [7]

        #: Media botella: sigue bajo, pero no es una novedad.
        milk.current_quantity = 6
        db.flush()
        notification_jobs.run_low_stock_notifications()
        assert [n.priority for n in _notifications(db, "low_stock")] == [7]

        #: El escalón del medio: por debajo de la mitad del umbral ya no es "está
        #: bajando" sino "está por acabarse", y eso es novedad sin haber llegado a cero.
        milk.current_quantity = 4
        db.flush()
        notification_jobs.run_low_stock_notifications()
        assert [n.priority for n in _notifications(db, "low_stock")] == [7, 8]

        milk.current_quantity = 0
        db.flush()
        notification_jobs.run_low_stock_notifications()
        after = _notifications(db, "low_stock")
        assert [n.priority for n in after] == [7, 8, 9]
        assert after[-1].title == "Out of milk"

    def test_a_freshly_loaded_pantry_does_not_become_a_wall(
        self, db: Session, household: Household
    ) -> None:
        """El tope por corrida, y que drene.

        Un aviso por ítem es lo que permite escalar, pero sin tope la primera corrida
        contra una despensa recién cargada escribiría una notificación por faltante.
        """
        for i in range(7):
            _low_item(db, household, f"item-{i}", quantity=8)

        notification_jobs.run_low_stock_notifications()
        assert len(_notifications(db, "low_stock")) == notification_jobs._MAX_NEW_STOCK_ALERTS

        notification_jobs.run_low_stock_notifications()
        assert len(_notifications(db, "low_stock")) == 7

    def test_the_emptiest_items_go_first(
        self, monkeypatch: pytest.MonkeyPatch, db: Session, household: Household
    ) -> None:
        """El tope reparte por urgencia, y de forma determinista.

        Sin el orden explícito de `_most_urgent_first`, a quién le toca el aviso de hoy
        lo decidiría el orden en que el motor devolvió las filas (regla 5 de
        `AGENTS.md`), y los dos ítems que se acabaron podrían quedar para mañana.
        """
        #: Los ítems que se cargan primero son los que *no* tienen que salir: si el
        #: orden se ignorara, el tope se lo llevarían estos.
        for i in range(3):
            _low_item(db, household, f"low-{i}", quantity=8)
        empty = [_low_item(db, household, f"empty-{i}", quantity=0) for i in range(2)]

        monkeypatch.setattr(notification_jobs, "_MAX_NEW_STOCK_ALERTS", 2)
        notification_jobs.run_low_stock_notifications()

        chosen = {n.related_entity_id for n in _notifications(db, "low_stock")}
        assert chosen == {s.id for s in empty}


class TestMetricReminderJob:
    def test_the_reminder_escalates_with_the_gap(
        self, db: Session, household: Household, diego: User
    ) -> None:
        """Tres días sin pesarse y diez no son la misma noticia.

        Es el caso que el chequeo por categoría no podía expresar: el sujeto es la
        persona, y lo que cambia es cuánto hace.
        """
        log = BodyMetricLog(
            user_id=diego.id,
            timestamp=datetime.now(UTC) - timedelta(days=4),
            weight_kg=70,
        )
        db.add(log)
        db.flush()

        notification_jobs.run_metric_reminder_notifications()
        assert [n.priority for n in _notifications(db, "metric_reminder")] == [4]

        notification_jobs.run_metric_reminder_notifications()
        assert [n.priority for n in _notifications(db, "metric_reminder")] == [4]

        log.timestamp = datetime.now(UTC) - timedelta(days=10)
        db.flush()
        notification_jobs.run_metric_reminder_notifications()
        assert [n.priority for n in _notifications(db, "metric_reminder")] == [4, 6]

    def test_someone_who_never_weighed_in_is_not_told_a_number(
        self, db: Session, household: Household, diego: User
    ) -> None:
        """Sin última medición no hay antigüedad, y el texto no puede inventarla.

        El centinela `days_since = _REMINDER_DAYS` de la rama sin historial entraba en el
        cuerpo: a alguien que nunca se pesó — una cuenta creada ayer, por ejemplo — la app
        le decía "you haven't logged your weight in 3 days".
        """
        notification_jobs.run_metric_reminder_notifications()
        body = _notifications(db, "metric_reminder")[0].body

        assert "no weight logged yet" in body
        assert "3 days" not in body

    def test_a_recent_weigh_in_is_left_alone(
        self, db: Session, household: Household, diego: User
    ) -> None:
        db.add(
            BodyMetricLog(
                user_id=diego.id,
                timestamp=datetime.now(UTC) - timedelta(hours=6),
                weight_kg=70,
            )
        )
        db.flush()

        notification_jobs.run_metric_reminder_notifications()
        assert _notifications(db, "metric_reminder") == []

    def test_a_night_of_sleep_is_not_a_weigh_in(
        self, db: Session, household: Household, diego: User
    ) -> None:
        """`weight_kg` es opcional, así que hay filas de medición que no son pesajes.

        "Dormí 7 horas" escribe una `BodyMetricLog` con `weight_kg=NULL` — el parser acepta
        el intent con cualquiera de los cuatro campos —, y `get_latest_for_user` la
        devolvía como si fuera el último pesaje. Antes eso atrasaba el recordatorio; con el
        retiro por sujeto de la 4.3 **lo borra**, y con él la marca de agua: quien anota
        sueño cada dos días no volvía a recibir el aviso del peso nunca, y sin ruido.

        El camino es el que abre esta misma sub-fase: la acción del aviso de sueño lleva a
        la captura con "I slept " puesto.
        """
        notification_jobs.run_metric_reminder_notifications()
        assert len(_notifications(db, "metric_reminder")) == 1

        _sleep_log(db, diego, days_ago=0, hours=7, weight=None)
        notification_jobs.run_metric_reminder_notifications()

        assert len(_notifications(db, "metric_reminder")) == 1

    def test_each_member_gets_their_own(
        self, db: Session, household: Household, diego: User, rocio: User
    ) -> None:
        """Ninguno de los dos tapa al otro.

        Los avisos per-user llevan también `household_id`, y ahí estaba el `or_` que
        hacía que el de Diego contara como el de Rocío.
        """
        notification_jobs.run_metric_reminder_notifications()
        assert {n.user_id for n in _notifications(db, "metric_reminder")} == {
            diego.id,
            rocio.id,
        }


class TestInactivityJob:
    def test_someone_who_never_logged_a_workout_is_told_once(
        self, db: Session, household: Household, diego: User
    ) -> None:
        """Sin antigüedad que medir, el aviso no escala: se dice una vez.

        Y no dice cuántos días: el centinela de esa rama entraba en el título, así que a
        alguien que nunca entrenó la app le anunciaba "No workouts logged in 4 days".
        """
        notification_jobs.run_inactivity_notifications()
        first = _notifications(db, "inactivity")
        assert [n.user_id for n in first] == [diego.id]
        assert first[0].related_entity_type == "user"
        assert first[0].title == "No workouts logged yet"
        assert "4 days" not in first[0].body

        notification_jobs.run_inactivity_notifications()
        assert [n.id for n in _notifications(db, "inactivity")] == [first[0].id]

    def test_a_recent_workout_is_left_alone_and_a_stale_one_escalates(
        self, db: Session, household: Household, diego: User
    ) -> None:
        """La rama que sí mide, que es la que usa `get_last_session_start`.

        El recordatorio de pesaje cubre la lógica análoga, así que la escalera parece
        probada cuando en realidad acá solo se ejercitaba el caso "nunca entrenó" — y con
        él ni la consulta nueva, ni el corte por debajo del umbral, ni el escalón.
        """
        session = _workout(db, household, diego, days_ago=2)
        notification_jobs.run_inactivity_notifications()
        assert _notifications(db, "inactivity") == []

        session.timestamp_start = datetime.now(UTC) - timedelta(days=5)
        db.flush()
        notification_jobs.run_inactivity_notifications()
        told = _notifications(db, "inactivity")
        assert [n.priority for n in told] == [5]
        assert told[0].title == "No workouts logged in 5 days"

        #: Nueve días son dos tandas de cuatro: empeoró, así que se vuelve a hablar.
        session.timestamp_start = datetime.now(UTC) - timedelta(days=9)
        db.flush()
        notification_jobs.run_inactivity_notifications()
        assert [n.priority for n in _notifications(db, "inactivity")] == [5, 6]

    def test_one_members_gap_is_not_the_others(
        self, db: Session, household: Household, diego: User, rocio: User
    ) -> None:
        """La sesión es de quien participó, no del hogar.

        `get_last_session_start` filtra por hogar **y** por participante: sin la segunda
        mitad, el entrenamiento de Diego contaría como el de Rocío y ella no recibiría el
        aviso que le corresponde.
        """
        _workout(db, household, diego, days_ago=1)
        notification_jobs.run_inactivity_notifications()

        assert [n.user_id for n in _notifications(db, "inactivity")] == [rocio.id]


class TestMealReminderJob:
    """Comer es lo más frecuente de las cuatro cosas, así que dos días sin registro ya
    es un hueco: no es que no comieron, es que la app dejó de saber qué comen."""

    def test_the_gap_is_measured_against_the_last_logged_meal(
        self, db: Session, household: Household, diego: User
    ) -> None:
        meal = _meal(db, household, diego, days_ago=1)
        notification_jobs.run_meal_reminder_notifications()
        assert _notifications(db, "meal_reminder") == []

        meal.timestamp = datetime.now(UTC) - timedelta(days=3)
        db.flush()
        notification_jobs.run_meal_reminder_notifications()
        told = _notifications(db, "meal_reminder")
        assert [(n.user_id, n.priority) for n in told] == [(diego.id, 4)]
        assert told[0].title == "No meals logged in 3 days"
        assert told[0].related_entity_type == "user"
        assert told[0].related_entity_id == diego.id

        #: Seis días son tres tandas de dos: empeoró, así que se vuelve a hablar.
        meal.timestamp = datetime.now(UTC) - timedelta(days=6)
        db.flush()
        notification_jobs.run_meal_reminder_notifications()
        assert [n.priority for n in _notifications(db, "meal_reminder")] == [4, 6]

    def test_someone_who_never_logged_a_meal_is_left_alone(
        self, db: Session, household: Household, diego: User, caplog: pytest.LogCaptureFixture
    ) -> None:
        """La diferencia deliberada con los dos recordatorios viejos.

        Al que nunca entrenó la app le dice algo; al que nunca anotó una comida, no. Una
        notificación señala un agujero en una costumbre, y proponer una costumbre que nadie
        tiene es tarea del motor de sugerencias: el día uno de la app no es una pared de
        retos. Sin este test, poner un `first_message` "por simetría" pasa la suite.
        """
        notification_jobs.run_meal_reminder_notifications()
        assert _notifications(db, "meal_reminder") == []
        _assert_no_silence_by_accident(caplog)

    def test_one_members_meals_are_not_the_others(
        self, db: Session, household: Household, diego: User, rocio: User
    ) -> None:
        """La comida es del hogar y el participante es quien la comió.

        Sin la mitad del participante en `get_last_meal_at`, la cena que Diego anotó solo
        contaría como registro de Rocío y el hueco de ella quedaría tapado. Los dos tienen
        un registro viejo para que ninguno caiga en la rama del "nunca anotó nada", que se
        calla y haría pasar el test por el motivo equivocado.
        """
        _meal(db, household, diego, days_ago=1)
        _meal(db, household, rocio, days_ago=5)

        notification_jobs.run_meal_reminder_notifications()
        assert [n.user_id for n in _notifications(db, "meal_reminder")] == [rocio.id]


class TestSleepReminderJob:
    def test_the_gap_is_measured_against_the_last_night_with_hours(
        self, db: Session, household: Household, diego: User
    ) -> None:
        """`sleep_hours` es una columna opcional de la fila del peso.

        Si el job leyera la última medición, alguien que se pesa todos los días y no anota
        sueño desde marzo nunca recibiría el aviso: la fila de hoy existe, y con
        `sleep_hours` en `NULL`. Es exactamente lo que arma este test — un pesaje de ayer
        sin sueño, y el último sueño hace cinco días.
        """
        _sleep_log(db, diego, days_ago=5, hours=7.5)
        _sleep_log(db, diego, days_ago=1, hours=None)

        notification_jobs.run_sleep_reminder_notifications()
        told = _notifications(db, "sleep_reminder")
        assert [(n.user_id, n.priority) for n in told] == [(diego.id, 3)]
        assert told[0].title == "No sleep logged in 5 days"

    def test_a_recent_night_is_left_alone(
        self, db: Session, household: Household, diego: User, caplog: pytest.LogCaptureFixture
    ) -> None:
        _sleep_log(db, diego, days_ago=1, hours=8)

        notification_jobs.run_sleep_reminder_notifications()
        assert _notifications(db, "sleep_reminder") == []
        _assert_no_silence_by_accident(caplog)

    def test_someone_who_never_logged_sleep_is_left_alone(
        self, db: Session, household: Household, diego: User, caplog: pytest.LogCaptureFixture
    ) -> None:
        notification_jobs.run_sleep_reminder_notifications()
        assert _notifications(db, "sleep_reminder") == []
        _assert_no_silence_by_accident(caplog)


class TestSubjectRetirement:
    """Que el aviso se vaya cuando su sujeto sale del conjunto.

    Es lo que le da sentido a la acción primaria de la 4.3 —tocar el aviso, hacer la cosa,
    y que el aviso se vaya— y lo que reinicia la marca de agua de `priority` en la
    recuperación en vez de esperar a que venza la ventana de siete días.
    """

    def test_logging_the_thing_removes_the_open_reminder(
        self, db: Session, household: Household, diego: User
    ) -> None:
        notification_jobs.run_metric_reminder_notifications()
        assert len(_notifications(db, "metric_reminder")) == 1

        #: Se pesó. El sujeto salió del conjunto.
        db.add(BodyMetricLog(user_id=diego.id, timestamp=datetime.now(UTC), weight_kg=70))
        db.flush()
        notification_jobs.run_metric_reminder_notifications()

        assert _notifications(db, "metric_reminder") == []

    def test_restocking_one_item_leaves_the_other_notice_alone(
        self, db: Session, household: Household
    ) -> None:
        """La despensa se retira por complemento: la lista de faltantes de hoy **es** la
        verdad, así que se borra todo `low_stock` cuyo sujeto no esté en ella.

        Y el complemento tiene que ser un complemento y no un `DELETE` de la categoría:
        reponer la leche no puede llevarse el aviso de los huevos.
        """
        milk = _low_item(db, household, "milk", quantity=8)
        eggs = _low_item(db, household, "eggs", quantity=2)
        notification_jobs.run_low_stock_notifications()
        assert len(_notifications(db, "low_stock")) == 2

        milk.current_quantity = 50
        db.flush()
        notification_jobs.run_low_stock_notifications()

        assert [n.related_entity_id for n in _notifications(db, "low_stock")] == [eggs.id]

    def test_the_escalation_watermark_resets_on_recovery(
        self, db: Session, household: Household, diego: User
    ) -> None:
        """El motivo por el que el retiro es un `DELETE` y no una marca.

        Sin el retiro, alguien que estuvo diez días sin entrenar queda con la marca de agua
        en 6, y cuando vuelve a caer en cuatro días la app se queda muda: la severidad 5 no
        supera al 6 que ya se dijo, y el silencio dura hasta que venza la ventana. Con el
        retiro, volver a entrenar limpia el sujeto y el próximo hueco se anuncia de nuevo
        desde el escalón base.
        """
        session = _workout(db, household, diego, days_ago=9)
        notification_jobs.run_inactivity_notifications()
        assert [n.priority for n in _notifications(db, "inactivity")] == [6]

        session.timestamp_start = datetime.now(UTC) - timedelta(days=1)
        db.flush()
        notification_jobs.run_inactivity_notifications()
        assert _notifications(db, "inactivity") == []

        session.timestamp_start = datetime.now(UTC) - timedelta(days=5)
        db.flush()
        notification_jobs.run_inactivity_notifications()
        assert [n.priority for n in _notifications(db, "inactivity")] == [5]

    def test_it_retires_a_notice_that_was_already_read_and_dismissed(
        self, db: Session, household: Household, diego: User
    ) -> None:
        """El retiro no mira `read_at` ni `dismissed_at`, y es a propósito.

        `has_recent_for_subject` tampoco los mira —descartar es "lo vi", no "resolvelo"—,
        así que si el retiro respetara el descarte, descartar el aviso de la leche y después
        reponerla dejaría la marca de agua en 9: la próxima vez que se acabe, silencio.
        """
        notification_jobs.run_metric_reminder_notifications()
        told = _notifications(db, "metric_reminder")[0]
        told.read_at = datetime.now(UTC)
        told.dismissed_at = datetime.now(UTC)
        db.flush()

        db.add(BodyMetricLog(user_id=diego.id, timestamp=datetime.now(UTC), weight_kg=70))
        db.flush()
        notification_jobs.run_metric_reminder_notifications()

        assert _notifications(db, "metric_reminder") == []

    def test_a_retire_only_pass_commits(
        self, monkeypatch: pytest.MonkeyPatch, db: Session, household: Household, diego: User
    ) -> None:
        """La misma trampa que la poda, y por el mismo motivo.

        `retire_subject` hace `flush()`, no `commit()`, y el único que commitea es
        `NotificationService.create` — que en una corrida donde todos se recuperaron no se
        llama ni una vez. Contar filas después no distingue: el `flush()` ya saca la fila de
        la vista de esta sesión, así que sacarle el `db.commit()` al driver deja la suite
        verde y el borrado se va con el `close()` del `finally`.
        """
        notification_jobs.run_metric_reminder_notifications()
        db.add(BodyMetricLog(user_id=diego.id, timestamp=datetime.now(UTC), weight_kg=70))
        db.flush()

        commits: list[None] = []
        real_commit = db.commit
        monkeypatch.setattr(db, "commit", lambda: (commits.append(None), real_commit())[0])

        notification_jobs.run_metric_reminder_notifications()

        assert _notifications(db, "metric_reminder") == []
        assert commits, "el retiro no commiteó: el borrado se iría con el `close()`"

    def test_it_does_not_touch_what_a_person_wrote(
        self, db: Session, household: Household, diego: User
    ) -> None:
        """El retiro filtra por `source_type == "job"`.

        Las notificaciones de la v1 y cualquier cosa que un día escriba otro camino no son
        del job, y el job no es quien decide que caducaron.
        """
        db.add(
            Notification(
                user_id=diego.id,
                household_id=household.id,
                category="metric_reminder",
                title="Written by hand",
                body="body",
                source_type="manual",
                related_entity_type="user",
                related_entity_id=diego.id,
            )
        )
        db.add(BodyMetricLog(user_id=diego.id, timestamp=datetime.now(UTC), weight_kg=70))
        db.flush()

        notification_jobs.run_metric_reminder_notifications()

        assert [n.title for n in _notifications(db, "metric_reminder")] == ["Written by hand"]


class TestPruningJob:
    def _ancient(self, db: Session, household: Household, diego: User) -> None:
        db.add(
            Notification(
                user_id=diego.id,
                household_id=household.id,
                category="info",
                title="Ancient",
                body="body",
                created_at=datetime.now(UTC) - timedelta(days=200),
            )
        )
        db.flush()

    def test_it_commits_what_it_removes(
        self, monkeypatch: pytest.MonkeyPatch, db: Session, household: Household, diego: User
    ) -> None:
        """La poda escribe, así que tiene que cerrar la transacción.

        Contar filas después no distingue commitear de no commitear: el `flush()` del
        repositorio ya saca la fila de la vista de *esta* sesión, así que sacarle el
        `db.commit()` al job deja la suite verde y el borrado se va con el `close()` del
        `finally`. Lo que se observa entonces es el commit.
        """
        self._ancient(db, household, diego)
        commits: list[None] = []
        real_commit = db.commit
        monkeypatch.setattr(db, "commit", lambda: (commits.append(None), real_commit())[0])

        notification_jobs.run_notification_pruning()

        assert db.query(Notification).count() == 0
        assert commits, "la poda no commiteó: el borrado se iría con el `close()`"

    def test_pruning_runs_inside_the_quiet_window(
        self, monkeypatch: pytest.MonkeyPatch, db: Session, household: Household, diego: User
    ) -> None:
        """Es a propósito, y sin esto nada lo sostiene.

        La poda no le habla a nadie, y su horario — 04:15 — está adentro de la franja de
        silencio de 22 a 8. Agregarle el gate `_muted` que tienen los otros tres jobs la
        apagaría **para siempre**, y la suite entera seguiría verde.
        """
        self._ancient(db, household, diego)
        monkeypatch.setattr(notification_jobs, "is_quiet_hours", lambda: True)

        notification_jobs.run_notification_pruning()

        assert db.query(Notification).count() == 0
