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


def test_home_warns_about_low_stock(
    authenticated_client: TestClient, seeded: dict[str, int]
) -> None:
    """El único aviso del Home que depende del estado de la despensa."""
    body = authenticated_client.get("/").text
    assert "banana" in body
