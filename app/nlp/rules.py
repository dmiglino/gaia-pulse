"""Rule-based NLP parser — Layer 1.

Handles ~80%+ of common GaiaPulse inputs via regex + keyword matching.
Operates fully offline and synchronously.

Supports:
- Meal logging with per-user item attribution
- Workout logging with exercise extraction
- Body metric logging
- Pantry add/consume
- Preference updates
- Mixed intents

Usage:
    from app.nlp.rules import parse
    result = parse("We bought 6 bananas", speaking_user="diego")
"""

from __future__ import annotations

import re
from typing import Any

from app.nlp.intents import (
    BodyMetricIntent,
    ExerciseRef,
    FoodItemRef,
    MealIntent,
    ParseResult,
    ParsedIntent,
    PreferenceIntent,
    StockAddIntent,
    StockConsumeIntent,
    StockItemRef,
    WorkoutIntent,
)

# ---------------------------------------------------------------------------
# User name resolution
# ---------------------------------------------------------------------------

_DIEGO_PATTERNS = re.compile(
    r"\b(diego)\b",
    re.IGNORECASE,
)
_ROCIO_PATTERNS = re.compile(
    r"\b(roc[íi]o|roci)\b",
    re.IGNORECASE,
)
_WE_PATTERNS = re.compile(
    r"\b(we|both|us|nosotros)\b",
    re.IGNORECASE,
)
_I_PATTERNS = re.compile(
    r"\b(i|yo|me)\b",
    re.IGNORECASE,
)


def _resolve_participants(text: str, speaking_user: str) -> list[str]:
    """Return list of user keys inferred from text."""
    has_diego = bool(_DIEGO_PATTERNS.search(text))
    has_rocio = bool(_ROCIO_PATTERNS.search(text))
    has_we = bool(_WE_PATTERNS.search(text))
    has_i = bool(_I_PATTERNS.search(text))

    if has_we:
        return ["both"]
    if has_diego and has_rocio:
        return ["both"]
    if has_diego:
        return ["diego"]
    if has_rocio:
        return ["rocio"]
    if has_i:
        return [speaking_user]
    return [speaking_user]


# ---------------------------------------------------------------------------
# Quantity / unit parsing
# ---------------------------------------------------------------------------

_NUMBER_WORDS: dict[str, float] = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "half": 0.5,
    "un": 1,
    "una": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
}

# Regex: optional leading number, then optional unit, then item name
_QTY_UNIT_ITEM = re.compile(
    r"""
    (?P<qty>
        \d+(?:[.,]\d+)?       # plain number
        |half                  # "half"
        |a\b|an\b              # "a" / "an"
    )?
    \s*
    (?P<unit>
        kg|g\b|grams?|gram\b
        |ml|milliliters?
        |l\b|liters?|litres?
        |oz|ounces?
        |lbs?|pounds?
        |cups?|tablespoons?|tbsp|teaspoons?|tsp
        |units?|pieces?|pcs?
        |slices?
        |servings?
        |portions?
    )?
    \s*
    (?:of\s+)?
    (?P<name>[a-záéíóúüñ][a-záéíóúüñ\s\-]+?)
    (?=\s*(?:,|and\b|$|\bwith\b|\bfor\b|\bof\b|\+))
    """,
    re.IGNORECASE | re.VERBOSE,
)

_UNIT_NORMALISE: dict[str, str] = {
    "gram": "g",
    "grams": "g",
    "kilogram": "kg",
    "kilograms": "kg",
    "milliliter": "ml",
    "milliliters": "ml",
    "millilitre": "ml",
    "millilitres": "ml",
    "liter": "l",
    "liters": "l",
    "litre": "l",
    "litres": "l",
    "ounce": "oz",
    "ounces": "oz",
    "pound": "lb",
    "pounds": "lb",
    "lbs": "lb",
    "unit": "unit",
    "units": "unit",
    "piece": "unit",
    "pieces": "unit",
    "pcs": "unit",
    "pc": "unit",
    "slice": "slice",
    "slices": "slice",
    "cup": "cup",
    "cups": "cup",
    "tablespoon": "tbsp",
    "tablespoons": "tbsp",
    "teaspoon": "tsp",
    "teaspoons": "tsp",
    "serving": "serving",
    "servings": "serving",
    "portion": "serving",
    "portions": "serving",
}


def _normalise_unit(raw: str | None) -> str | None:
    if not raw:
        return None
    return _UNIT_NORMALISE.get(raw.lower(), raw.lower())


def _parse_qty(raw: str | None) -> float | None:
    if not raw:
        return None
    raw = raw.strip().lower()
    if raw in _NUMBER_WORDS:
        return _NUMBER_WORDS[raw]
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return None


def _extract_items(text: str) -> list[FoodItemRef]:
    """Extract food items with optional quantity/unit from a text segment."""
    items: list[FoodItemRef] = []
    # Split on commas and 'and' to tokenize
    segments = re.split(r",\s*|\band\b", text, flags=re.IGNORECASE)
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        m = _QTY_UNIT_ITEM.match(seg)
        if m:
            qty = _parse_qty(m.group("qty"))
            unit = _normalise_unit(m.group("unit"))
            raw_name = m.group("name").strip() if m.group("name") else seg
            # Attempt basic depluralization: only strip trailing 's' if word ends in 's' but not 'ss'
            if raw_name.endswith("s") and not raw_name.endswith("ss") and len(raw_name) > 3:
                name = raw_name[:-1]
            else:
                name = raw_name
            # Avoid capturing stopwords alone
            if len(name) < 2 or name.lower() in {"we", "i", "you", "they", "he", "she"}:
                continue
            items.append(FoodItemRef(food_name=name, qty=qty, unit=unit))
        else:
            # Fallback: plain name
            name = seg.strip()
            if len(name) >= 2 and name.lower() not in {"we", "i", "you", "they", "he", "she"}:
                items.append(FoodItemRef(food_name=name))
    return items


def _extract_stock_items(text: str) -> list[StockItemRef]:
    raw = _extract_items(text)
    return [StockItemRef(food_name=r.food_name, quantity=r.qty, unit=r.unit) for r in raw]


# ---------------------------------------------------------------------------
# Time / meal-type inference
# ---------------------------------------------------------------------------

_TIME_REFS = re.compile(
    r"\b(today|tonight|yesterday|this\s+morning|this\s+afternoon|this\s+evening"
    r"|last\s+night|mañana|hoy|ayer|esta\s+noche|esta\s+mañana)\b",
    re.IGNORECASE,
)

_MEAL_TYPE_MAP: dict[str, str] = {
    "breakfast": "breakfast",
    "desayuno": "breakfast",
    "lunch": "lunch",
    "almuerzo": "lunch",
    "dinner": "dinner",
    "cena": "dinner",
    "supper": "dinner",
    "snack": "snack",
    "merienda": "snack",
    "brunch": "brunch",
}

_MEAL_TYPE_RE = re.compile(
    r"\b(" + "|".join(_MEAL_TYPE_MAP.keys()) + r")\b",
    re.IGNORECASE,
)

_TIME_MEAL_MAP: dict[str, str] = {
    "this morning": "breakfast",
    "esta mañana": "breakfast",
    "this afternoon": "lunch",
    "tonight": "dinner",
    "esta noche": "dinner",
    "last night": "dinner",
}


def _infer_meal_type(text: str) -> str:
    m = _MEAL_TYPE_RE.search(text)
    if m:
        return _MEAL_TYPE_MAP[m.group(1).lower()]
    for phrase, meal in _TIME_MEAL_MAP.items():
        if phrase in text.lower():
            return meal
    return "other"


def _extract_time_ref(text: str) -> str | None:
    m = _TIME_REFS.search(text)
    return m.group(1).lower() if m else None


# ---------------------------------------------------------------------------
# Exercise extraction
# ---------------------------------------------------------------------------

_EXERCISE_MAP: dict[str, tuple[str, str | None]] = {
    # name: (canonical_name, muscle_group)
    "gym": ("gym", None),
    "biking": ("biking", "cardio"),
    "bike": ("biking", "cardio"),
    "cycling": ("cycling", "cardio"),
    "yoga": ("yoga", None),
    "running": ("running", "cardio"),
    "run": ("running", "cardio"),
    "jogging": ("jogging", "cardio"),
    "swimming": ("swimming", "cardio"),
    "swim": ("swimming", "cardio"),
    "weights": ("weight training", None),
    "chest": ("chest press", "chest"),
    "shoulders": ("shoulder press", "shoulders"),
    "triceps": ("triceps", "triceps"),
    "biceps": ("biceps", "biceps"),
    "back": ("back", "back"),
    "legs": ("legs", "legs"),
    "squats": ("squats", "legs"),
    "deadlift": ("deadlift", "back"),
    "deadlifts": ("deadlift", "back"),
    "bench": ("bench press", "chest"),
    "pull-ups": ("pull-ups", "back"),
    "pullups": ("pull-ups", "back"),
    "push-ups": ("push-ups", "chest"),
    "pushups": ("push-ups", "chest"),
    "abs": ("abs", "core"),
    "core": ("core", "core"),
    "cardio": ("cardio", "cardio"),
    "pilates": ("pilates", None),
    "crossfit": ("crossfit", None),
    "hiit": ("hiit", "cardio"),
    "zumba": ("zumba", "cardio"),
    "spinning": ("spinning", "cardio"),
    "rowing": ("rowing", "back"),
    "boxing": ("boxing", None),
    "martial arts": ("martial arts", None),
    "dance": ("dance", "cardio"),
    "hiking": ("hiking", "cardio"),
    "walking": ("walking", "cardio"),
    "elliptical": ("elliptical", "cardio"),
    "treadmill": ("treadmill", "cardio"),
}

_EXERCISE_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(_EXERCISE_MAP, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

_DURATION_RE = re.compile(
    r"\b(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>hours?|hrs?|h\b|minutes?|mins?|m\b)\b",
    re.IGNORECASE,
)

_WORKOUT_TYPE_MAP: dict[str, str] = {
    "gym": "gym",
    "biking": "outdoor",
    "bike": "outdoor",
    "cycling": "outdoor",
    "yoga": "home",
    "pilates": "home",
    "running": "outdoor",
    "swimming": "outdoor",
    "crossfit": "gym",
    "hiit": "home",
    "zumba": "home",
    "spinning": "gym",
    "hiking": "outdoor",
    "walking": "outdoor",
}


def _extract_duration_minutes(text: str) -> int | None:
    m = _DURATION_RE.search(text)
    if not m:
        return None
    val = float(m.group("val"))
    unit = m.group("unit").lower()
    if unit.startswith("h"):
        return int(val * 60)
    return int(val)


def _extract_exercises(text: str) -> list[ExerciseRef]:
    found: list[ExerciseRef] = []
    seen: set[str] = set()
    for m in _EXERCISE_RE.finditer(text):
        key = m.group(1).lower()
        canonical, muscle = _EXERCISE_MAP.get(key, (key, None))
        if canonical not in seen:
            seen.add(canonical)
            found.append(ExerciseRef(name=canonical, muscle_group=muscle))
    return found


def find_known_activities(text: str) -> list[str]:
    """Los nombres canónicos de actividad que aparecen en *text*, sin duplicados.

    Público —el único de este módulo además de `parse`— porque el aprendizaje necesita
    reconocer una actividad dentro de una frase que **no** es una captura: el motivo de
    texto libre con el que se rechaza una sugerencia ("hoy no, el yoga nos aburre"). Lo que se
    reutiliza es el matcher exacto de `_EXERCISE_MAP`, que compara contra nombres conocidos
    con `\\b`; lo que deliberadamente **no** se reutiliza es `_parse_preference`, que después
    de sacar la negación se queda con las tres primeras palabras y por lo tanto de *"no es
    para nosotros"* deduciría una actividad llamada "para nosotros".

    El límite que hereda de ese mapa, y que conviene saber al leer los nombres que salen:
    **las claves están en inglés**, así que de una frase en castellano solo aparecen las que
    se escriben igual en los dos idiomas. Son bastantes, porque el castellano rioplatense
    toma prestados estos nombres —"yoga", "pilates", "spinning", "crossfit", "cardio",
    "running", "hiit", "zumba", "core", "gym"— pero la contracara es la que importa: lo que
    la casa escribiría en castellano y el mapa **no** conoce no aparece. *"Odio correr"*,
    *"caminar"*, *"pesas"*, *"natación"* no devuelven nada. Ensanchar el mapa cambiaría
    también lo que reconoce una captura, así que no se hace de contrabando acá; la 4.5
    reemplaza esta lista fija por `ExerciseType`, que es donde los nombres ya viven en la
    base.

    Devuelve solo el nombre, no el `ExerciseRef`: el grupo muscular que `_EXERCISE_MAP`
    también sabe queda afuera a propósito, porque deducir de una frase un veto a un grupo
    muscular entero es un salto que el texto no autoriza —generalizar es tarea del nivel
    atributo, con su propia vara de evidencia—.
    """
    return [exercise.name for exercise in _extract_exercises(text)]


def _infer_workout_type(text: str, exercises: list[ExerciseRef]) -> str | None:
    tl = text.lower()
    for kw, wtype in _WORKOUT_TYPE_MAP.items():
        if kw in tl:
            return wtype
    if exercises:
        return "gym"
    return None


# ---------------------------------------------------------------------------
# Keyword triggers
# ---------------------------------------------------------------------------

_MEAL_TRIGGERS = re.compile(
    r"\b(ate|eat|eaten|had|having|breakfast|lunch|dinner|snack|meal|food|comió|comimos|"
    r"desayunó|almorzó|cenó|desayunamos|almorzamos|cenamos|about to have|going to eat|"
    r"just ate|just had)\b",
    re.IGNORECASE,
)

_WORKOUT_TRIGGERS = re.compile(
    r"\b(went to the gym|trained|training|workout|worked out|exercised|rode|"
    r"went for a (run|bike|swim|walk|ride)|gym|biked|ran|swam|walked|did yoga|"
    r"did pilates|did hiit|went hiking)\b",
    re.IGNORECASE,
)

_BODY_METRIC_TRIGGERS = re.compile(
    r"\b(weigh|weight|weighed|peso|I weigh|I am|my weight|body fat|waist|sleep|slept|"
    r"body mass|bmi)\b",
    re.IGNORECASE,
)

_STOCK_ADD_TRIGGERS = re.compile(
    r"\b(bought|buy|purchased|got|added|picked up|we have|compramos|compraron|"
    r"I got|we got)\b",
    re.IGNORECASE,
)

_STOCK_CONSUME_TRIGGERS = re.compile(
    r"\b(used|consumed|finished|ran out|used up|usamos|gastamos|we used|I used)\b",
    re.IGNORECASE,
)

_PREFERENCE_NEG_TRIGGERS = re.compile(
    r"\b(don'?t|do not|no|never|can'?t|cannot|hate|dislike|avoid|not suggest|"
    r"no me gusta|no nos gusta|imposible|no podemos)\b",
    re.IGNORECASE,
)

_PREFERENCE_POS_TRIGGERS = re.compile(
    r"\b(like|love|enjoy|prefer|we like|I like|we love|I love|nos gusta|"
    r"me gusta|preferimos)\b",
    re.IGNORECASE,
)

_SUGGEST_WORD = re.compile(r"\b(suggest|recommend|sugerir)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Per-user food extraction (split by user mention)
# ---------------------------------------------------------------------------

def _split_by_user(text: str) -> dict[str, str]:
    """Split a sentence like 'Rocío ate X, Diego ate Y' into per-user segments."""
    # Patterns: "<name> ate/had/…" OR "Diego: …"
    pattern = re.compile(
        r"(?:^|,\s*)"
        r"(?P<user>diego|roc[íi]o|roci|we|both)\s*"
        r"(?:ate|had|having|eat|eaten|comió|tomó|:)\s*"
        r"(?P<items>.+?)(?=,\s*(?:diego|roc[íi]o|roci|we|both)\s*(?:ate|had|having|eat|comió)|$)",
        re.IGNORECASE,
    )
    result: dict[str, str] = {}
    for m in pattern.finditer(text):
        user_raw = m.group("user").lower()
        if re.match(r"roc[íi]o|roci", user_raw):
            user_key = "rocio"
        elif user_raw == "diego":
            user_key = "diego"
        else:
            user_key = "both"
        result[user_key] = m.group("items").strip()
    return result


def _build_items_per_user(
    text: str,
    participants: list[str],
    speaking_user: str,
) -> dict[str, list[FoodItemRef]]:
    """Build the items_per_user dict for a MealIntent."""
    per_user = _split_by_user(text)
    if per_user:
        return {u: _extract_items(seg) for u, seg in per_user.items()}

    # No per-user split found — all items go to all participants
    # Strip leading trigger words before extracting
    stripped = re.sub(
        r"^.*?(?:ate|had|having|eat|eaten|comió|comimos|about to have|going to eat|just ate|just had)\s*",
        "",
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    # Also strip meal type words and time refs from the front
    stripped = _MEAL_TYPE_RE.sub("", stripped)
    stripped = _TIME_REFS.sub("", stripped)
    items = _extract_items(stripped)
    items_dict: dict[str, list[FoodItemRef]] = {}
    if participants == ["both"]:
        items_dict["both"] = items
    else:
        for p in participants:
            items_dict[p] = items
    return items_dict


# ---------------------------------------------------------------------------
# Preference intent parsing
# ---------------------------------------------------------------------------

_PREF_EXERCISE_WORDS = {
    "gym", "biking", "bike", "cycling", "yoga", "running", "swimming", "swim",
    "pilates", "crossfit", "hiit", "zumba", "spinning", "hiking", "walking",
    "weightlifting", "weights", "boxing", "dancing", "rowing",
}


def _classify_item_type(item_name: str) -> str:
    if item_name.lower() in _PREF_EXERCISE_WORDS:
        return "exercise"
    exercises = _extract_exercises(item_name)
    if exercises:
        return "exercise"
    return "food"


def _parse_preference(text: str, speaking_user: str) -> PreferenceIntent | None:
    """Parse a preference update statement."""
    participants = _resolve_participants(text, speaking_user)
    user_key = participants[0] if len(participants) == 1 else "both"

    is_neg = bool(_PREFERENCE_NEG_TRIGGERS.search(text))
    is_pos = bool(_PREFERENCE_POS_TRIGGERS.search(text))

    if not is_neg and not is_pos:
        return None

    has_suggest = bool(_SUGGEST_WORD.search(text))

    # Determine signal
    if is_neg:
        if has_suggest:
            signal = "avoid"
        else:
            # Could be impossible (e.g. "can't swim") or just dislike
            if re.search(r"\b(can'?t|cannot|impossible|imposible)\b", text, re.IGNORECASE):
                signal = "impossible"
            else:
                signal = "dislikes"
    else:
        signal = "likes"

    # Extract the item name
    # Try to strip negation + suggestion words to get the noun
    cleaned = re.sub(
        r"\b(don'?t|do not|please|no|never|not|suggest|recommend|we|i|like|love|"
        r"enjoy|prefer|avoid|dislike|hate|can'?t|cannot)\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(",.;!")

    exercises = _extract_exercises(cleaned)
    if exercises:
        item_name = exercises[0].name
        item_type = "exercise"
    else:
        # Use the first meaningful word chunk remaining
        words = [w for w in cleaned.split() if len(w) > 2]
        item_name = " ".join(words[:3]) if words else cleaned
        item_type = _classify_item_type(item_name)

    if not item_name:
        return None

    return PreferenceIntent(
        participants=[user_key],
        user_key=user_key,
        item_type=item_type,
        item_name=item_name,
        preference_signal=signal,
        confidence=0.75,
        raw_span=text,
    )


# ---------------------------------------------------------------------------
# Body metric parsing
# ---------------------------------------------------------------------------

_WEIGHT_RE = re.compile(
    r"\b(?P<val>\d+(?:[.,]\d+)?)\s*(?P<unit>kg|kilos?|kilograms?|lbs?|pounds?)\b",
    re.IGNORECASE,
)
_BODY_FAT_RE = re.compile(
    r"\b(?P<val>\d+(?:[.,]\d+)?)\s*%\s*(?:body\s*fat|bf|grasa)\b",
    re.IGNORECASE,
)
_WAIST_RE = re.compile(
    r"\bwaist\s*(?:is\s*)?(?P<val>\d+(?:[.,]\d+)?)\s*cm\b",
    re.IGNORECASE,
)
_SLEEP_RE = re.compile(
    r"\b(?:slept|sleep|dormí|dormimos)\s*(?:for\s*)?(?P<val>\d+(?:[.,]\d+)?)\s*(?:hours?|hrs?|h\b)\b",
    re.IGNORECASE,
)


def _parse_body_metric(text: str, speaking_user: str) -> BodyMetricIntent | None:
    participants = _resolve_participants(text, speaking_user)
    user_key = participants[0] if len(participants) == 1 else speaking_user

    weight_kg: float | None = None
    body_fat: float | None = None
    waist_cm: float | None = None
    sleep_h: float | None = None

    mw = _WEIGHT_RE.search(text)
    if mw:
        val = float(mw.group("val").replace(",", "."))
        unit = mw.group("unit").lower()
        if unit.startswith("lb") or unit.startswith("pound"):
            val = round(val * 0.453592, 2)
        weight_kg = val

    mbf = _BODY_FAT_RE.search(text)
    if mbf:
        body_fat = float(mbf.group("val").replace(",", "."))

    mw2 = _WAIST_RE.search(text)
    if mw2:
        waist_cm = float(mw2.group("val").replace(",", "."))

    ms = _SLEEP_RE.search(text)
    if ms:
        sleep_h = float(ms.group("val").replace(",", "."))

    if weight_kg is None and body_fat is None and waist_cm is None and sleep_h is None:
        return None

    confidence = 0.85 if weight_kg is not None else 0.7

    return BodyMetricIntent(
        participants=[user_key],
        user_key=user_key,
        weight_kg=weight_kg,
        body_fat_pct=body_fat,
        waist_cm=waist_cm,
        sleep_hours=sleep_h,
        confidence=confidence,
        raw_span=text,
    )


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------


def _confidence_from_items(items_per_user: dict[str, list[FoodItemRef]]) -> float:
    total = sum(len(v) for v in items_per_user.values())
    if total == 0:
        return 0.4
    if total >= 3:
        return 0.85
    return 0.7


def parse(text: str, speaking_user: str = "diego") -> ParseResult:
    """Entry point for Layer 1 rule-based parsing.

    Args:
        text: Natural language input from the user.
        speaking_user: Key of the user who typed/spoke ("diego" or "rocio").

    Returns:
        ParseResult with zero or more intents and an overall_confidence.
    """
    text = text.strip()
    if not text:
        return ParseResult(raw_text=text, overall_confidence=0.0)

    intents: list[Any] = []

    is_meal = bool(_MEAL_TRIGGERS.search(text))
    is_workout = bool(_WORKOUT_TRIGGERS.search(text))
    is_metric = bool(_BODY_METRIC_TRIGGERS.search(text))
    is_stock_add = bool(_STOCK_ADD_TRIGGERS.search(text))
    is_stock_consume = bool(_STOCK_CONSUME_TRIGGERS.search(text))
    is_pref = bool(_PREFERENCE_NEG_TRIGGERS.search(text) or _PREFERENCE_POS_TRIGGERS.search(text))

    # ── Preference ──────────────────────────────────────────────────────────
    if is_pref:
        pref = _parse_preference(text, speaking_user)
        if pref:
            intents.append(pref)

    # ── Body metric ─────────────────────────────────────────────────────────
    if is_metric:
        metric = _parse_body_metric(text, speaking_user)
        if metric:
            intents.append(metric)

    # ── Workout ─────────────────────────────────────────────────────────────
    if is_workout and not is_meal:
        participants = _resolve_participants(text, speaking_user)
        exercises = _extract_exercises(text)
        duration = _extract_duration_minutes(text)
        workout_type = _infer_workout_type(text, exercises)

        # If "gym" is in text, add it as workout_type even if no specific exercises found
        if re.search(r"\bgym\b", text, re.IGNORECASE):
            workout_type = "gym"

        confidence = 0.5
        if exercises:
            confidence += 0.25
        if duration:
            confidence += 0.15
        if workout_type:
            confidence += 0.1

        intents.append(
            WorkoutIntent(
                participants=participants,
                workout_type=workout_type,
                duration_minutes=duration,
                exercises=exercises,
                confidence=min(confidence, 1.0),
                raw_span=text,
            )
        )

    # ── Meal ────────────────────────────────────────────────────────────────
    if is_meal and not is_workout:
        participants = _resolve_participants(text, speaking_user)
        meal_type = _infer_meal_type(text)
        time_ref = _extract_time_ref(text)
        items_per_user = _build_items_per_user(text, participants, speaking_user)

        # Handle "around X grams each" pattern
        qty_each_match = re.search(
            r"around\s+(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>grams?|g\b|kg|ml|l)\s+each\b",
            text,
            re.IGNORECASE,
        )
        if qty_each_match:
            qty_val = float(qty_each_match.group("val"))
            qty_unit = _normalise_unit(qty_each_match.group("unit"))
            # Apply this quantity to all items that have none
            for items in items_per_user.values():
                for item in items:
                    if item.qty is None:
                        item.qty = qty_val
                        item.unit = qty_unit

        confidence = _confidence_from_items(items_per_user)
        if meal_type != "other":
            confidence = min(confidence + 0.05, 1.0)

        intents.append(
            MealIntent(
                participants=participants,
                meal_type=meal_type,
                time_reference=time_ref,
                items_per_user=items_per_user,
                confidence=confidence,
                raw_span=text,
            )
        )

    # ── Stock add ───────────────────────────────────────────────────────────
    if is_stock_add and not is_meal and not is_workout:
        # Strip the trigger verb to get just the item list
        stripped = re.sub(
            r"^.*?(?:bought|purchased|got|added|picked up|compramos|I got|we got)\s*",
            "",
            text,
            count=1,
            flags=re.IGNORECASE,
        )
        items = _extract_stock_items(stripped)
        confidence = 0.5 if not items else min(0.6 + 0.1 * len(items), 0.9)
        intents.append(
            StockAddIntent(
                participants=_resolve_participants(text, speaking_user),
                items=items,
                confidence=confidence,
                raw_span=text,
            )
        )

    # ── Stock consume ────────────────────────────────────────────────────────
    if is_stock_consume:
        stripped = re.sub(
            r"^.*?(?:used|consumed|finished|ran out of|used up|usamos|gastamos)\s*",
            "",
            text,
            count=1,
            flags=re.IGNORECASE,
        )
        items = _extract_stock_items(stripped)
        confidence = 0.5 if not items else min(0.6 + 0.1 * len(items), 0.9)
        intents.append(
            StockConsumeIntent(
                participants=_resolve_participants(text, speaking_user),
                items=items,
                confidence=confidence,
                raw_span=text,
            )
        )

    # ── Nothing matched ──────────────────────────────────────────────────────
    if not intents:
        fallback = ParsedIntent(
            intent_type="mixed",
            participants=_resolve_participants(text, speaking_user),
            confidence=0.1,
            raw_span=text,
        )
        return ParseResult(
            intents=[fallback],
            overall_confidence=0.1,
            parser_layer="rules",
            raw_text=text,
        )

    overall = sum(i.confidence for i in intents) / len(intents)

    return ParseResult(
        intents=intents,
        overall_confidence=round(overall, 3),
        parser_layer="rules",
        raw_text=text,
    )
