"""Los jobs de notificación vistos de punta a punta: qué escriben y qué se callan.

`tests/test_notifications.py` prueba el chequeo por sujeto contra el repositorio. Acá
se prueba lo que la persona ve: que la leche baja no genere un aviso por corrida, que
un ítem que se vació sí vuelva a hablar, y que una despensa recién cargada no se
convierta en una pared de notificaciones el primer día.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.jobs import notification_jobs
from app.models.body_metric import BodyMetricLog
from app.models.food import FoodItem
from app.models.household import Household
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


def _workout(
    db: Session, household: Household, user: User, *, days_ago: int
) -> WorkoutSession:
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
