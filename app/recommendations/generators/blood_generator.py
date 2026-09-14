"""Blood analysis-based recommendation generator.

Maps abnormal biomarker values to concrete food/activity suggestions.
Only generates suggestions for statuses: low, critical_low, high, critical_high.
"""
from __future__ import annotations

import logging
from typing import Any

from app.models.user import User
from app.recommendations.context import BloodPanel

logger = logging.getLogger(__name__)

# Map biomarker key -> (low suggestions, high suggestions)
# Each suggestion: dict with title, text, rationale, category, confidence
_BIOMARKER_SUGGESTIONS: dict[str, dict[str, list[dict[str, Any]]]] = {
    "hemoglobin": {
        "low": [
            {
                "title": "Boost iron with lentils",
                "text": "Your hemoglobin is low. Lentils are rich in iron and folate—try adding them to soups or salads.",
                "rationale": "Low hemoglobin may indicate iron deficiency anemia.",
                "category": "meal",
                "confidence": 0.85,
            },
            {
                "title": "Add spinach to your meals",
                "text": "Spinach is high in iron and vitamin C, which helps iron absorption.",
                "rationale": "Low hemoglobin benefit from iron-rich leafy greens.",
                "category": "meal",
                "confidence": 0.80,
            },
        ],
        "high": [],
    },
    "ferritin": {
        "low": [
            {
                "title": "Iron-rich foods: red meat or legumes",
                "text": "Your ferritin (iron stores) is low. Consider lean red meat, lentils, or tofu a few times a week.",
                "rationale": "Low ferritin indicates depleted iron stores.",
                "category": "meal",
                "confidence": 0.85,
            },
        ],
        "high": [
            {
                "title": "Reduce red meat intake",
                "text": "Your ferritin is elevated. Limit red and processed meats and avoid iron supplements.",
                "rationale": "High ferritin may indicate iron overload.",
                "category": "meal",
                "confidence": 0.75,
            },
        ],
    },
    "vitamin_d": {
        "low": [
            {
                "title": "Get 20 min of morning sun",
                "text": "Your vitamin D is low. Spending 20 minutes in morning sunlight helps your body synthesize vitamin D naturally.",
                "rationale": "Sunlight is the most effective source of vitamin D.",
                "category": "activity",
                "confidence": 0.90,
            },
            {
                "title": "Add fatty fish to your diet",
                "text": "Salmon, mackerel, and sardines are rich in vitamin D. Aim for 2 servings per week.",
                "rationale": "Low vitamin D benefits from dietary sources.",
                "category": "meal",
                "confidence": 0.80,
            },
        ],
        "high": [],
    },
    "vitamin_b12": {
        "low": [
            {
                "title": "Include eggs and dairy daily",
                "text": "Your B12 is low. Eggs, cheese, and yogurt are easy daily sources of vitamin B12.",
                "rationale": "B12 deficiency affects energy and nerve function.",
                "category": "meal",
                "confidence": 0.85,
            },
        ],
        "high": [],
    },
    "folate": {
        "low": [
            {
                "title": "Eat more leafy greens",
                "text": "Your folate is low. Kale, spinach, broccoli, and asparagus are excellent sources.",
                "rationale": "Folate is essential for cell function and energy.",
                "category": "meal",
                "confidence": 0.85,
            },
        ],
        "high": [],
    },
    "iron": {
        "low": [
            {
                "title": "Pair iron foods with vitamin C",
                "text": "Your iron is low. Eat iron-rich foods (meat, beans) alongside vitamin C sources like citrus to boost absorption.",
                "rationale": "Vitamin C enhances non-heme iron absorption significantly.",
                "category": "meal",
                "confidence": 0.80,
            },
        ],
        "high": [],
    },
    "glucose": {
        "high": [
            {
                "title": "30-min walk after dinner",
                "text": "Your glucose is elevated. A 30-minute walk after meals can significantly lower blood sugar.",
                "rationale": "Post-meal exercise improves insulin sensitivity.",
                "category": "activity",
                "confidence": 0.90,
            },
            {
                "title": "Reduce refined carbohydrates",
                "text": "Swap white bread, rice, and sugary drinks for whole grains and fiber-rich options.",
                "rationale": "Refined carbs spike blood glucose rapidly.",
                "category": "meal",
                "confidence": 0.85,
            },
        ],
        "low": [],
    },
    "cholesterol_total": {
        "high": [
            {
                "title": "Add oats to your breakfast",
                "text": "Your total cholesterol is high. Oats contain beta-glucan which helps lower LDL cholesterol.",
                "rationale": "Soluble fiber binds cholesterol in the digestive tract.",
                "category": "meal",
                "confidence": 0.85,
            },
        ],
        "low": [],
    },
    "ldl": {
        "high": [
            {
                "title": "Limit saturated fats",
                "text": "Your LDL (bad cholesterol) is high. Reduce butter, fatty meats, and full-fat dairy. Choose olive oil instead.",
                "rationale": "Saturated fat raises LDL cholesterol.",
                "category": "meal",
                "confidence": 0.85,
            },
            {
                "title": "Cardio 3x/week for cholesterol",
                "text": "Regular aerobic exercise (cycling, swimming, brisk walking) helps lower LDL and raise HDL.",
                "rationale": "Aerobic exercise is one of the most effective lifestyle changes for cholesterol.",
                "category": "activity",
                "confidence": 0.85,
            },
        ],
        "low": [],
    },
    "hdl": {
        "low": [
            {
                "title": "Increase aerobic exercise",
                "text": "Your HDL (good cholesterol) is low. Regular aerobic activity is the most effective way to raise HDL.",
                "rationale": "HDL removes excess cholesterol from arteries.",
                "category": "activity",
                "confidence": 0.90,
            },
            {
                "title": "Add avocado and nuts to meals",
                "text": "Healthy fats from avocado, walnuts, and almonds help raise HDL cholesterol.",
                "rationale": "Monounsaturated fats improve HDL levels.",
                "category": "meal",
                "confidence": 0.80,
            },
        ],
        "high": [],
    },
    "triglycerides": {
        "high": [
            {
                "title": "Cut sugary drinks and alcohol",
                "text": "Your triglycerides are elevated. Sugary drinks, alcohol, and refined carbs are the main culprits.",
                "rationale": "Simple sugars are converted to triglycerides in the liver.",
                "category": "meal",
                "confidence": 0.90,
            },
        ],
        "low": [],
    },
    "tsh": {
        "high": [
            {
                "title": "Discuss thyroid check-up with your doctor",
                "text": "Your TSH is elevated, which may indicate underactive thyroid. A medical consultation is recommended.",
                "rationale": "High TSH can cause fatigue and metabolic slowdown.",
                "category": "habit",
                "confidence": 0.70,
            },
        ],
        "low": [
            {
                "title": "Discuss thyroid check-up with your doctor",
                "text": "Your TSH is low, which may indicate overactive thyroid. Consider a medical consultation.",
                "rationale": "Low TSH can cause rapid heart rate and anxiety.",
                "category": "habit",
                "confidence": 0.70,
            },
        ],
    },
    "magnesium": {
        "low": [
            {
                "title": "Add magnesium-rich foods",
                "text": "Your magnesium is low. Pumpkin seeds, dark chocolate, spinach, and almonds are excellent sources.",
                "rationale": "Magnesium supports muscle function and sleep quality.",
                "category": "meal",
                "confidence": 0.80,
            },
        ],
        "high": [],
    },
    "zinc": {
        "low": [
            {
                "title": "Eat more shellfish and seeds",
                "text": "Your zinc is low. Oysters, pumpkin seeds, beef, and chickpeas are rich in zinc.",
                "rationale": "Zinc is essential for immunity and wound healing.",
                "category": "meal",
                "confidence": 0.80,
            },
        ],
        "high": [],
    },
    "creatinine": {
        "high": [
            {
                "title": "Increase daily water intake",
                "text": "Your creatinine is elevated. Staying well-hydrated supports kidney function.",
                "rationale": "Dehydration can raise creatinine levels.",
                "category": "habit",
                "confidence": 0.75,
            },
        ],
        "low": [],
    },
}


def generate(
    user: User,
    panel: BloodPanel | None,
) -> list[dict[str, Any]]:
    """Generate suggestions based on abnormal blood biomarker values.

    Args:
        user: The target User.
        panel: El último panel legible de esa persona, con su fecha, o `None` si no hay.

    Returns:
        List of candidate suggestion dicts with title, text, rationale, category, confidence.

    Recibía `db` —declarado "unused currently, reserved for future DB lookups"— y un dict de
    marcadores sin fecha, porque `get_latest_values` devolvía el blob y tiraba la fila. Ahora
    recibe el panel entero: la antigüedad estaba en la base todo este tiempo y era lo único
    que faltaba para no dar consejos sobre un análisis de hace tres años. Usarla es 4.5.6; que
    llegue hasta acá es este punto.
    """
    if panel is None or not panel.values:
        return []

    candidates: list[dict[str, Any]] = []

    for biomarker_key, data in panel.values.items():
        status = data.get("status", "normal")
        if status not in ("low", "critical_low", "high", "critical_high"):
            continue

        bucket = "low" if status in ("low", "critical_low") else "high"
        suggestions = _BIOMARKER_SUGGESTIONS.get(biomarker_key, {}).get(bucket, [])

        for s in suggestions:
            candidate = dict(s)
            # Bump confidence for critical values
            if status in ("critical_low", "critical_high"):
                candidate["confidence"] = min(1.0, s["confidence"] + 0.05)
            candidate["source_type"] = "blood_analysis"
            #: El sujeto es el marcador, no el consejo. Cada marcador anormal produce
            #: hasta dos tarjetas con el mismo origen, así que anclarlas al alimento que
            #: nombran —"lentils", "spinach"— habría hecho que rechazar una enseñara sobre
            #: las lentejas cuando lo que la persona rechazó es que le hablen del hierro.
            #: Y con el marcador como sujeto, el dedup de la 4.4 alcanza para que un
            #: mismo panel no vuelva a producir la tarjeta que ya está pendiente.
            candidate["subject_type"] = "biomarker"
            candidate["subject_name"] = biomarker_key
            candidate["evidence_summary"] = (
                f"{data.get('display_name', biomarker_key)}: "
                f"{data.get('value')} {data.get('unit', '')} ({status})"
            )
            candidates.append(candidate)

    logger.debug(
        "Blood generator produced %d candidates for user_id=%d",
        len(candidates),
        user.id,
    )
    return candidates
