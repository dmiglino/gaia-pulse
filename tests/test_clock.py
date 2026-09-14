"""El reloj del hogar: hora local, franja de silencio y el calendario de los jobs.

Lo que estos tests fijan es lo que antes no existía: que "ahora" y "hoy" salgan de
la timezone configurada, que la app tenga horas en las que no habla, y que la hora
a la que corre cada job **no dependa de cuándo arrancó el proceso**.
"""

import ast
import importlib
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from app.core.clock import (
    as_utc,
    household_tz,
    is_quiet_hours,
    local_day_bounds,
    local_now,
    local_today,
    to_local,
)
from app.core.config import get_settings
from app.jobs.scheduler import _SCHEDULE, _trigger
from app.models.household import Household
from app.models.notification import Notification
from app.models.user import User
from app.schemas.meal import MealEventCreate, MealParticipantCreate
from app.services.meal_service import MealService

#: La zona con la que están escritas las horas de acá: UTC-3 todo el año, sin DST.
_ZONE = "America/Argentina/Buenos_Aires"


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fija el reloj del hogar en vez de heredarlo del entorno.

    Todos los tests de acá miden la diferencia entre local y UTC, y varios tienen
    la hora escrita a mano (`to_local(12:00).hour == 9`, la franja 22→8). Con
    `TIMEZONE=UTC` no distinguirían una conversión de una afirmación, y con una zona
    de otro offset fallarían por el entorno y no por el código. El `assert` de abajo
    queda como red: si el sistema no tiene la base de zonas, `_zone` degrada a UTC en
    silencio y sin esto el módulo entero pasaría sin probar nada.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "timezone", _ZONE)
    monkeypatch.setattr(settings, "quiet_hours_start", 22)
    monkeypatch.setattr(settings, "quiet_hours_end", 8)

    offset = datetime(2026, 3, 15, 12, tzinfo=household_tz()).utcoffset()
    assert offset == timedelta(hours=-3), f"el sistema no resolvió {_ZONE}"


def _local(hour: int, minute: int = 30) -> datetime:
    """Un instante real cuya hora *local* es *hour*."""
    return datetime(2026, 3, 15, hour, minute, tzinfo=household_tz())


_JOBS_DIR = Path(__file__).resolve().parent.parent / "app" / "jobs"

#: El nombre por el que se le habla a una persona. Es el único camino: la única forma de
#: que aparezca una notificación es `NotificationService.create`, y sus únicos llamadores
#: son los jobs (ver la docstring de `notification_jobs._muted`).
_SPEAKING_NAME = "NotificationService"


def _jobs_that_speak() -> list[tuple[str, str]]:
    """Los jobs de `app/jobs/` que pueden llegar a crear una notificación.

    Se lee la fuente con `ast` en vez de importar y seguir llamadas porque lo que hay que
    saber es estático: qué *puede* hacer el job, no qué hizo en una corrida. Un job habla si
    su cuerpo nombra `NotificationService` o si llama —directa o indirectamente, dentro de su
    módulo— a algo que lo nombra; los cuatro recordatorios hablan por la segunda vía, porque
    delegan en `_run_absence_job`. De ahí el punto fijo: hoy la cadena tiene dos saltos y
    mañana puede tener tres.

    Devuelve pares `(módulo, función)` y no solo el nombre porque el gate se prueba pisando
    `is_quiet_hours` **en el módulo del job**, y un job nuevo puede vivir en un archivo nuevo.
    """
    found: list[tuple[str, str]] = []
    for path in sorted(_JOBS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        speaks = {
            name: any(
                isinstance(inner, ast.Name) and inner.id == _SPEAKING_NAME
                for inner in ast.walk(node)
            )
            for name, node in functions.items()
        }
        calls = {
            name: {
                inner.func.id
                for inner in ast.walk(node)
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
            }
            for name, node in functions.items()
        }
        propagating = True
        while propagating:
            propagating = False
            for name, callees in calls.items():
                if not speaks[name] and any(speaks.get(callee, False) for callee in callees):
                    speaks[name] = True
                    propagating = True
        module = f"app.jobs.{path.stem}"
        found.extend(
            (module, name) for name in sorted(functions) if name.startswith("run_") and speaks[name]
        )
    return found


class TestQuietHours:
    """La franja cruza la medianoche, y eso es el caso normal (22 → 8)."""

    @pytest.mark.parametrize(
        ("local_hour", "quiet"),
        [
            (23, True),  # después del inicio, antes de medianoche
            (0, True),  # la medianoche está adentro
            (3, True),  # la hora en la que llegaban notificaciones
            (7, True),  # el último minuto de la franja
            (8, False),  # el fin es exclusivo
            (12, False),
            (21, False),  # el inicio todavía no llegó
            (22, True),  # el inicio es inclusivo
        ],
    )
    def test_the_default_window_wraps_around_midnight(self, local_hour: int, quiet: bool) -> None:
        assert is_quiet_hours(_local(local_hour)) is quiet

    def test_a_window_inside_one_day_does_not_wrap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Con inicio menor que fin la franja es un intervalo común."""
        settings = get_settings()
        monkeypatch.setattr(settings, "quiet_hours_start", 1)
        monkeypatch.setattr(settings, "quiet_hours_end", 5)

        assert is_quiet_hours(_local(3)) is True
        assert is_quiet_hours(_local(23)) is False
        assert is_quiet_hours(_local(5)) is False

    def test_start_equal_to_end_means_no_quiet_hours(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Es la forma de apagar el silencio sin un flag aparte.

        Sin esta rama, `start == end` con la comparación de franja que cruza la
        medianoche daría `True` a **toda** hora: la app se quedaría muda para
        siempre en vez de hablar siempre.
        """
        settings = get_settings()
        monkeypatch.setattr(settings, "quiet_hours_start", 8)
        monkeypatch.setattr(settings, "quiet_hours_end", 8)

        assert is_quiet_hours(_local(3)) is False
        assert is_quiet_hours(_local(12)) is False

    @pytest.mark.parametrize(
        ("utc_hour", "quiet"),
        # Los dos casos están elegidos para que distingan interpretar de afirmar:
        # 10:00 UTC son 07:00 locales (silencio) pero 10:00 leídas como locales no
        # lo serían, y 23:30 UTC son 20:30 locales (habla) pero 23:30 leídas como
        # locales caerían adentro de la franja.
        [(10, True), (23, False)],
    )
    def test_a_naive_moment_is_read_as_utc_not_as_local(self, utc_hour: int, quiet: bool) -> None:
        naive = datetime(2026, 3, 15, utc_hour, 30)
        assert is_quiet_hours(naive) is quiet

    def test_without_an_argument_it_reads_the_clock_now(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Es la forma en la que lo llaman los jobs: sin argumento.

        La franja se mueve alrededor de la hora actual en vez de comparar dos
        llamadas entre sí, que pasaría igual si el argumento se ignorara.
        """
        settings = get_settings()
        now = local_now().hour

        monkeypatch.setattr(settings, "quiet_hours_start", now)
        monkeypatch.setattr(settings, "quiet_hours_end", (now + 1) % 24)
        assert is_quiet_hours() is True

        monkeypatch.setattr(settings, "quiet_hours_start", (now + 1) % 24)
        monkeypatch.setattr(settings, "quiet_hours_end", (now + 2) % 24)
        assert is_quiet_hours() is False


class TestConversions:
    """`as_utc` **convierte**; el `.replace(tzinfo=utc)` que había **afirmaba**."""

    def test_an_aware_moment_keeps_its_instant(self) -> None:
        local = _local(9)
        converted = as_utc(local)

        assert converted.tzinfo is UTC
        assert converted == local  # el mismo instante, otra representación
        assert converted.hour != local.hour  # y no la misma lectura de reloj

    def test_a_naive_moment_is_stamped_utc(self) -> None:
        naive = datetime(2026, 3, 15, 12, 0)
        assert as_utc(naive) == datetime(2026, 3, 15, 12, 0, tzinfo=UTC)

    def test_to_local_reads_a_naive_column_as_utc(self) -> None:
        """Lo que guardan las columnas del esquema es UTC, no hora local.

        Es el bug del recordatorio de pesaje: con el instante afirmado en vez de
        convertido, la antigüedad de la última medición se corría el offset del
        servidor y el aviso salía un día antes.
        """
        naive_utc = datetime(2026, 3, 15, 12, 0)
        assert to_local(naive_utc).hour == 9  # UTC-3

    def test_round_tripping_a_naive_column_does_not_move_the_instant(self) -> None:
        naive_utc = datetime(2026, 3, 15, 12, 0)
        assert as_utc(to_local(naive_utc)) == naive_utc.replace(tzinfo=UTC)


class TestLocalDay:
    """ "Hoy" es el día del hogar, no el día UTC del proceso."""

    def test_the_bounds_cover_the_local_day_and_nothing_else(self) -> None:
        start, end = local_day_bounds(date(2026, 3, 15))

        assert to_local(start).date() == date(2026, 3, 15)
        assert to_local(end).date() == date(2026, 3, 15)
        assert (to_local(start).hour, to_local(start).minute) == (0, 0)
        assert (to_local(end).hour, to_local(end).minute) == (23, 59)
        # Aware y en UTC, que es contra lo que se compara la columna.
        assert start.tzinfo is UTC and end.tzinfo is UTC
        # Y el día local no es el día UTC: el corte cae dentro del otro día.
        assert start.date() == date(2026, 3, 15) and end.date() == date(2026, 3, 16)

    def test_a_late_night_meal_still_belongs_to_today(
        self, db: Session, household: Household, diego: User
    ) -> None:
        """El bug que se veía en pantalla.

        Los límites del día se armaban con `datetime.combine(...)` naive contra una
        columna `timestamptz`, así que "hoy" era el día UTC: una comida de las 22:00
        de acá (01:00 UTC de mañana) no la contaba el Home y `/meals` la mostraba en
        el día siguiente.
        """
        today = local_today()
        at_night = datetime.combine(today, time(22, 0), tzinfo=household_tz())
        #: Guardado en UTC, que es lo que escribe la app: el mismo instante cae en el
        #: día UTC **siguiente**, y ahí se rompía el filtro.
        stored = as_utc(at_night)
        assert stored.date() != today, "el test necesita una comida al filo del día"

        svc = MealService(db)
        event = svc.log_meal(
            household.id,
            MealEventCreate(
                timestamp=stored,
                meal_type="dinner",
                participants=[MealParticipantCreate(user_id=diego.id, items=[])],
            ),
        )

        assert [m.id for m in svc.get_today_meals(household.id)] == [event.id]
        assert [m.id for m in svc.get_meals(household.id, on_date=today)] == [event.id]


class TestSchedule:
    """El calendario dejó de depender de la hora de arranque del proceso."""

    def test_every_job_runs_on_a_cron_trigger_in_the_household_timezone(self) -> None:
        assert set(_SCHEDULE) == {
            "low_stock_notifications",
            "inactivity_notifications",
            "metric_reminders",
            "meal_reminders",
            "sleep_reminders",
            "absence_sweep",
            "suggestion_generation",
            "notification_pruning",
        }
        for job_id in _SCHEDULE:
            trigger = _trigger(job_id)
            assert isinstance(trigger, CronTrigger)
            assert str(trigger.timezone) == get_settings().timezone

    @pytest.mark.parametrize("job_id", sorted(_SCHEDULE))
    def test_the_run_time_is_the_same_whenever_the_process_started(self, job_id: str) -> None:
        """Esto es todo el punto del cambio.

        `IntervalTrigger` sin `start_date` dispara por primera vez en
        `arranque + intervalo`, así que un deploy a las 03:00 dejaba el aviso de
        despensa saliendo a las 03:00 para siempre. Con `CronTrigger` los dos
        arranques de acá — separados por nueve horas — caen en alguna de las horas
        de la tabla, que es lo único que decide cuándo corre el job.
        """
        hours, minute = _SCHEDULE[job_id]
        scheduled = {(int(h), minute) for h in hours.split(",")}
        trigger = _trigger(job_id)

        for started_at in (3, 12):
            fire_time = trigger.get_next_fire_time(None, _local(started_at, 0))
            assert fire_time is not None
            assert (fire_time.hour, fire_time.minute) in scheduled

    def test_the_absence_sweep_runs_before_the_morning_generation(self) -> None:
        """El orden entre estos dos es la razón de la hora, no una casualidad.

        El barrido escribe lo que se aprendió de las tarjetas que nadie usó. Si corriera
        después de la generación de la mañana, ese aprendizaje recién reordenaría las
        sugerencias del día siguiente y la persona vería otra vez la tarjeta que ya ignoró
        una semana.
        """
        sweep = _trigger("absence_sweep").get_next_fire_time(None, _local(0, 0))
        generation = _trigger("suggestion_generation").get_next_fire_time(None, _local(0, 0))
        assert sweep is not None and generation is not None
        assert sweep < generation

    def test_start_scheduler_registers_the_whole_table(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Un id de `_SCHEDULE` que no coincida con un job explota en `_trigger`.

        Las dos listas viven separadas — la tabla de horarios y el mapa de
        funciones —, así que vale pinchar que sigan alineadas sin arrancar un
        scheduler de verdad.
        """
        from app.jobs import scheduler as scheduler_module

        registered: dict[str, object] = {}

        class _FakeScheduler:
            def add_job(self, func: object, trigger: object, id: str, **kw: object) -> None:
                registered[id] = trigger

            def start(self) -> None:
                pass

            def get_jobs(self) -> list[object]:
                return list(registered)

        monkeypatch.setattr(scheduler_module.settings, "enable_background_jobs", True)
        monkeypatch.setattr(scheduler_module, "get_scheduler", _FakeScheduler)
        scheduler_module.start_scheduler()

        assert set(registered) == set(scheduler_module._SCHEDULE)
        assert all(isinstance(t, CronTrigger) for t in registered.values())


class TestQuietHoursGate:
    """Todos los jobs que le hablan a alguien se callan en la franja.

    La lista no está escrita a mano, y esa es la mitad del test. Escrita a mano —como
    estaba— cubría los cinco jobs que hablaban el día que se escribió, y un sexto que
    creara notificaciones sin pasar por `_muted` no aparecía en ninguna parametrización:
    la suite entera pasaba en verde y el síntoma era la casa despierta a las 3 de la
    mañana. Ahora la lista sale de leer las fuentes (`_jobs_that_speak`), así que un job
    nuevo que pueda llegar a `NotificationService` entra solo y tiene que probar que se
    calla.

    La poda queda afuera sola, sin excepción escrita: usa `NotificationRepository` para
    borrar filas y no le habla a nadie, y por eso corre a las 4:15, adentro de la franja.
    El barrido de ausencias tampoco aparece por el mismo motivo — escribe señales.
    """

    def test_the_search_finds_the_jobs_that_do_speak_and_only_those(self) -> None:
        """El riesgo de una lista computada: si devuelve vacío, parametriza cero casos.

        Cero casos es verde, así que la lista que reemplazó a la escrita a mano necesita su
        propio piso. Se fija por los dos lados y sin escribir el conjunto completo: los cinco
        que hoy hablan tienen que estar —si el recorrido se rompe, el silencio se nota— y los
        dos que escriben sin hablar no pueden estar, porque si estuvieran el gate les
        apagaría un trabajo que nadie escucha. Un job nuevo y bien silenciado no rompe nada
        acá: el que lo obliga a callarse es el test de abajo.
        """
        speaking = {name for _, name in _jobs_that_speak()}

        assert {
            "run_low_stock_notifications",
            "run_inactivity_notifications",
            "run_metric_reminder_notifications",
            "run_meal_reminder_notifications",
            "run_sleep_reminder_notifications",
        } <= speaking
        assert "run_notification_pruning" not in speaking
        assert "run_absence_sweep" not in speaking

    @pytest.mark.parametrize(("module_name", "job_name"), _jobs_that_speak())
    def test_a_muted_job_does_not_even_open_a_session(
        self, monkeypatch: pytest.MonkeyPatch, module_name: str, job_name: str
    ) -> None:
        """El gate va antes de `SessionLocal()`, no después.

        Con la franja activa el job no toca la base: si abriera la sesión y
        filtrara más adentro, el `except Exception` de cada job se comería la
        prueba en silencio.

        El `monkeypatch` de `is_quiet_hours` es sobre el módulo del job, y con `raising`
        puesto: un job que hable y no tenga el nombre importado falla acá, que es
        exactamente lo que hay que saber de él.
        """
        module = importlib.import_module(module_name)

        def _no_sessions_please() -> None:
            raise AssertionError(f"{job_name} abrió una sesión en horario de silencio")

        monkeypatch.setattr(module, "is_quiet_hours", lambda: True)
        monkeypatch.setattr(module, "SessionLocal", _no_sessions_please)

        getattr(module, job_name)()  # no debe levantar nada

    def test_outside_the_window_the_job_does_its_work(
        self, monkeypatch: pytest.MonkeyPatch, db: Session, diego: User
    ) -> None:
        """El control negativo del test de arriba.

        Sin esto, un `_muted` que devolviera `True` siempre — o un `return` de más
        arriba — pasa toda la suite, y el síntoma es una app que no vuelve a avisar
        nada nunca. Diego no tiene ninguna medición, así que el recordatorio de
        pesaje le corresponde.
        """
        from app.jobs import notification_jobs

        monkeypatch.setattr(notification_jobs, "is_quiet_hours", lambda: False)
        monkeypatch.setattr(notification_jobs, "SessionLocal", lambda: db)
        #: El job cierra su sesión en el `finally` y acá la sesión es la del test,
        #: que todavía tiene que poder leer lo que se escribió.
        monkeypatch.setattr(db, "close", lambda: None)

        notification_jobs.run_metric_reminder_notifications()

        created = db.query(Notification).filter(Notification.user_id == diego.id).all()
        assert [n.category for n in created] == ["metric_reminder"]
