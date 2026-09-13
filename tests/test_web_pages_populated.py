"""Smoke: las mismas páginas, pero con datos adentro.

`test_web_pages.py` corre contra una base vacía, así que ejercita **solo la rama
`{% else %}`** de cada pantalla: los estados vacíos. Justo la mitad que el barrido
de la Fase 3 casi no toca.

Las ramas con datos son las que tienen la lógica: el delta de peso del dashboard
(`latest - first`), los condicionales por `meal_type`, la atribución por
participante, los markers de sangre partidos en abnormal/normal. Un `|round(1)`
sobre `None`, un tono que no existe o un atributo renombrado revientan **solo**
cuando hay una fila que renderizar, y sin este módulo eso pasaría en el navegador.
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.blood_analysis import BloodAnalysis
from app.models.food import FoodItem
from app.models.household import Household
from app.models.meal import MealEvent, MealItemConsumed, MealParticipant
from app.models.notification import Notification
from app.models.pantry import PantryMovement, PantryStock
from app.models.suggestion import Suggestion
from app.models.user import User
from app.models.workout import WorkoutExercise, WorkoutParticipant, WorkoutSession

UTC_NOW = datetime.now(UTC)


@pytest.fixture
def seeded(
    db: Session, household: Household, diego: User, rocio: User, banana: FoodItem
) -> dict[str, int]:
    """Una fila de cada cosa que las pantallas saben renderizar.

    Dos pesajes (no uno) porque el delta de 30 días del dashboard solo se calcula
    con más de una medición, y dos comensales porque la atribución con un solo
    participante no distingue entre "el avatar correcto" y "el primero de la lista".
    """
    from app.models.body_metric import BodyMetricLog

    db.add_all(
        [
            BodyMetricLog(
                user_id=diego.id,
                timestamp=UTC_NOW - timedelta(days=20),
                weight_kg=80.0,
                sleep_hours=7.5,
            ),
            BodyMetricLog(
                user_id=diego.id,
                timestamp=UTC_NOW,
                weight_kg=78.4,
                body_fat_pct=18.2,
                waist_cm=84.0,
                notes="post vacaciones",
            ),
        ]
    )

    meal = MealEvent(
        household_id=household.id, timestamp=UTC_NOW, meal_type="lunch", context="home"
    )
    db.add(meal)
    db.flush()
    for user in (diego, rocio):
        part = MealParticipant(
            meal_event_id=meal.id, user_id=user.id, portion_label="normal", satiety_after=4
        )
        db.add(part)
        db.flush()
        db.add(
            MealItemConsumed(
                meal_event_id=meal.id,
                meal_participant_id=part.id,
                food_item_id=banana.id,
                normalized_free_text_name="banana",
                quantity=1,
                unit="unit",
            )
        )

    session = WorkoutSession(
        household_id=household.id,
        timestamp_start=UTC_NOW - timedelta(hours=2),
        duration_minutes=55,
        workout_type="gym",
        source="manual",
    )
    db.add(session)
    db.flush()
    participant = WorkoutParticipant(workout_session_id=session.id, user_id=diego.id)
    db.add(participant)
    db.flush()
    db.add(
        WorkoutExercise(
            workout_session_id=session.id,
            workout_participant_id=participant.id,
            exercise_name="press banca",
            sets=4,
            reps=8,
            load_kg=60.0,
            perceived_effort=7,
            muscle_group="chest",
        )
    )

    # Stock bajo a propósito: es el que dispara el banner del Home y el tono de
    # alerta de la grilla de pantry.
    stock = PantryStock(
        household_id=household.id,
        food_item_id=banana.id,
        current_quantity=1,
        unit="unit",
        low_stock_threshold=3,
        storage_location="fruit bowl",
    )
    db.add(stock)
    db.add(
        PantryMovement(
            household_id=household.id,
            user_id=diego.id,
            food_item_id=banana.id,
            movement_type="purchase",
            quantity=6,
            unit="unit",
            timestamp=UTC_NOW - timedelta(days=1),
        )
    )

    db.add(
        Suggestion(
            scope_type="user",
            household_id=household.id,
            scope_user_id=diego.id,
            category="activity",
            title="Sumá una caminata liviana",
            text="Venís de cuatro días de fuerza sin cardio.",
            rationale="Balance entre fuerza y cardio en los últimos 7 días.",
            confidence=0.72,
            source_type="rule",
            status="pending",
        )
    )
    db.add(
        Notification(
            household_id=household.id,
            user_id=diego.id,
            category="low_stock",
            title="Se está terminando: banana",
            body="Queda 1 unit y el umbral es 3.",
        )
    )

    analysis = BloodAnalysis(
        user_id=diego.id,
        analysis_date=date.today(),
        lab_name="Lab Central",
        file_name="panel.pdf",
        raw_text="colesterol total 210 mg/dL",
        status="analyzed",
        parsing_method="regex",
        ai_summary="Colesterol levemente elevado.",
        values_json={
            "ldl": {
                "display_name": "LDL",
                "value": 145,
                "unit": "mg/dL",
                "ref_min": 0,
                "ref_max": 130,
                "status": "high",
                "category": "lipids",
            },
            "glucose": {
                "display_name": "Glucosa",
                "value": 88,
                "unit": "mg/dL",
                "ref_min": 70,
                "ref_max": 100,
                "status": "normal",
                "category": "metabolic",
            },
        },
    )
    db.add(analysis)
    db.flush()
    return {"meal_id": meal.id, "analysis_id": analysis.id, "stock_id": stock.id}


PAGES = [
    "/",
    "/pantry/",
    "/pantry/movements",
    "/meals/",
    "/workouts/",
    "/body-metrics/",
    "/health/",
    "/suggestions/",
    "/dashboard/",
    "/history/?tab=meals",
    "/history/?tab=workouts",
    "/history/?tab=body_metrics",
    "/history/?tab=pantry",
    "/profile/",
    "/notifications/",
]


@pytest.mark.parametrize("path", PAGES)
def test_page_renders_with_data(
    authenticated_client: TestClient, seeded: dict[str, int], path: str
) -> None:
    resp = authenticated_client.get(path)
    assert resp.status_code == 200, f"{path} → {resp.status_code}"
    assert "</body>" in resp.text, f"{path}: el documento quedó cortado"
    for leak in ("<object ", " object at 0x", "jinja2.", "Undefined"):
        assert leak not in resp.text, f"{path}: se filtró {leak!r} al HTML"


def test_detail_pages_render(authenticated_client: TestClient, seeded: dict[str, int]) -> None:
    """Las dos pantallas con `{id}` en la ruta, que la suite vacía no puede pedir."""
    for path in (f"/meals/{seeded['meal_id']}", f"/health/{seeded['analysis_id']}"):
        resp = authenticated_client.get(path)
        assert resp.status_code == 200, f"{path} → {resp.status_code}"
        assert "</body>" in resp.text, path


def test_dashboard_shows_the_weight_delta(
    authenticated_client: TestClient, seeded: dict[str, int]
) -> None:
    """80.0 → 78.4 son −1.6 kg, y el signo es la mitad del mensaje.

    Se afirma el número renderizado porque el cálculo vive en la plantilla
    (`latest - first`, `|round(1)`): si alguien lo mueve o invierte el orden de la
    serie, el dashboard sigue dando 200 y muestra el delta al revés.
    """
    body = authenticated_client.get("/dashboard/").text
    assert "78.4" in body
    assert "-1.6" in body


def test_body_metrics_cards_show_the_latest_reading(
    authenticated_client: TestClient, seeded: dict[str, int]
) -> None:
    """Las cuatro tarjetas leían una clave que la ruta no escribía.

    La plantilla pedía `latest_per_user` y la ruta pasaba `latest_metrics`, así que la
    expresión caía siempre en el `{% else %}`: la pantalla mostraba "—" en las cuatro
    con la base llena y seguía devolviendo 200. Solo un assert sobre el número
    renderizado atrapa un desajuste de nombres entre ruta y plantilla.
    """
    body = authenticated_client.get("/body-metrics/").text
    assert "78.4" in body, "la tarjeta de peso no muestra el último pesaje"
    assert "18.2" in body and "84" in body
    assert "post vacaciones" in body, "el historial no llega a la plantilla"


def test_body_metrics_ignores_a_user_id_from_another_household(
    authenticated_client: TestClient, seeded: dict[str, int], db: Session
) -> None:
    """`?user_id=` venía del query string sin validarse contra el hogar.

    Iba directo a `get_user_metrics`, así que el id de cualquier persona de la base
    devolvía **su** peso, su grasa corporal y sus horas de sueño. Todo dato personal
    se filtra por el hogar de quien pregunta (`AGENTS.md`), y esta es la prueba de que
    un id ajeno cae de vuelta en el usuario que pide en lugar de servirlo.
    """
    from app.models.body_metric import BodyMetricLog

    other = Household(name="Otra casa")
    db.add(other)
    db.flush()
    stranger = User(
        household_id=other.id,
        name="Ajena",
        email="ajena@test.com",
        password_hash="x",
        onboarding_completed=True,
    )
    db.add(stranger)
    db.flush()
    db.add(BodyMetricLog(user_id=stranger.id, timestamp=UTC_NOW, weight_kg=99.9))
    db.flush()

    resp = authenticated_client.get(f"/body-metrics/?user_id={stranger.id}")
    assert resp.status_code == 200
    assert "99.9" not in resp.text, "fuga: métricas corporales de otro hogar"
    assert "78.4" in resp.text, "debería caer de vuelta en el usuario que pide"


def test_health_detail_does_not_leak_raw_enum_values(
    authenticated_client: TestClient, seeded: dict[str, int]
) -> None:
    """El detalle mostraba `status: high` y el `parsing_method` crudos.

    Los rótulos ahora pasan por `dm.marker_status_badge` / `dm.analysis_status_badge`,
    que son las únicas dos traducciones de esos enums: si alguien vuelve a imprimir
    `m.status` directo, la clave de la base reaparece en pantalla.
    """
    body = authenticated_client.get(f"/health/{seeded['analysis_id']}").text
    assert "LDL" in body and "Glucosa" in body
    assert "status: " not in body
    assert "analyzed" not in body, "el estado del análisis se muestra sin traducir"


def test_dashboard_ignores_a_user_id_from_another_household(
    authenticated_client: TestClient, seeded: dict[str, int], db: Session
) -> None:
    """La misma fuga que `/body-metrics/`, en la pantalla de gráficos.

    `uid = user_id or current_user.id` sin validar iba a `get_weight_series`, que filtra
    **solo** por usuario: el id de cualquier persona de la base devolvía su serie de peso
    de 30 días. Los otros cuatro gráficos ya arrancaban del hogar, así que la que se
    filtraba era justo la medida que el resto del rediseño protege.
    """
    from app.models.body_metric import BodyMetricLog

    other = Household(name="Tercera casa")
    db.add(other)
    db.flush()
    stranger = User(
        household_id=other.id,
        name="Ajeni",
        email="ajeni@test.com",
        password_hash="x",
        onboarding_completed=True,
    )
    db.add(stranger)
    db.flush()
    db.add_all(
        [
            BodyMetricLog(
                user_id=stranger.id, timestamp=UTC_NOW - timedelta(days=5), weight_kg=99.9
            ),
            BodyMetricLog(user_id=stranger.id, timestamp=UTC_NOW, weight_kg=98.7),
        ]
    )
    db.flush()

    resp = authenticated_client.get(f"/dashboard/?user_id={stranger.id}")
    assert resp.status_code == 200
    assert "99.9" not in resp.text and "98.7" not in resp.text, "fuga: serie de peso ajena"
    assert "78.4" in resp.text, "debería caer de vuelta en el usuario que pide"


@pytest.mark.parametrize("path", ["/dashboard/", "/body-metrics/", "/history/"])
@pytest.mark.parametrize("raw", ["", "abc", "-1", "²"])
def test_person_filters_survive_a_hand_edited_user_id(
    authenticated_client: TestClient, seeded: dict[str, int], path: str, raw: str
) -> None:
    """Ninguna de las tres pantallas con `?user_id=` puede contestar 422 ni 500.

    `""` es lo que manda un formulario GET sin JS, y `"²"` está acá porque
    `"²".isdigit()` es `True` mientras `int("²")` explota: con `isdigit`, el helper que
    existe para absorber URLs editadas a mano devolvía un 500.
    """
    resp = authenticated_client.get(f"{path}?user_id={raw}")
    assert resp.status_code == 200, f"{path}?user_id={raw!r} → {resp.status_code}"


def test_health_detail_renders_its_safety_notice_in_spanish(
    authenticated_client: TestClient, seeded: dict[str, int]
) -> None:
    """El aviso de "esto no es un diagnóstico" tiene que estar en el idioma del hogar.

    Poner `_()` alrededor de una cadena no la traduce: el catálogo hay que regenerarlo.
    El rediseño agregó las llamadas y no el `.po`, así que la pantalla de salud mostraba
    "Out of range" / "In range" / "days ago" en inglés con `DEFAULT_LOCALE=es_AR` — un
    aviso de seguridad que el hogar no lee en su idioma no es un aviso. El otro test de
    esta pantalla no lo atrapa porque afirma sobre "LDL"/"Glucosa", que salen de
    `values_json`, no del catálogo.
    """
    body = authenticated_client.get(f"/health/{seeded['analysis_id']}").text
    assert "no un diagnóstico" in body
    assert "Fuera de rango" in body and "En rango" in body
    assert "Out of range" not in body and "In range" not in body


def test_health_detail_does_not_call_an_unevaluated_marker_normal(
    authenticated_client: TestClient, seeded: dict[str, int], db: Session, diego: User
) -> None:
    """Un marcador sin rango de referencia no está "en rango": no se evaluó.

    `_determine_status` devuelve `"unknown"` para cualquier clave que la tabla de 31
    rangos no conozca, y la Capa 2 del NLP acepta cualquier clave snake_case que el LLM
    emita. Ese `"unknown"` caía en el `else` de la ruta, o sea en la tarjeta verde, bajo
    un texto que afirmaba que todos los marcadores estaban dentro de su rango — una
    afirmación clínica sobre un valor que la app nunca comparó con nada.
    """
    analysis = BloodAnalysis(
        user_id=diego.id,
        analysis_date=date.today(),
        status="analyzed",
        values_json={
            "psa": {"display_name": "PSA", "value": 45, "unit": "ng/mL", "status": "unknown"},
            "glucose": {"display_name": "Glucosa", "value": 88, "status": "normal"},
        },
    )
    db.add(analysis)
    db.flush()

    body = authenticated_client.get(f"/health/{analysis.id}").text
    unevaluated = body.split('data-marker-group="unevaluated"')[-1]
    normal = body.split('data-marker-group="normal"')[-1].split("</ul>")[0]
    assert "PSA" in unevaluated.split("</ul>")[0], "el marcador sin rango no se muestra"
    assert "PSA" not in normal, 'un marcador sin evaluar se muestra como "en rango"'
    assert "Glucosa" in normal


def test_health_detail_hides_the_age_of_a_future_dated_panel(
    authenticated_client: TestClient, seeded: dict[str, int], db: Session, diego: User
) -> None:
    """La fecha la lee el parser del PDF, así que puede caer en el futuro.

    El `max(0, ...)` la convertía en "hace 0 días" al lado de un encabezado que dice
    2099: dos hechos contradictorios en pantalla, y el más prominente era el falso. Si la
    antigüedad no se puede afirmar, no se muestra.
    """
    analysis = BloodAnalysis(
        user_id=diego.id, analysis_date=date(2099, 1, 1), status="analyzed", values_json={}
    )
    db.add(analysis)
    db.flush()

    body = authenticated_client.get(f"/health/{analysis.id}").text
    assert "2099" in body, "la fecha leída se sigue mostrando, tal cual"
    for claim in ("0 day", "days ago", "hace 0"):
        assert claim not in body, f"un panel del futuro afirma antigüedad: {claim!r}"


def test_health_responses_are_not_cached_by_the_browser(
    authenticated_client: TestClient, seeded: dict[str, int]
) -> None:
    """Dos personas, un teléfono: el panel no puede quedar en el caché de disco."""
    resp = authenticated_client.get(f"/health/{seeded['analysis_id']}")
    assert resp.headers["cache-control"] == "no-store"
    assert resp.headers["referrer-policy"] == "same-origin"


def test_history_body_metrics_tab_ignores_a_foreign_user_id(
    authenticated_client: TestClient, seeded: dict[str, int], db: Session
) -> None:
    """El mismo `?user_id=` sin validar que `/body-metrics/`, en la otra pantalla.

    Las consultas de comidas y entrenamientos ya filtran por `household_id`, así que
    ahí un id ajeno solo daba lista vacía; el tab de métricas lo pasaba derecho a
    `get_user_metrics`, que filtra **solo** por usuario.
    """
    from app.models.body_metric import BodyMetricLog

    other = Household(name="Otra casa")
    db.add(other)
    db.flush()
    stranger = User(
        household_id=other.id,
        name="Ajeno",
        email="ajeno@test.com",
        password_hash="x",
        onboarding_completed=True,
    )
    db.add(stranger)
    db.flush()
    db.add(
        BodyMetricLog(user_id=stranger.id, timestamp=UTC_NOW, weight_kg=99.9, notes="de otra casa")
    )
    db.flush()

    resp = authenticated_client.get(f"/history/?tab=body_metrics&user_id={stranger.id}")
    assert resp.status_code == 200
    assert "99.9" not in resp.text and "de otra casa" not in resp.text


def test_history_body_metrics_tab_actually_paginates(
    authenticated_client: TestClient, seeded: dict[str, int], db: Session, diego: User
) -> None:
    """`BodyMetricService.get_user_metrics` no exponía el `offset` del repositorio.

    La plantilla dibujaba "Older →" igual, y la segunda página traía las mismas filas
    que la primera. Hacen falta más de `PAGE_SIZE` registros para verlo, de ahí las 31
    filas locales en vez de las dos del fixture.
    """
    from app.models.body_metric import BodyMetricLog

    for i in range(31):
        db.add(
            BodyMetricLog(
                user_id=diego.id,
                timestamp=UTC_NOW - timedelta(days=100 + i),
                weight_kg=90.0,
                notes=f"fila {i}",
            )
        )
    db.flush()

    second = authenticated_client.get("/history/?tab=body_metrics&offset=30").text
    assert "fila 0" not in second, "la segunda página repite la primera"
    assert "fila " in second, "la segunda página quedó vacía"


@pytest.mark.parametrize(
    "qs", ["?tab=body_metrics&user_id=", "?tab=meals&user_id=abc", "?offset=", "?offset=-30"]
)
def test_history_survives_a_hand_edited_query_string(
    authenticated_client: TestClient, seeded: dict[str, int], qs: str
) -> None:
    """Con `int | None`, FastAPI responde 422 en JSON desde una ruta de página.

    Estas URLs se comparten y se editan a mano, y un `?user_id=` vacío es exactamente
    lo que manda un formulario GET sin JS.
    """
    resp = authenticated_client.get(f"/history/{qs}")
    assert resp.status_code == 200, f"{qs} → {resp.status_code}"


def test_home_warns_about_low_stock(
    authenticated_client: TestClient, seeded: dict[str, int]
) -> None:
    """El único aviso del Home que depende del estado de la despensa."""
    body = authenticated_client.get("/").text
    assert "banana" in body


@pytest.fixture
def three_categories(db: Session, household: Household, diego: User) -> None:
    """Una notificación de cada categoría que algún job escribe hoy.

    El fixture `seeded` trae una sola (`low_stock`), y con una sola categoría la
    pantalla no dibuja pestañas — así que la rama del filtro quedaría sin cubrir.
    """
    db.add_all(
        [
            Notification(
                household_id=household.id,
                category="inactivity",
                title="Hace 5 días que nadie entrena",
                body="El último entrenamiento fue el lunes.",
            ),
            Notification(
                household_id=household.id,
                user_id=diego.id,
                category="metric_reminder",
                title="¿Te pesaste esta semana?",
                body="El último registro es de hace 9 días.",
            ),
        ]
    )
    db.flush()


def test_notification_filters_are_the_categories_that_exist(
    authenticated_client: TestClient, seeded: dict[str, int], three_categories: None
) -> None:
    """Las pastillas eran seis literales en la plantilla; tres no las escribe nadie.

    `suggestion`, `trend` e `info` están declaradas en la columna y ningún job las
    produce, así que eran tres filtros que no podían devolver nada. Ahora las
    pastillas salen de `get_category_counts`.
    """
    body = authenticated_client.get("/notifications/").text
    for present in ("low_stock", "inactivity", "metric_reminder"):
        assert f"/notifications/?category={present}" in body, present
    for absent in ("suggestion", "trend", "info"):
        assert f"/notifications/?category={absent}" not in body, absent


@pytest.mark.parametrize("category", ["low_stock", "inactivity", "metric_reminder"])
def test_notification_filter_links_do_not_bounce(
    authenticated_client: TestClient,
    seeded: dict[str, int],
    three_categories: None,
    category: str,
) -> None:
    """Los enlaces iban a `/notifications?category=…`, sin barra final: 307 en cada clic."""
    resp = authenticated_client.get(f"/notifications/?category={category}", follow_redirects=False)
    assert resp.status_code == 200, f"{category} → {resp.status_code}"


def test_notification_card_names_its_category_in_spanish(
    authenticated_client: TestClient, seeded: dict[str, int]
) -> None:
    """El rótulo era `cat|replace('_',' ')|title` — "Low Stock", inextraíble y en inglés.

    Y el ícono era un emoji: 🛒 no dice "poco stock" a un lector de pantalla, y cambia
    de forma en cada sistema operativo.
    """
    body = authenticated_client.get("/notifications/").text
    assert "Poco stock" in body
    assert "Low Stock" not in body
    for emoji in ("🛒", "🏃", "⚖️", "💡", "📈", "🔔"):
        assert emoji not in body, emoji


def test_notification_card_dates_are_local_and_in_spanish(
    authenticated_client: TestClient, seeded: dict[str, int], db: Session, diego: User
) -> None:
    """`created_at.strftime('%b %d')` daba el mes en inglés, en UTC y sin hora."""
    db.add(
        Notification(
            household_id=diego.household_id,
            user_id=diego.id,
            category="metric_reminder",
            title="Pesaje pendiente",
            body="El último registro es de hace 9 días.",
            created_at=datetime(2026, 3, 7, 23, 30, tzinfo=UTC),
        )
    )
    db.flush()

    body = authenticated_client.get("/notifications/").text
    assert "mar" in body, "no se ve el mes localizado"
    for english in ("Mar 07", "Mar 7"):
        assert english not in body, english


def test_mark_all_read_never_swaps_a_whole_document(
    authenticated_client: TestClient, seeded: dict[str, int]
) -> None:
    """La rama `HX-Request` devolvía `index.html` entera para meterla dentro del <body>.

    La plantilla la llamaba con `hx-target="body" hx-swap="outerHTML"`, así que un
    documento con `<html><head>` terminaba anidado adentro del que ya estaba abierto.
    Ahora es un POST con redirect, con o sin HTMX.
    """
    for headers in ({}, {"HX-Request": "true"}):
        resp = authenticated_client.post(
            "/notifications/mark-all-read", headers=headers, follow_redirects=False
        )
        assert resp.status_code == 302, resp.status_code
        assert "<html" not in resp.text

    assert "0 sin leer" not in authenticated_client.get("/notifications/").text
