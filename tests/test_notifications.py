"""Tests for notification creation and management."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.household import Household
from app.models.notification import Notification
from app.models.user import User
from app.repositories.notification_repo import NotificationRepository
from app.schemas.notification import NotificationCreate
from app.services.notification_service import NotificationService


class TestNotificationService:
    def test_create_household_notification(self, db: Session, household: Household) -> None:
        svc = NotificationService(db)
        n = svc.create(
            NotificationCreate(
                household_id=household.id,
                category="low_stock",
                title="Low stock alert",
                body="You're running low on eggs.",
                priority=7,
            )
        )
        assert n.id is not None
        assert n.category == "low_stock"
        assert not n.is_read
        assert not n.is_dismissed

    def test_create_user_notification(self, db: Session, household: Household, diego: User) -> None:
        svc = NotificationService(db)
        n = svc.create(
            NotificationCreate(
                user_id=diego.id,
                household_id=household.id,
                category="inactivity",
                title="No workouts in 4 days",
                body="Hey Diego, time to move!",
            )
        )
        assert n.user_id == diego.id

    def test_mark_read(self, db: Session, household: Household, diego: User) -> None:
        svc = NotificationService(db)
        n = svc.create(
            NotificationCreate(
                user_id=diego.id,
                household_id=household.id,
                category="info",
                title="Test",
                body="Body",
            )
        )
        assert not n.is_read
        svc.mark_read(n.id, diego.id, household.id)
        db.refresh(n)
        assert n.is_read

    def test_dismiss(self, db: Session, household: Household, diego: User) -> None:
        svc = NotificationService(db)
        n = svc.create(
            NotificationCreate(
                user_id=diego.id,
                household_id=household.id,
                category="info",
                title="Test",
                body="Body",
            )
        )
        svc.dismiss(n.id, diego.id, household.id)
        db.refresh(n)
        assert n.is_dismissed

    def test_unread_count(
        self, db: Session, household: Household, diego: User, rocio: User
    ) -> None:
        svc = NotificationService(db)
        # Create 2 for diego
        svc.create(
            NotificationCreate(
                user_id=diego.id, household_id=household.id, category="info", title="A", body="B"
            )
        )
        svc.create(
            NotificationCreate(
                user_id=diego.id, household_id=household.id, category="info", title="C", body="D"
            )
        )
        # Household-level (should be visible to both)
        svc.create(
            NotificationCreate(
                household_id=household.id, category="low_stock", title="Low", body="Low on eggs"
            )
        )

        count = svc.get_unread_count(diego.id, household.id)
        assert count >= 2

    def test_mark_all_read(self, db: Session, household: Household, diego: User) -> None:
        svc = NotificationService(db)
        for i in range(3):
            svc.create(
                NotificationCreate(
                    user_id=diego.id,
                    household_id=household.id,
                    category="info",
                    title=f"Notif {i}",
                    body="body",
                )
            )
        marked = svc.mark_all_read(diego.id, household.id)
        assert marked >= 3
        assert svc.get_unread_count(diego.id, household.id) == 0

    def test_notifications_scoped_to_household(
        self, db: Session, household: Household, diego: User
    ) -> None:
        """Household-level notifications should appear for all household members."""
        svc = NotificationService(db)
        svc.create(
            NotificationCreate(
                household_id=household.id,
                category="low_stock",
                title="Shared alert",
                body="Pantry needs attention",
            )
        )
        # Should appear for Diego (member of household)
        notifs = svc.get_for_user(diego.id, household.id)
        assert any(n.title == "Shared alert" for n in notifs)


class TestVisibility:
    """Lo que cada uno ve: lo suyo, más lo que es del hogar. Nada del otro.

    Las cuatro consultas de lectura usaban `or_(user_id == yo, household_id == mío)`, y
    un aviso dirigido a una persona lleva **también** `household_id`: la segunda mitad lo
    hacía visible para el otro. Es la misma regla 4 que la 4.2 cerró del lado de la
    deduplicación, del lado de la lectura.
    """

    def _for(self, db: Session, household: Household, user: User, title: str) -> None:
        NotificationService(db).create(
            NotificationCreate(
                user_id=user.id,
                household_id=household.id,
                category="metric_reminder",
                title=title,
                body="body",
            )
        )

    def test_a_notification_for_one_member_is_invisible_to_the_other(
        self, db: Session, household: Household, diego: User, rocio: User
    ) -> None:
        """Y era visible con el cuerpo entero, que desde la 4.2 dice cuántos días lleva
        sin pesarse."""
        self._for(db, household, rocio, "Rocío's reminder")
        svc = NotificationService(db)

        assert svc.get_unread_count(diego.id, household.id) == 0
        assert svc.get_for_user(diego.id, household.id) == []
        assert svc.get_category_counts(diego.id, household.id) == {}

    def test_marking_all_read_does_not_touch_the_other_members_rows(
        self, db: Session, household: Household, diego: User, rocio: User
    ) -> None:
        """El síntoma más raro del `or_`: Diego podía marcar como leído un aviso que
        `_get_owned` no lo dejaba descartar, así que lo veía y no podía sacárselo."""
        self._for(db, household, rocio, "Rocío's reminder")

        assert NotificationService(db).mark_all_read(diego.id, household.id) == 0
        assert db.query(Notification).one().read_at is None

    def test_a_household_notification_reaches_both(
        self, db: Session, household: Household, diego: User, rocio: User
    ) -> None:
        """El control negativo: la mitad del `or_` que sí tenía que estar.

        Sin esto, filtrar solo por `user_id` pasaría los dos tests de arriba y dejaría el
        aviso de la despensa — que no es de nadie en particular — sin llegar a nadie.
        """
        NotificationService(db).create(
            NotificationCreate(
                household_id=household.id,
                category="low_stock",
                title="Out of milk",
                body="body",
            )
        )
        svc = NotificationService(db)

        assert svc.get_unread_count(diego.id, household.id) == 1
        assert svc.get_unread_count(rocio.id, household.id) == 1


class TestSubjectDedup:
    """`has_recent_for_subject` decide si un job se calla.

    Lo que reemplaza: un cooldown por **categoría**, que no distinguía la leche del
    café y cuya ventana quedó más corta que el período de su propio job.
    """

    def _notify(
        self,
        db: Session,
        household: Household,
        *,
        category: str = "low_stock",
        entity_type: str = "pantry_stock",
        entity_id: int = 1,
        priority: int = 7,
        user_id: int | None = None,
    ) -> None:
        NotificationService(db).create(
            NotificationCreate(
                user_id=user_id,
                household_id=household.id,
                category=category,
                title="title",
                body="body",
                priority=priority,
                related_entity_type=entity_type,
                related_entity_id=entity_id,
            )
        )

    def _asked(
        self,
        db: Session,
        household: Household,
        *,
        category: str = "low_stock",
        entity_type: str = "pantry_stock",
        entity_id: int = 1,
        severity: int = 7,
        user_id: int | None = None,
    ) -> bool:
        return NotificationRepository(db).has_recent_for_subject(
            category,
            entity_type,
            entity_id,
            household_id=household.id,
            user_id=user_id,
            days=7,
            severity=severity,
        )

    def test_the_same_subject_is_not_announced_twice(
        self, db: Session, household: Household
    ) -> None:
        assert not self._asked(db, household)
        self._notify(db, household)
        assert self._asked(db, household)

    def test_another_subject_of_the_same_category_still_gets_through(
        self, db: Session, household: Household
    ) -> None:
        """El bug que motivó todo esto, del otro lado.

        Con el cooldown por categoría, avisar de la leche silenciaba el café: el
        único aviso de despensa del día ya estaba gastado.
        """
        self._notify(db, household, entity_id=1)
        assert not self._asked(db, household, entity_id=2)

    def test_it_speaks_again_when_the_subject_gets_worse(
        self, db: Session, household: Household
    ) -> None:
        """Escalada: media botella de leche calla, la botella vacía no."""
        self._notify(db, household, priority=7)
        assert self._asked(db, household, severity=7)
        assert not self._asked(db, household, severity=8)

    def test_getting_better_does_not_earn_a_new_notification(
        self, db: Session, household: Household
    ) -> None:
        """La escalada es en un solo sentido.

        Si el estado mejora — se repuso algo pero sigue bajo el umbral — la
        severidad baja, y eso no es novedad: ya se avisó de algo peor.
        """
        self._notify(db, household, priority=9)
        assert self._asked(db, household, severity=7)

    def test_the_window_expires(self, db: Session, household: Household) -> None:
        """Un faltante que sigue ahí a la semana siguiente vuelve a aparecer.

        Sin esto el silencio sería definitivo: la fila vieja del mismo sujeto y la
        misma severidad taparía el aviso para siempre.
        """
        self._notify(db, household)
        old = db.query(Notification).one()
        old.created_at = datetime.now(UTC) - timedelta(days=9)
        db.flush()

        assert not self._asked(db, household)

    def test_dismissing_it_does_not_bring_it_back_tomorrow(
        self, db: Session, household: Household, diego: User
    ) -> None:
        """Descartar es "lo vi", no "avisame de nuevo"."""
        self._notify(db, household)
        n = db.query(Notification).one()
        NotificationService(db).dismiss(n.id, diego.id, household.id)

        assert self._asked(db, household)

    def test_two_categories_about_the_same_person_are_two_subjects(
        self, db: Session, household: Household, diego: User
    ) -> None:
        """La categoría es parte de la clave.

        El sujeto de la inactividad y el del recordatorio de pesaje son los dos la
        misma persona: sin la categoría en la clave, avisarle de una le taparía la
        otra.
        """
        self._notify(
            db,
            household,
            category="inactivity",
            entity_type="user",
            entity_id=diego.id,
            priority=5,
            user_id=diego.id,
        )
        assert self._asked(
            db,
            household,
            category="inactivity",
            entity_type="user",
            entity_id=diego.id,
            severity=5,
            user_id=diego.id,
        )
        assert not self._asked(
            db,
            household,
            category="metric_reminder",
            entity_type="user",
            entity_id=diego.id,
            severity=4,
            user_id=diego.id,
        )

    def test_one_members_notification_does_not_suppress_the_other(
        self, db: Session, household: Household, diego: User, rocio: User
    ) -> None:
        """Per-user cooldowns are per user.

        Per-user reminders also carry ``household_id``, so an ``or_``-ed
        household clause made Diego's reminder count as Rocío's.
        """
        self._notify(
            db,
            household,
            category="inactivity",
            entity_type="user",
            entity_id=diego.id,
            priority=5,
            user_id=diego.id,
        )
        assert self._asked(
            db,
            household,
            category="inactivity",
            entity_type="user",
            entity_id=diego.id,
            severity=5,
            user_id=diego.id,
        )
        assert not self._asked(
            db,
            household,
            category="inactivity",
            entity_type="user",
            entity_id=rocio.id,
            severity=5,
            user_id=rocio.id,
        )

    def test_a_household_check_ignores_per_user_rows(
        self, db: Session, household: Household, diego: User
    ) -> None:
        """Un aviso dirigido a una persona no es un aviso del hogar."""
        self._notify(db, household, user_id=diego.id)
        assert not self._asked(db, household)


class TestRetiringASubject:
    """La otra mitad del ciclo por sujeto: sacar el aviso cuando la cosa se arregló.

    `TestSubjectDedup` cubre el "no repetir". Acá se cubre el "retirar", que es lo que
    reinicia la marca de agua de `priority`: mientras la fila esté, `has_recent_for_subject`
    la ve — leída, descartada, da igual — y la app se queda muda en la próxima recaída.
    """

    def _notify(
        self,
        db: Session,
        household: Household,
        *,
        category: str = "low_stock",
        entity_type: str | None = "pantry_stock",
        entity_id: int | None = 1,
        user_id: int | None = None,
        source_type: str = "job",
    ) -> int:
        NotificationService(db).create(
            NotificationCreate(
                user_id=user_id,
                household_id=household.id,
                category=category,
                title="title",
                body="body",
                priority=7,
                source_type=source_type,
                related_entity_type=entity_type,
                related_entity_id=entity_id,
            )
        )
        return db.query(Notification).count()

    def _surviving(self, db: Session) -> set[tuple[str, int | None]]:
        return {(n.category, n.related_entity_id) for n in db.query(Notification).all()}

    def test_it_removes_the_row_and_with_it_the_watermark(
        self, db: Session, household: Household, diego: User
    ) -> None:
        """El borrado es el mecanismo, no un efecto: es lo que hace que la próxima vez
        que el sujeto vuelva a estar mal, el aviso vuelva a salir desde el escalón base."""
        repo = NotificationRepository(db)
        self._notify(
            db,
            household,
            category="inactivity",
            entity_type="user",
            entity_id=diego.id,
            user_id=diego.id,
        )
        assert repo.has_recent_for_subject(
            "inactivity",
            "user",
            diego.id,
            household_id=household.id,
            user_id=diego.id,
            days=7,
            severity=5,
        )

        assert (
            repo.retire_subject(
                "inactivity", "user", diego.id, household_id=household.id, user_id=diego.id
            )
            == 1
        )

        assert not repo.has_recent_for_subject(
            "inactivity",
            "user",
            diego.id,
            household_id=household.id,
            user_id=diego.id,
            days=7,
            severity=5,
        )

    def test_it_only_takes_the_subject_it_was_given(
        self, db: Session, household: Household
    ) -> None:
        self._notify(db, household, entity_id=1)
        self._notify(db, household, entity_id=2)

        assert (
            NotificationRepository(db).retire_subject(
                "low_stock", "pantry_stock", 1, household_id=household.id
            )
            == 1
        )
        assert self._surviving(db) == {("low_stock", 2)}

    def test_retiring_someone_elses_subject_is_not_retiring_theirs(
        self, db: Session, household: Household, diego: User, rocio: User
    ) -> None:
        """Regla 4 en el borrado, que es donde más caro sale.

        Los avisos per-user llevan también `household_id`, así que un scope con `or_` —el
        mismo bug que tenía `has_recent_for_subject`— haría que el pesaje de Diego borre el
        de Rocío: ella pierde su recordatorio **y** su watermark sin haberse pesado.
        """
        for user in (diego, rocio):
            self._notify(
                db,
                household,
                category="metric_reminder",
                entity_type="user",
                entity_id=user.id,
                user_id=user.id,
            )

        assert (
            NotificationRepository(db).retire_subject(
                "metric_reminder", "user", diego.id, household_id=household.id, user_id=diego.id
            )
            == 1
        )
        assert self._surviving(db) == {("metric_reminder", rocio.id)}

    def test_a_household_retirement_does_not_reach_a_per_user_row(
        self, db: Session, household: Household, diego: User
    ) -> None:
        """El otro lado del scope: el aviso de la despensa es del hogar (`user_id IS
        NULL`), y un `DELETE` de hogar no tiene por qué llevarse una fila dirigida."""
        self._notify(db, household, user_id=diego.id)

        assert (
            NotificationRepository(db).retire_subject(
                "low_stock", "pantry_stock", 1, household_id=household.id
            )
            == 0
        )
        assert db.query(Notification).count() == 1

    def test_it_leaves_alone_what_a_job_did_not_write(
        self, db: Session, household: Household
    ) -> None:
        self._notify(db, household, source_type="manual")

        assert (
            NotificationRepository(db).retire_subject(
                "low_stock", "pantry_stock", 1, household_id=household.id
            )
            == 0
        )
        assert db.query(Notification).count() == 1

    def test_the_complement_keeps_what_is_still_missing(
        self, db: Session, household: Household
    ) -> None:
        """La forma de la despensa: la lista de faltantes de hoy **es** la verdad."""
        for entity_id in (1, 2, 3):
            self._notify(db, household, entity_id=entity_id)

        assert (
            NotificationRepository(db).retire_subjects_other_than(
                "low_stock", "pantry_stock", [2, 3], household_id=household.id
            )
            == 1
        )
        assert self._surviving(db) == {("low_stock", 2), ("low_stock", 3)}

    def test_an_empty_set_retires_everything(self, db: Session, household: Household) -> None:
        """Despensa entera repuesta. Sin este caso, un `if keep_ids:` de más arriba —o un
        `notin_([])`, que en SQL no matchea nada— dejaría los avisos puestos justo cuando
        ya no falta nada."""
        for entity_id in (1, 2):
            self._notify(db, household, entity_id=entity_id)

        assert (
            NotificationRepository(db).retire_subjects_other_than(
                "low_stock", "pantry_stock", [], household_id=household.id
            )
            == 2
        )
        assert db.query(Notification).count() == 0

    def test_the_complement_does_not_touch_a_row_without_a_subject(
        self, db: Session, household: Household
    ) -> None:
        """Las filas de la v1 no grababan `related_entity_*`, y sin id no hay forma de
        saber de qué ítem hablaban: no se puede decidir que se repuso. Se van con la poda."""
        self._notify(db, household, entity_type=None, entity_id=None)

        assert (
            NotificationRepository(db).retire_subjects_other_than(
                "low_stock", "pantry_stock", [], household_id=household.id
            )
            == 0
        )
        assert db.query(Notification).count() == 1

    def test_the_complement_does_not_cross_categories(
        self, db: Session, household: Household, diego: User
    ) -> None:
        self._notify(db, household, entity_id=1)
        self._notify(
            db,
            household,
            category="inactivity",
            entity_type="user",
            entity_id=diego.id,
            user_id=diego.id,
        )

        assert (
            NotificationRepository(db).retire_subjects_other_than(
                "low_stock", "pantry_stock", [], household_id=household.id
            )
            == 1
        )
        assert self._surviving(db) == {("inactivity", diego.id)}

    def test_retiring_nothing_is_not_an_error(self, db: Session, household: Household) -> None:
        repo = NotificationRepository(db)
        assert repo.retire_subject("low_stock", "pantry_stock", 99, household_id=household.id) == 0
        assert (
            repo.retire_subjects_other_than(
                "low_stock", "pantry_stock", [], household_id=household.id
            )
            == 0
        )


class TestPruning:
    def test_it_drops_the_old_and_keeps_the_recent(
        self, db: Session, household: Household, diego: User
    ) -> None:
        """Nada las borraba: la tabla crecía sin techo debajo de dos consultas que
        corren en cada carga de página."""
        svc = NotificationService(db)
        for i in range(3):
            svc.create(
                NotificationCreate(
                    user_id=diego.id,
                    household_id=household.id,
                    category="info",
                    title=f"Notif {i}",
                    body="body",
                )
            )
        stale, fresh = db.query(Notification).order_by(Notification.id).all()[:2]
        stale.created_at = datetime.now(UTC) - timedelta(days=120)
        db.flush()
        #: Los ids se leen antes de la poda: el `DELETE` masivo marca la instancia
        #: como borrada en la sesión, y leerle un atributo después es preguntarle por
        #: una fila que ya no está.
        stale_id, fresh_id = stale.id, fresh.id

        assert NotificationRepository(db).prune_older_than(90) == 1
        surviving = {n.id for n in db.query(Notification).all()}
        assert stale_id not in surviving
        assert fresh_id in surviving

    def test_pruning_an_empty_window_removes_nothing(
        self, db: Session, household: Household, diego: User
    ) -> None:
        NotificationService(db).create(
            NotificationCreate(
                user_id=diego.id,
                household_id=household.id,
                category="info",
                title="Recent",
                body="body",
            )
        )
        assert NotificationRepository(db).prune_older_than(90) == 0
        assert db.query(Notification).count() == 1
