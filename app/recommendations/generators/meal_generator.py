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

#: Los tres macros que esta app se permite nombrar, **en el orden en que desempatan**
#: cuando más de uno está corto. El orden está declarado y no derivado de nada, por la
#: misma razón que `activity_generator._ROTATION_PRIORITY`: un orden que sale del orden de
#: un `dict` se cambia sin querer al agregar una línea.
#:
#: Hasta la 7.6 la app no tenía objetivo de macros (`goals_json` y `target_weight_kg` no se
#: leían para esto), así que lo único afirmable era la comparación de la persona contra sí
#: misma, y solo hacia abajo: "hoy vas más liviano de proteína que tu promedio, y tenés
#: lentejas" propone algo; "hoy vas más pesado de grasa que tu promedio" no propone
#: nada —no hay nada que agregar, solo algo que dejar de comer—, y eso es consejo dietario
#: sin objetivo. Es la misma escala de un solo lado que
#: `activity_generator._HIGH_EFFORT_RPE`.
#:
#: La 7.6 agrega `User.goal_protein_g`/`goal_fiber_g`/`goal_calories_kcal` (migración
#: `0005`), y con un objetivo declarado la comparación deja de ser contra uno mismo — ver
#: `_MACRO_GOAL_ATTR` y `_macro_gap_card`. Pero **calorías sigue sin base propia**
#: (`_MACRO_BASELINE_ELIGIBLE` no la incluye): sin objetivo declarado, "hoy comiste menos
#: calorías que tu promedio" tiene el mismo problema de siempre — no hay nada que agregar
#: sin saber si el promedio mismo alcanza —, así que calorías solo se afirma cuando la
#: persona puso un número.
#:
#: Proteína primero porque es la que una comida mueve más, fibra segunda porque suele venir
#: con las verduras que ya arrastra cualquier tarjeta de despensa, y calorías última porque
#: es la más nueva y la que menos gente declara.
_MACRO_TRACKED: tuple[tuple[str, str], ...] = (
    ("protein_g", "protein"),
    ("fiber_g", "fiber"),
    ("calories", "calories"),
)

#: Qué macros pueden compararse contra el propio promedio cuando no hay objetivo
#: declarado. Calorías queda afuera a propósito — ver el comentario de `_MACRO_TRACKED`.
_MACRO_BASELINE_ELIGIBLE: frozenset[str] = frozenset({"protein_g", "fiber_g"})

#: El atributo de `UserContext` que trae el objetivo declarado de cada macro. Están ahí y
#: no en `MacroTotals` porque un objetivo no es algo que se mida, es algo que se declaró
#: una vez en `/profile/` — `MacroTotals` es siempre el resultado de sumar filas.
_MACRO_GOAL_ATTR: dict[str, str] = {
    "protein_g": "goal_protein_g",
    "fiber_g": "goal_fiber_g",
    "calories": "goal_calories_kcal",
}

#: El nombre del macro en `MacroTotals` no siempre es el nombre en `FoodItem`: proteína y
#: fibra se llaman igual de los dos lados por casualidad, pero el total del día es
#: `calories` y el aporte de un alimento es `calories_per_100g`. Sin este mapa,
#: `_macro_carrier` necesitaría un `if` propio para calorías en vez de leer con el mismo
#: `getattr` que usa para los otros dos.
_MACRO_FOOD_FIELD: dict[str, str] = {
    "protein_g": "protein_g",
    "fiber_g": "fiber_g",
    "calories": "calories_per_100g",
}

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
#: ese macro. Los tres números son el piso de "esto de verdad lo aporta": el catálogo tiene
#: 30 alimentos y sin piso el mejor candidato para "proteína" podía ser una manzana por ser
#: el único en la despensa. Los 200 kcal/100 g de calorías son el mismo criterio que usa la
#: nutrición para "denso en energía" — abajo de eso, sugerirlo como fuente de calorías sería
#: nombrar cualquier alimento de la despensa.
_MACRO_CARRIER_PER_100G: dict[str, float] = {
    "protein_g": 10.0,
    "fiber_g": 3.0,
    "calories": 200.0,
}

#: La unidad en la que se dice cada macro. Separado del nombre del campo porque `calories`
#: no tiene el `_g` que delataría la unidad de los otros dos.
_MACRO_UNIT: dict[str, str] = {"protein_g": "g", "fiber_g": "g", "calories": "kcal"}

#: La escalera de confianza de las cinco secciones, junta y en orden. Estaban sueltas dentro
#: de cada `dict` y el comentario de `_MACRO_CONFIDENCE` repetía dos de memoria ("0.85 la
#: despensa, 0.8 el stock bajo"), que es la forma en que dos números se separan: mover uno
#: dejaba el comentario mintiendo sin que nada fallara.
#:
#: Lo que ordena la escalera es qué tan directo es el hecho que la tarjeta afirma. La
#: despensa reporta una cantidad medida; el stock bajo también, pero contra un umbral que
#: puso una persona y no una medición; la variedad reporta una **ausencia**, que es más fácil
#: de tener por una captura incompleta que por no haber comido; y el hueco de macros reporta
#: una **comparación**, armada sobre dos muestras chicas de la propia persona.
#:
#: Ninguno se descuenta acá por nada aprendido: eso lo hace el scorer, que es el único que ve
#: las señales. Hasta la 4.5.8 esta sección se lo descontaba sola —
#: `max(0.5, 0.85 - 0.05 * freq_penalty)`— y era el mismo eje de saciedad
#: (`scorer._SATIETY_PENALTY`) escrito dos veces con dos números que no se conocían: un
#: alimento de todos los días perdía hasta 0.35 acá **más** 0.15 allá, con dos ventanas
#: distintas y, del lado de acá, sin decaimiento —un escalón plano de siete días que no se
#: apagaba nunca—. Quedó el que decae.
_PANTRY_CONFIDENCE = 0.85
_LOW_STOCK_CONFIDENCE = 0.8
_VARIETY_CONFIDENCE = 0.7
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


def _recency_phrase(count: int, name: str) -> str:
    """Cómo se nombra la frecuencia reciente de un alimento, sin caer en un plural inglés.

    "it appears 1 times" es la clase de detalle que hace que un texto se lea como generado, y
    `ngettext` no sirve acá: el `rationale` se arma dentro de un job, sin locale de request
    (ver `app/recommendations/explain.py`). La forma con "of the foods you logged" funciona
    igual para 0, 1 y 12, y además es la cuenta que el generador de verdad tiene —
    `context.recent_food_counts` cuenta apariciones, no comidas.
    """
    if count == 0:
        return f"{name} does not appear in your last {RECENT_FOOD_DAYS} days of logged food"
    return (
        f"{name} accounts for {count} of the foods you logged in the last "
        f"{RECENT_FOOD_DAYS} days"
    )


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

    **Dónde termina este generador y dónde empieza el scorer**, que es la línea que la 4.5.8
    vino a poner: acá se decide *si hay algo que decir* y el scorer decide *cuánto compite*.
    `recent_foods` se sigue leyendo en las secciones 2 y 3, y eso no es la duplicación que se
    sacó: la sección 2 lo usa para **elegir** el alimento del que la tarjeta puede afirmar
    "hace tiempo que no comés esto" —sin eso la tarjeta sería falsa, no floja— y la 3 lo usa
    para callarse cuando un favorito ya se come todos los días, porque ahí el empujón no
    aporta nada. Lo que se fue era otra cosa: un descuento de **confianza** por frecuencia,
    que es exactamente el trabajo del eje de saciedad y estaba escrito dos veces.
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
            #: Del alimento que encabeza, no de los cinco. Antes era la **suma** sobre los
            #: cinco destacados, y con eso la frase del `rationale` —"y {esto} aparece N
            #: veces"— afirmaba sobre el sujeto un total de otras cuatro cosas: con cinco
            #: ítems normales podía decir "12" de algo que la persona no comió nunca.
            recent_count = recent_foods.get(featured[0], 0)
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
                    #: Por qué **esta** tarjeta: la despensa se ordena por cantidad
                    #: ascendente, así que el ítem que la encabeza es el que menos queda. Eso
                    #: es la regla de selección con su valor real; los gramos y el umbral van
                    #: en `evidence_summary`, que es el rastro auditable.
                    "rationale": (
                        f"{featured[0]} has the least left of anything in your pantry, and "
                        f"{_recency_phrase(recent_count, 'it')}."
                    ),
                    #: "Recent frequency score: 3" nombraba un puntaje que no existe —era una
                    #: cuenta— y encima ya no se usa para nada: la recencia la cobra el
                    #: scorer. Queda la cuenta, dicha como cuenta y con el sujeto nombrado,
                    #: igual que en la sección 3.
                    "evidence_summary": (
                        f"Pantry items: {', '.join(featured)}. "
                        f"Recent occurrences of {featured[0]}: {recent_count}."
                    ),
                    "confidence": _PANTRY_CONFIDENCE,
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
                "rationale": f"{_recency_phrase(0, pick)}, and it is in stock.",
                "evidence_summary": f"{pick} not consumed in past {RECENT_FOOD_DAYS} days.",
                "confidence": _VARIETY_CONFIDENCE,
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
                "rationale": (
                    f"You marked {pref.item_name} as '{pref.preference_signal}' with "
                    f"strength {float(pref.strength):.1f}, and "
                    f"{_recency_phrase(recent_count, 'it')}."
                ),
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
                "rationale": (
                    f"The low-stock threshold set for {food.canonical_name} is "
                    f"{stock.low_stock_threshold} {stock.unit}, and it is under it."
                ),
                "evidence_summary": (
                    f"Current stock: {stock.current_quantity} {stock.unit}. "
                    f"Threshold: {stock.low_stock_threshold}."
                ),
                "confidence": _LOW_STOCK_CONFIDENCE,
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
    afirmación es una comparación — contra el objetivo que la persona declaró en
    `/profile/` cuando existe (7.6), y contra sí misma a la misma hora del día cuando no.

    Cuatro cosas tienen que ser verdad para que diga algo, y cada una tapa una forma de
    mentir con un número:

    - **Tiene que haber algo contra qué comparar**: un objetivo declarado
      (`_MACRO_GOAL_ATTR`) o, si no hay, una base que sea base
      (`_MACRO_MIN_BASELINE_DAYS` días registrados, y solo para los macros de
      `_MACRO_BASELINE_ELIGIBLE`). Un solo día anotado no es un promedio, y sin objetivo
      ni promedio no hay con qué medir.
    - **Lo de hoy tiene que estar medido** (`_MACRO_MIN_COVERAGE`), o la tarjeta habla de
      lo que se pudo convertir a gramos y no de lo que se comió. La base, además, cuando
      es la base la que se usa.
    - **La diferencia tiene que ser una diferencia** (`_MACRO_SHORTFALL_RATIO`).
    - **Algo en la despensa tiene que poder llenarlo.** Si no hay con qué, no hay tarjeta:
      un empujón que no se puede accionar es exactamente lo que esta app viene a dejar de
      ser. Y por eso también solo se avisa hacia abajo — ver `_MACRO_TRACKED`.

    El texto dice **los dos números** en vez de afirmar un déficit en abstracto: contra el
    objetivo cuando hay uno declarado ("tu objetivo es 120 g, hoy sumaste 58"), o contra el
    propio promedio cuando no ("hoy sumaste 22 y a esta hora venís en 58"). Deja que la
    persona decida si le importa.

    Un solo candidato por corrida aunque más de un macro esté corto: son la misma comida.
    Dos tarjetas serían dos pedidos por la misma cena, y el desempate está declarado.
    """
    today = context.macros_today
    baseline = context.macros_baseline

    if today.coverage < _MACRO_MIN_COVERAGE:
        return None
    baseline_ready = (
        baseline.days_counted >= _MACRO_MIN_BASELINE_DAYS
        and baseline.coverage >= _MACRO_MIN_COVERAGE
    )

    #: Los sujetos que ya se llevaron las secciones anteriores. Dos tarjetas con un mismo
    #: sujeto no son dos sugerencias: el de-dup final es **por título**, así que las dos
    #: sobreviven, se estorban en la lista y el feedback de una enseña sobre la otra.
    used = {str(card["subject_name"]).strip().lower() for card in taken}

    for field_name, label in _MACRO_TRACKED:
        goal = getattr(context, _MACRO_GOAL_ATTR[field_name])
        if goal is not None:
            target = float(goal)
            from_goal = True
        elif field_name in _MACRO_BASELINE_ELIGIBLE and baseline_ready:
            target = float(getattr(baseline, field_name))
            from_goal = False
        else:
            continue

        so_far = float(getattr(today, field_name))
        if target <= 0.0 or so_far >= target * _MACRO_SHORTFALL_RATIO:
            continue
        carrier = _macro_carrier(pantry, field_name, disliked, used)
        if carrier is None:
            logger.debug(
                "Hueco de %s para el usuario %s: nada en la despensa que lo aporte.",
                label,
                context.user_id,
            )
            continue

        unit = _MACRO_UNIT[field_name]
        if from_goal:
            target_phrase = f"your goal is {target:.0f} {unit}"
            comparison = "goal"
            evidence_target = f"Goal: {target:.1f} {unit} per day."
        else:
            target_phrase = f"you usually have {target:.0f} {unit}"
            comparison = "own average at this hour"
            evidence_target = (
                f"Average to this hour: {target:.1f} {unit} over "
                f"{baseline.days_counted} recorded days."
            )
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
                f"So far today you logged {so_far:.0f} {unit} of {label}; {target_phrase}. "
                f"You have {carrier.canonical_name} in the pantry, which is a good source."
            ),
            #: La 4.5.3 ya la dejó computada, pero decía "this person" —tercera persona en una
            #: frase que la persona lee— y repetía lo que el texto ya afirma. Lo que agrega
            #: ahora es la **selección**: cuál macro ganó el desempate declarado y por qué
            #: este alimento y no otro de la despensa.
            "rationale": (
                f"{label.title()} is the first tracked macro running below your {comparison}, "
                f"and {carrier.canonical_name} is a source of it you already have."
            ),
            "evidence_summary": (
                f"{label.title()} today: {so_far:.1f} {unit} over {today.items_counted} of "
                f"{today.items_total} logged items. {evidence_target}"
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
        value = float(getattr(food, _MACRO_FOOD_FIELD[field_name]) or 0.0)
        if value >= floor:
            carriers.append((food, value))
    if not carriers:
        return None
    carriers.sort(key=lambda pair: (-pair[1], pair[0].canonical_name.lower()))
    return carriers[0][0]
