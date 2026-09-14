"""Meal suggestion generator.

Generates meal suggestions for a user based on:
- Current pantry stock (prefer items that are available / nearly expiring)
- Recent meal history (avoid recent repetition)
- User dietary preferences and restrictions
- Time-of-day context (meal type)
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from sqlalchemy.orm import Session

from app.core.clock import local_now
from app.models.food import FoodItem
from app.models.pantry import PantryStock
from app.models.suggestion import RecommendationPreference
from app.models.user import User
from app.recommendations.context import RECENT_FOOD_DAYS, UserContext
from app.repositories.pantry_repo import PantryStockRepository

logger = logging.getLogger(__name__)

_MAX_SUGGESTIONS = 8

#: Los dos macros que esta app se permite nombrar, **en el orden en que desempatan** cuando
#: los dos están cortos. El orden está declarado y no derivado de nada, por la misma razón
#: que `activity_generator._ROTATION_PRIORITY`: un orden que sale del orden de un `dict` se
#: cambia sin querer al agregar una línea.
#:
#: Por qué solo dos, y por qué solo hacia abajo: la app **no tiene objetivo de macros**
#: (`goals_json` y `target_weight_kg` no se leen en ningún lado), así que lo único
#: afirmable es la comparación de la persona contra sí misma. Y una comparación contra uno
#: mismo solo da un consejo accionable en un sentido: "hoy vas más liviano de proteína que
#: tu promedio, y tenés lentejas" propone algo; "hoy vas más pesado de grasa que tu
#: promedio" no propone nada —no hay nada que agregar, solo algo que dejar de comer—, y eso
#: es consejo dietario sin objetivo, que es exactamente lo que no se puede sostener. Es la
#: misma escala de un solo lado que `activity_generator._HIGH_EFFORT_RPE`.
#:
#: Proteína primero porque es la que una comida mueve más, y porque la fibra suele venir con
#: las verduras que ya arrastra cualquier tarjeta de despensa.
_MACRO_TRACKED: tuple[tuple[str, str], ...] = (("protein_g", "protein"), ("fiber_g", "fiber"))

#: Cuántos días registrados tiene que tener la base para que sea una base. Con uno solo, "tu
#: promedio" es "el único día que anotaste", y ese día puede haber sido un asado.
_MACRO_MIN_BASELINE_DAYS = 3

#: Qué fracción de los ítems tuvo que entrar en la suma, **en las dos puntas**. Sumar un
#: macro necesita el alimento resuelto y una cantidad convertible a gramos (ver
#: `context.MacroTotals`), así que un día capturado en texto libre da totales bajos porque
#: no se pudo medir, no porque se comió menos. Sin este piso la tarjeta mide la captura y no
#: la comida — y le avisa "te falta proteína" justamente a quien escribe "cené milanesas".
_MACRO_MIN_COVERAGE = 0.6

#: Cuánto abajo tiene que estar hoy para que valga decirlo: por debajo del 70% de la base.
#: La tolerancia no es cosmética, es lo que separa una diferencia de una costumbre — la base
#: es un promedio de pocos días y oscila sola, así que un umbral pegado al 100% dispararía
#: la mitad de los días por ruido.
_MACRO_SHORTFALL_RATIO = 0.7

#: Cuánto tiene que tener un alimento por 100 g para que se lo pueda nombrar como fuente de
#: ese macro. Los dos números son el piso de "esto de verdad lo aporta": el catálogo tiene
#: 30 alimentos y sin piso el mejor candidato para "proteína" podía ser una manzana por ser
#: el único en la despensa.
_MACRO_CARRIER_PER_100G: dict[str, float] = {"protein_g": 10.0, "fiber_g": 3.0}

#: Por debajo de toda sección que reporta un hecho (0.85 la despensa, 0.8 el stock bajo):
#: esta reporta una **comparación**, armada sobre dos muestras chicas de la propia persona.
_MACRO_CONFIDENCE = 0.6


def _current_meal_type() -> str:
    """Infer meal type from the current hour in the household's timezone.

    This used to read the UTC hour, so at 08:00 in Buenos Aires (UTC-3) the
    engine believed it was 11:00 and suggested lunch at breakfast time.

    La zona sale de `app.core.clock`, que es el único lugar que lee
    `settings.timezone`: armándola acá, un `TIMEZONE` mal escrito hacía explotar la
    generación de sugerencias en vez de degradar a UTC como promete el resto.
    """
    hour = local_now().hour
    if 5 <= hour < 10:
        return "breakfast"
    if 10 <= hour < 14:
        return "lunch"
    if 14 <= hour < 18:
        return "snack"
    return "dinner"


#: Acá vivían `_get_recent_food_names` y `_get_pantry_items`, dos consultas escritas a
#: mano contra los modelos. La primera es ahora `context.recent_food_counts` —la misma
#: pregunta que `MealRepository.get_recent_foods_for_user` contestaba distinto, que es el
#: motivo por el que el contexto existe—; la segunda,
#: `PantryStockRepository.get_in_stock`, que además trae el alimento en la misma consulta
#: en vez de una por ítem.


def _get_disliked_names(user: User, preferences: list[RecommendationPreference]) -> set[str]:
    disliked: set[str] = set()
    # From User model fields
    for lst in (user.disliked_foods_json or [], user.dietary_restrictions_json or []):
        for item in lst:
            disliked.add(item.lower())
    # From explicit preferences
    for pref in preferences:
        if pref.item_type in ("food", "ingredient", "recipe") and pref.preference_signal in (
            "dislikes",
            "impossible",
            "avoid",
        ):
            disliked.add(pref.item_name.lower())
    return disliked


def generate(
    db: Session,
    user: User,
    preferences: list[RecommendationPreference],
    context: UserContext,
    meal_type: str | None = None,
    limit: int = _MAX_SUGGESTIONS,
) -> list[dict[str, Any]]:
    """Generate meal suggestions for *user*.

    Returns a list of suggestion dicts suitable for building Suggestion records.
    Each dict has: title, text, rationale, evidence_summary, confidence, source_type, category.

    `db` sigue haciendo falta —la despensa es del hogar y por eso no está en el contexto,
    que es personal— pero ya no se abren consultas acá: se piden por repositorio.
    """
    if meal_type is None:
        meal_type = _current_meal_type()

    recent_foods = context.recent_food_counts
    disliked = _get_disliked_names(user, preferences)
    pantry = PantryStockRepository(db).get_in_stock(user.household_id)

    suggestions: list[dict[str, Any]] = []

    # ── 1. Use-what-you-have: suggest meals from pantry items ─────────────
    pantry_names = []
    for stock in pantry:
        food: FoodItem | None = stock.food_item
        if food is None:
            continue
        name = food.canonical_name.lower()
        if name in disliked:
            continue
        pantry_names.append((name, float(stock.current_quantity), stock.unit))

    if pantry_names:
        # Sort by quantity ascending — use items before they run out
        pantry_names.sort(key=lambda x: x[1])
        featured = [n for n, _, _ in pantry_names[:5]]
        if featured:
            title = f"Use your {featured[0]} today"
            text = (
                f"You have {', '.join(featured)} in your pantry. "
                f"Consider incorporating them into {meal_type}."
            )
            freq_penalty = sum(recent_foods.get(f, 0) for f in featured)
            confidence = max(0.5, 0.85 - 0.05 * freq_penalty)
            suggestions.append(
                {
                    "category": "meal",
                    #: El sujeto es el alimento que encabeza la tarjeta, no la lista
                    #: entera: es el que da el título, y el que la persona acepta o
                    #: rechaza cuando toca el botón. Ver `recommendations/learning.py`.
                    "subject_type": "food",
                    "subject_name": featured[0],
                    #: La franja para la que se ofrece. La declara el generador —que ya la
                    #: tiene, y que además admite el override— en vez de que el scorer la
                    #: vuelva a derivar del reloj: dos copias de las ventanas horarias
                    #: discreparían justo cuando alguien pasa `meal_type` a mano. Es lo que
                    #: le permite al scorer distinguir el café del desayuno del de la cena.
                    "meal_type": meal_type,
                    "title": title,
                    "text": text,
                    "rationale": "Items available in pantry that should be used.",
                    "evidence_summary": (
                        f"Pantry items: {', '.join(featured)}. "
                        f"Recent frequency score: {freq_penalty}."
                    ),
                    "confidence": round(confidence, 3),
                    "source_type": "stock",
                }
            )

    # ── 2. Variety: suggest foods not eaten recently ──────────────────────
    all_recent = set(recent_foods.keys())
    variety_candidates = [n for n, _, _ in pantry_names if n not in all_recent]
    if variety_candidates:
        pick = variety_candidates[0]
        suggestions.append(
            {
                "category": "meal",
                "subject_type": "food",
                "subject_name": pick,
                "meal_type": meal_type,
                "title": f"Try {pick.title()} for variety",
                "text": (
                    f"You haven't had {pick} recently. "
                    f"It's available in your pantry — a good option for {meal_type}."
                ),
                "rationale": "Dietary variety supports micronutrient balance.",
                "evidence_summary": f"{pick} not consumed in past {RECENT_FOOD_DAYS} days.",
                "confidence": 0.7,
                "source_type": "rule",
            }
        )

    # ── 3. Preferred foods from explicit preferences ───────────────────────
    liked_prefs = [
        p
        for p in preferences
        if p.item_type in ("food", "ingredient", "recipe")
        and p.preference_signal in ("likes", "preferred")
    ]
    for pref in liked_prefs[:3]:
        name = pref.item_name.lower()
        if name in disliked:
            continue
        recent_count = recent_foods.get(name, 0)
        if recent_count >= 3:
            # Already eating it a lot — skip
            continue
        suggestions.append(
            {
                "category": "meal",
                "subject_type": "food",
                "subject_name": pref.item_name,
                "meal_type": meal_type,
                "title": f"Have {pref.item_name} today",
                "text": (
                    f"Based on your preferences, {pref.item_name} is a great option "
                    f"for {meal_type}."
                ),
                "rationale": "Matches explicit food preference.",
                "evidence_summary": (
                    f"User preference signal: {pref.preference_signal}. "
                    f"Recent occurrences: {recent_count}."
                ),
                "confidence": min(0.6 + float(pref.strength) * 0.3, 0.95),
                "source_type": "preference",
            }
        )

    # ── 4. Low-stock alert: use near-empty items ───────────────────────────
    for stock in pantry:
        if not stock.is_low:
            continue
        food = stock.food_item
        if food is None:
            continue
        name = food.canonical_name.lower()
        if name in disliked:
            continue
        suggestions.append(
            {
                "category": "meal",
                "subject_type": "food",
                "subject_name": food.canonical_name,
                "meal_type": meal_type,
                "title": f"Use your last {food.canonical_name}",
                "text": (
                    f"Your {food.canonical_name} stock is running low "
                    f"({stock.current_quantity} {stock.unit} remaining). "
                    "Consider using it before it goes bad."
                ),
                "rationale": "Item is near depletion — use to avoid waste.",
                "evidence_summary": (
                    f"Current stock: {stock.current_quantity} {stock.unit}. "
                    f"Threshold: {stock.low_stock_threshold}."
                ),
                "confidence": 0.8,
                "source_type": "stock",
            }
        )

    # ── 5. Hueco de macros: lo que hoy tiene menos que el propio promedio ──
    #: Va última, y no es indiferente: `unique[:limit]` corta por posición, así que una
    #: despensa con muchos ítems bajos puede tapar esta tarjeta. Se aguanta porque el
    #: problema no es de esta sección —las cinco no tienen prioridad declarada y la sección 4
    #: no tiene techo—, y porque estar última es lo que le permite mirar **todos** los
    #: sujetos ya tomados: es la única con libertad para elegir su sujeto, así que es la que
    #: puede ceder. `docs/v3-plan.md` anota la prioridad entre secciones como lo que falta.
    macro_card = _macro_gap_card(context, pantry, disliked, suggestions, meal_type)
    if macro_card is not None:
        suggestions.append(macro_card)

    # De-duplicate by title and respect limit
    seen_titles: set[str] = set()
    unique: list[dict[str, Any]] = []
    for s in suggestions:
        if s["title"] not in seen_titles:
            seen_titles.add(s["title"])
            unique.append(s)

    return unique[:limit]


def _macro_gap_card(
    context: UserContext,
    pantry: Sequence[PantryStock],
    disliked: set[str],
    taken: Sequence[dict[str, Any]],
    meal_type: str,
) -> dict[str, Any] | None:
    """La tarjeta del macro que hoy viene corto, o `None` —que es lo normal—.

    Es el primer lector de `context.macros_today` / `macros_baseline`: la 4.5.1 armó los
    totales con su cobertura y nadie los leía. Lo que hace es una sola afirmación, y la
    afirmación es una comparación de la persona contra sí misma a la misma hora del día.

    Cuatro cosas tienen que ser verdad para que diga algo, y cada una tapa una forma de
    mentir con un número:

    - **La base tiene que ser una base** (`_MACRO_MIN_BASELINE_DAYS` días registrados). Un
      solo día anotado no es un promedio.
    - **Las dos puntas tienen que estar medidas** (`_MACRO_MIN_COVERAGE`), o la tarjeta habla
      de lo que se pudo convertir a gramos y no de lo que se comió.
    - **La diferencia tiene que ser una diferencia** (`_MACRO_SHORTFALL_RATIO`).
    - **Algo en la despensa tiene que poder llenarlo.** Si no hay con qué, no hay tarjeta:
      un empujón que no se puede accionar es exactamente lo que esta app viene a dejar de
      ser. Y por eso también solo se avisa hacia abajo — ver `_MACRO_TRACKED`.

    El texto dice **los dos números** en vez de afirmar un déficit. "Te faltan 40 g" necesita
    un objetivo y la app no tiene ninguno; "hoy sumaste 22 y a esta hora venís en 58" es lo
    que efectivamente se midió, y deja que la persona decida si le importa.

    Un solo candidato por corrida aunque los dos macros estén cortos: son la misma comida.
    Dos tarjetas serían dos pedidos por la misma cena, y el desempate está declarado.
    """
    today = context.macros_today
    baseline = context.macros_baseline

    if baseline.days_counted < _MACRO_MIN_BASELINE_DAYS:
        return None
    if today.coverage < _MACRO_MIN_COVERAGE or baseline.coverage < _MACRO_MIN_COVERAGE:
        return None

    #: Los sujetos que ya se llevaron las secciones anteriores. Dos tarjetas con un mismo
    #: sujeto no son dos sugerencias: el de-dup final es **por título**, así que las dos
    #: sobreviven, se estorban en la lista y el feedback de una enseña sobre la otra.
    used = {str(card["subject_name"]).strip().lower() for card in taken}

    for field_name, label in _MACRO_TRACKED:
        base = float(getattr(baseline, field_name))
        so_far = float(getattr(today, field_name))
        if base <= 0.0 or so_far >= base * _MACRO_SHORTFALL_RATIO:
            continue
        carrier = _macro_carrier(pantry, field_name, disliked, used)
        if carrier is None:
            logger.debug(
                "Hueco de %s para el usuario %s: nada en la despensa que lo aporte.",
                label,
                context.user_id,
            )
            continue
        return {
            "category": "meal",
            "subject_type": "food",
            #: El sujeto es el alimento, no el macro: "proteína" no es algo que se acepte o
            #: se rechace, y `learning.SUBJECT_TYPES` no lo conoce. Lo que se aprende de un
            #: "no" acá es que las lentejas no van, que es información sobre la comida.
            "subject_name": carrier.canonical_name,
            "meal_type": meal_type,
            "title": f"Add {carrier.canonical_name} for {label}",
            "text": (
                f"So far today you logged {so_far:.0f} g of {label}; by this hour you "
                f"usually have {base:.0f} g. You have {carrier.canonical_name} in the "
                f"pantry, which is a good source."
            ),
            "rationale": f"Today's {label} is below this person's own average at this hour.",
            "evidence_summary": (
                f"{label.title()} today: {so_far:.1f} g over {today.items_counted} of "
                f"{today.items_total} logged items. Average to this hour: {base:.1f} g "
                f"over {baseline.days_counted} recorded days."
            ),
            "confidence": _MACRO_CONFIDENCE,
            "source_type": "rule",
        }
    return None


def _macro_carrier(
    pantry: Sequence[PantryStock],
    field_name: str,
    disliked: set[str],
    used: set[str],
) -> FoodItem | None:
    """El alimento de la despensa que mejor aporta ese macro, o `None`.

    El orden es **declarado** y no el que venga: mayor aporte por 100 g primero, y a igual
    aporte el nombre alfabético. Sin la segunda mitad la tarjeta cambiaría de alimento entre
    dos corridas idénticas según cómo ordene la base, que es el mismo bug que la 4.5.2
    encontró en `ExerciseTypeRepository.list_all`.
    """
    floor = _MACRO_CARRIER_PER_100G[field_name]
    carriers: list[tuple[FoodItem, float]] = []
    for stock in pantry:
        food = stock.food_item
        if food is None:
            continue
        name = food.canonical_name.strip().lower()
        if name in disliked or name in used:
            continue
        value = float(getattr(food, field_name) or 0.0)
        if value >= floor:
            carriers.append((food, value))
    if not carriers:
        return None
    carriers.sort(key=lambda pair: (-pair[1], pair[0].canonical_name.lower()))
    return carriers[0][0]
