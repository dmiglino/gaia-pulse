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
from typing import Any, cast

from app.nlp.intents import (
    BodyMetricIntent,
    ExerciseRef,
    FoodItemRef,
    MealIntent,
    ParsedIntent,
    ParseResult,
    PreferenceIntent,
    PreferenceSignal,
    StockAddIntent,
    StockConsumeIntent,
    StockItemRef,
    UserKey,
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
#: Primera persona del plural, la que dice "esto lo hicimos los dos".
#:
#: En inglés la pluralidad viaja en un pronombre suelto ("**we** had pasta"), y por eso
#: `_WE_PATTERNS` alcanzaba con cuatro palabras. En castellano viaja **en el verbo** y el
#: pronombre no se escribe: nadie pone "nosotros cenamos fideos", pone "cenamos fideos".
#: Sin esta lista esa frase se atribuía a una sola persona —la que escribe—, así que una
#: cena compartida entraba como comida de uno, que es justo lo que esta app no debería
#: equivocar. Va por lista explícita y no por `\w+amos\b` porque ese atajo convierte
#: "500 **gramos** de avena" en algo que hicieron los dos.
_WE_VERBS_ES = (
    "desayunamos|almorzamos|cenamos|merendamos|comimos|tomamos"
    "|compramos|conseguimos|usamos|gastamos|terminamos|acabamos"
    "|entrenamos|corrimos|caminamos|nadamos|hicimos|fuimos"
    "|pesamos|dormimos"
    # Las de preferencia: "no **nos** gusta la remolacha" es de los dos, igual que
    # "we don't like beets", y sin esto quedaba como el gusto de quien escribió.
    "|preferimos|podemos|queremos|odiamos|detestamos|evitamos"
)

_WE_PATTERNS = re.compile(
    r"\b(we|both|us|nosotros|nos|" + _WE_VERBS_ES + r")\b",
    re.IGNORECASE,
)
_I_PATTERNS = re.compile(
    r"\b(i|yo|me)\b",
    re.IGNORECASE,
)


def _resolve_participants(text: str, speaking_user: str) -> list[UserKey]:
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
        return [cast(UserKey, speaking_user)]
    return [cast(UserKey, speaking_user)]


# ---------------------------------------------------------------------------
# Quantity / unit parsing
# ---------------------------------------------------------------------------

#: Cantidades escritas con letras. La tabla la lee `_parse_qty`, y el **único** que le pasa
#: algo es el grupo `qty` de `_QTY_UNIT_ITEM`: una clave que ese grupo no sepa reconocer es
#: una entrada muerta. Hasta acá el grupo aceptaba `\d+`, `half`, `a` y `an`, o sea que
#: `one`…`ten` y `un`…`seis` estaban escritas y nunca se leían — y peor que muertas: sin
#: reconocer la palabra, el nombre del alimento se la come ("dos bananas" → un alimento
#: llamado "dos banana"). El grupo `qty` ahora nombra todas las claves de acá; si se agrega
#: una, hay que agregarla también allá. Un test fija que las dos listas coincidan.
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
    "medio": 0.5,
    "media": 0.5,
}

#: Alternación de las cantidades escritas con letras, armada **desde** `_NUMBER_WORDS` para
#: que no haya dos listas que se puedan desincronizar. Las más largas primero: si "un" se
#: probara antes que "una", "una banana" dejaría "a banana" como nombre.
_NUMBER_WORD_ALT = "|".join(re.escape(w) for w in sorted(_NUMBER_WORDS, key=len, reverse=True))

# Regex: optional leading number, then optional unit, then item name
#
# El orden dentro de `unit` **no es cosmético**: la alternación es lo primero que gana, así
# que una unidad que sea prefijo de otra tiene que ir después de la larga. `kilos?` antes de
# `kilogramos?` dejaría "gramos de arroz" como nombre del alimento, y `grams?` antes de
# `gramos?` dejaría "os de arroz". Cada unidad de acá necesita además su entrada en
# `_UNIT_NORMALISE`, o se guarda tal cual se escribió y "kilos" y "kg" quedan como dos
# unidades distintas para la misma cosa.
_QTY_UNIT_ITEM = re.compile(
    r"""
    (?P<qty>
        \d+(?:[.,]\d+)?       # plain number
        |(?:"""
    + _NUMBER_WORD_ALT
    + r""")\b   # "half", "two", "dos", "media"…
    )?
    \s*
    (?P<unit>
        kilogramos?|kilos?|kg
        |gramos?|grams?|gram\b|gr\b|g\b
        |mililitros?|milliliters?|ml
        |litros?|liters?|litres?|l\b
        |oz|ounces?
        |lbs?|pounds?
        |cucharaditas?|cucharadas?|tablespoons?|tbsp|teaspoons?|tsp
        |tazas?|cups?
        |unidades|unidad|units?|pieces?|pcs?
        |docenas?
        |rebanadas?|slices?
        |porciones|porci[oó]n|servings?|portions?
    )?
    \s*
    (?:of\s+|de\s+)?
    (?P<name>[a-záéíóúüñ][a-záéíóúüñ\s\-]+?)
    (?=\s*(?:,|and\b|$|\bwith\b|\bcon\b|\bfor\b|\bof\b|\+))
    """,
    re.IGNORECASE | re.VERBOSE,
)

_UNIT_NORMALISE: dict[str, str] = {
    "gram": "g",
    "grams": "g",
    "gramo": "g",
    "gramos": "g",
    "gr": "g",
    "kilogram": "kg",
    "kilograms": "kg",
    "kilogramo": "kg",
    "kilogramos": "kg",
    "kilo": "kg",
    "kilos": "kg",
    "mililitro": "ml",
    "mililitros": "ml",
    "litro": "l",
    "litros": "l",
    "cucharada": "tbsp",
    "cucharadas": "tbsp",
    "cucharadita": "tsp",
    "cucharaditas": "tsp",
    "taza": "cup",
    "tazas": "cup",
    "unidad": "unit",
    "unidades": "unit",
    "docena": "unit",
    "docenas": "unit",
    "rebanada": "slice",
    "rebanadas": "slice",
    "porción": "serving",
    "porcion": "serving",
    "porciones": "serving",
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


#: `docena`/`docenas` no es una unidad más: es una cantidad de la unidad genérica
#: `unit`, así que además de normalizarse (arriba) tiene que multiplicar el número que la
#: precede — "una docena" es 1 * 12, "dos docenas" es 2 * 12. Sin esto "una docena de huevos"
#: normalizaría a `unit` pero guardaría `quantity=1`, media docena de la cantidad real.
_DOZEN_UNITS = {"docena", "docenas"}
_DOZEN_MULTIPLIER = 12


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


#: Determinantes que abren un ítem y no forman parte de su nombre. El nombre que sale de acá
#: se busca después contra el catálogo de alimentos, así que "la última leche" no encuentra
#: la leche y "usé la última leche" termina creando un alimento nuevo. Se recortan solo los
#: **definidos** y los posesivos: `un`, `una`, `a` y `an` valen 1 y los lee el grupo `qty`,
#: recortarlos sería perder la cantidad. Es un `+` porque se apilan ("la última leche").
_LEADING_DETERMINERS = re.compile(
    r"^(?:(?:the|el|la|los|las|mi|mis|nuestr[oa]s?|últim[oa]s?|ultim[oa]s?|last)\s+)+",
    re.IGNORECASE,
)

#: Un pronombre suelto no es un alimento. Estaba escrito dos veces en `_extract_items`, una
#: por rama, y solo la rama del regex lo chequeaba con el largo mínimo.
_ITEM_STOPWORDS = {
    "we",
    "i",
    "you",
    "they",
    "he",
    "she",
    "nosotros",
    "yo",
    "él",
    "ella",
    "vos",
    "tu",
    "tú",
}


def _extract_items(text: str) -> list[FoodItemRef]:
    """Extract food items with optional quantity/unit from a text segment."""
    items: list[FoodItemRef] = []
    # Split on commas and 'and'/'y' to tokenize. Sin el `y` toda una lista escrita en
    # castellano entraba como **un** alimento: "6 bananas y 1 kg de avena" quedaba como un
    # ítem llamado "6 bananas y 1 kg de avena".
    segments = re.split(r",\s*|\band\b|\by\b", text, flags=re.IGNORECASE)
    for seg in segments:
        seg = _LEADING_DETERMINERS.sub("", seg.strip()).strip()
        if not seg:
            continue
        m = _QTY_UNIT_ITEM.match(seg)
        if m:
            qty = _parse_qty(m.group("qty"))
            raw_unit = m.group("unit")
            unit = _normalise_unit(raw_unit)
            if raw_unit and raw_unit.lower() in _DOZEN_UNITS:
                qty = (qty if qty is not None else 1) * _DOZEN_MULTIPLIER
            raw_name = m.group("name").strip() if m.group("name") else seg
            # Attempt basic depluralization: only strip trailing 's'
            # if word ends in 's' but not 'ss'
            if raw_name.endswith("s") and not raw_name.endswith("ss") and len(raw_name) > 3:
                name = raw_name[:-1]
            else:
                name = raw_name
        else:
            # Fallback: plain name
            qty = None
            unit = None
            name = seg
        # Avoid capturing stopwords alone
        if len(name) < 2 or name.lower() in _ITEM_STOPWORDS:
            continue
        items.append(FoodItemRef(food_name=name, qty=qty, unit=unit))
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

#: Palabra → tipo de comida. En inglés el sustantivo alcanza porque el verbo es genérico
#: ("we **had** pasta for **dinner**"); en castellano el tipo de comida vive en el verbo
#: conjugado y no se repite como sustantivo —nadie escribe "cené la cena"—, así que las
#: conjugaciones tienen que estar acá o "cenamos fideos" queda como `meal_type="other"`.
#: Que estén también en la lista que `_build_items_per_user` recorta es inofensivo: ese
#: recorte pasa después del recorte del verbo, así que la palabra ya no está.
_MEAL_TYPE_MAP: dict[str, str] = {
    "breakfast": "breakfast",
    "desayuno": "breakfast",
    "desayunamos": "breakfast",
    "desayuné": "breakfast",
    "desayunó": "breakfast",
    "lunch": "lunch",
    "almuerzo": "lunch",
    "almorzamos": "lunch",
    "almorcé": "lunch",
    "almorzó": "lunch",
    "dinner": "dinner",
    "cena": "dinner",
    "cenamos": "dinner",
    "cené": "dinner",
    "cenó": "dinner",
    "supper": "dinner",
    "snack": "snack",
    "merienda": "snack",
    "merendamos": "snack",
    "merendé": "snack",
    "merendó": "snack",
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

#: Palabra suelta → (nombre canónico del ejercicio, grupo muscular).
#:
#: Los grupos que salen de acá tienen que estar en `learning.MUSCLE_GROUPS`, que es el
#: vocabulario único desde la 4.5.2: lo que se graba leyendo una captura y lo que el
#: generador de actividad propone se comparan por esta clave, y hasta la 4.5.2 no coincidían
#: —este mapa emitía `triceps` y `biceps` como grupos propios y el catálogo escribe los dos
#: como `arms`, así que el mismo músculo quedaba en dos claves y ninguna veía el estímulo de
#: la otra—. El **nombre** del ejercicio sigue siendo el que se dijo (se muestra en la
#: pantalla de entrenamientos); lo que conforma al vocabulario es el grupo. Un test lo fija
#: (`test_every_muscle_group_the_nlp_emits_is_in_the_vocabulary`) para que la próxima entrada
#: no lo rompa en silencio.
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
    "triceps": ("triceps", "arms"),
    "biceps": ("biceps", "arms"),
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

#: `_extract_duration_minutes` decide horas contra minutos por la primera letra de la unidad,
#: así que `horas`/`minutos` caen del lado correcto sin tocar esa función. Sin ellas
#: "corrí 30 **minutos**" entraba como entrenamiento sin duración, mientras que
#: "corrí 30 **min**" —lo que ofrece el placeholder— sí se leía: la mitad de un ejemplo.
#: La duración acepta la cantidad escrita con letras por la **misma** razón que la acepta el
#: grupo `qty` de los alimentos, y con la misma tabla: "entrené pesas una hora" es la forma
#: normal de decirlo y con solo `\d+` daba `duration_minutes: None` — un entrenamiento
#: guardado sin duración, que es el dato que el motor usa para todo lo demás.
#:
#: El apóstrofo (`30'`) es una tercera forma de escribir minutos y va en su **propia**
#: alternativa (`apos`), no dentro de `unit`: un apóstrofo no es un carácter de palabra, así
#: que el `\b` final que cierra `horas?|...|m\b` nunca lo alcanzaría — entre `'` y el espacio
#: o el final de la frase no hay transición palabra/no-palabra. En su lugar la alternativa
#: usa `(?!\w)`, y el número tiene que estar pegado o separado solo por espacios: exige un
#: `\d` o una palabra de cantidad inmediatamente antes, así que un apóstrofo que abre una cita
#: ("dijo 'no'") no matchea nunca — no hay número del que colgarse.
_DURATION_RE = re.compile(
    r"\b(?P<val>\d+(?:\.\d+)?|" + _NUMBER_WORD_ALT + r")\s*"
    r"(?:(?P<unit>horas?|hours?|hrs?|hs\b|h\b|minutos?|minutes?|mins?|m\b)\b"
    r"|(?P<apos>')(?!\w))",
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
    # Va por `_parse_qty` y no por `float()` porque el grupo `val` ahora también acepta la
    # cantidad escrita con letras; `float("una")` sería un ValueError sin atrapar.
    val = _parse_qty(m.group("val"))
    if val is None:
        return None
    if m.group("apos"):
        return int(val)
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
    también lo que reconoce una captura, así que no se hace de contrabando acá.

    Y no lo arregla esta función, aunque `ExerciseType` ya tenga `aliases_json` con las
    formas castellanas desde la 7.5 (`0004`): esta función sigue **sin tocar la base**, a
    propósito —es el mismo motivo por el que `parse` no toma una `Session`—, así que el
    castellano de la 7.5 no entra por acá. Entra por `learning.subjects_in_text`, que combina
    esta función con `activity_vocabulary(db)` —el vocabulario del catálogo, alias
    incluidos— exactamente como ya hacía con la comida: dos vocabularios cerrados, cada uno
    resuelto donde corresponde.

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
#
# Estos cinco regex son la **compuerta**: sin uno de estos verbos la frase no llega a
# ningún parser y sale como `mixed` con confianza 0.10, o sea sin nada que confirmar.
# Hasta acá el castellano estaba solo en primera persona del plural —"cenamos",
# "compramos", "usamos"— y faltaba el singular, que es la mitad de lo que escribe una casa
# de dos: *"comí milanesa"*, *"compré 6 bananas"*, *"dormí 7 horas"* no entraban. Lo de
# dormir era el caso más claro de que era un descuido y no una decisión: `_SLEEP_RE` ya
# entiende `dormí|dormimos` desde siempre, y el valor nunca podía leerse porque la
# compuerta no dejaba pasar la frase. El *placeholder* de la pantalla de captura, además,
# ofrecía como ejemplo "compré 1 kg de avena", que es exactamente una de las formas que no
# funcionaban.
#
# Lo que se agrega es solo la compuerta. **Los nombres de ejercicio siguen en inglés** y
# eso no cambia acá: `find_known_activities` explica por qué (hace falta una columna de
# alias, o sea una migración). La consecuencia es visible y aceptada: *"corrí 30 minutos"*
# ahora sí se reconoce como entrenamiento, con su duración, y **sin ejercicio nombrado** —
# igual que "trained for 45 minutes", que es el caso que el parser ya trataba así.

#: Verbos de comer, una sola vez. Los leen tres cosas que tenían tres listas distintas y se
#: desincronizaban de a una: la compuerta (`_MEAL_TRIGGERS`), el corte de "quién comió qué"
#: (`_split_by_user`) y el corte del verbo que abre la frase (`_build_items_per_user`). Que
#: un verbo esté en la compuerta y no en los cortes no da un error: da un alimento llamado
#: "comí milanesa con puré". Por eso ahora es una lista y tres lectores.
_MEAL_VERBS = (
    "ate|eat|eaten|had|having"
    "|comí|comió|comimos|comiste"
    "|desayuné|desayunó|desayunamos"
    "|almorcé|almorzó|almorzamos"
    "|cené|cenó|cenamos"
    "|merendé|merendó|merendamos"
    "|tomé|tomó|tomamos"
)

_MEAL_TRIGGERS = re.compile(
    r"\b(" + _MEAL_VERBS + r"|breakfast|lunch|dinner|snack|meal|food"
    r"|about to have|going to eat|just ate|just had)\b",
    re.IGNORECASE,
)

#: Vocabulario de actividades, una sola vez, con **dos** lectores que fallan distinto.
#:
#: `_classify_item_type` lo usa para decidir si una preferencia se guarda contra un
#: **ejercicio** o contra un **alimento**: "prefiero correr" guardado como preferencia *de
#: comida* ensucia el filtro de alimentos con una palabra que no es comida, y nadie lo ve
#: nunca. `_DID_ACTIVITY` lo usa para que la compuerta de entrenamiento reconozca
#: "hice yoga" y "fuimos a spinning". Los nombres castellanos van acá aunque `_EXERCISE_MAP`
#: siga en inglés —eso necesita una columna de alias, ver `find_known_activities`—: un
#: nombre que el catálogo no reconoce queda como texto libre y **se ve** en pantalla, que es
#: el modo de falla barato de los dos.
_ACTIVITY_NAMES = {
    "gym",
    "biking",
    "bike",
    "cycling",
    "yoga",
    "running",
    "swimming",
    "swim",
    "pilates",
    "crossfit",
    "hiit",
    "zumba",
    "spinning",
    "hiking",
    "walking",
    "weightlifting",
    "weights",
    "boxing",
    "dancing",
    "rowing",
    "correr",
    "caminar",
    "nadar",
    "natación",
    "natacion",
    "pesas",
    "bicicleta",
    "gimnasio",
    "trotar",
    "andar",
    "remo",
    "boxeo",
    "baile",
    "bailar",
    "entrenar",
    "ejercicio",
}

_ACTIVITY_ALT = "|".join(re.escape(w) for w in sorted(_ACTIVITY_NAMES, key=len, reverse=True))

#: "hice yoga", "fuimos a spinning", "did pilates". Antes esto estaba **enumerado a mano** y
#: solo en inglés (`did yoga|did pilates|did hiit`), o sea que la actividad número cuatro no
#: entraba en ningún idioma y en castellano no entraba ninguna: "hice yoga 40 minutos"
#: volvía `mixed` al 0.10. Pide el verbo a propósito y no acepta la actividad sola, porque
#: "prefiero correr" es una preferencia y no un entrenamiento — sin el verbo, esa frase
#: dispararía las dos cosas y la segunda saldría vacía.
_DID_ACTIVITY = r"(?:hice|hicimos|fui a|fuimos a|did|went to)\s+(?:" + _ACTIVITY_ALT + r")"

_WORKOUT_TRIGGERS = re.compile(
    r"\b(went to the gym|trained|training|workout|worked out|exercised|rode|"
    r"went for a (run|bike|swim|walk|ride)|gym|biked|ran|swam|walked|"
    r"went hiking|gimnasio|entrené|entrenamos|corrí|corrimos|"
    r"caminé|caminamos|nadé|nadamos|pesas|" + _DID_ACTIVITY + r")\b",
    re.IGNORECASE,
)

_BODY_METRIC_TRIGGERS = re.compile(
    r"\b(weigh|weight|weighed|peso|pesé|pesamos|I weigh|I am|my weight|body fat|waist|"
    r"sleep|slept|dormí|dormimos|cintura|body mass|bmi)\b",
    re.IGNORECASE,
)

#: Verbos de entrada y de salida de la despensa. Igual que con las comidas, cada lista la
#: leen dos cosas —la compuerta y el recorte del verbo que abre la frase— y tenerlas
#: separadas se paga en datos, no en excepciones: un verbo que abre la frase y no está en el
#: recorte deja el verbo adentro del primer ítem ("compré 6 bananas" → un alimento llamado
#: "compré 6 bananas"). Las formas largas van antes que las cortas: "ran out of" antes que
#: "ran out", o el recorte deja el "of" colgando.
_STOCK_ADD_VERBS = (
    "bought|buy|purchased|picked up|added|we got|I got|got|we have"
    "|compramos|compré|compraron|conseguimos|conseguí"
)

#: Las formas **impersonales** del castellano ("se acabó la leche", "no queda café") van acá
#: aunque no tengan sujeto: son la manera normal de avisar que algo se terminó, y sin ellas
#: "se acabó la leche" volvía `mixed` al 0.10. Las largas antes que las cortas, como siempre:
#: "no hay más" antes que "no hay", o el recorte deja el "más" adentro del nombre.
_STOCK_CONSUME_VERBS = (
    "ran out of|ran out|used up|we used|I used|used|consumed|finished"
    "|usamos|usé|gastamos|gasté|terminamos|terminé|acabamos|acabé"
    "|se acabaron|se acabó|se acabo|se terminaron|se terminó|se termino"
    "|no hay más|no hay mas|no quedan|no queda"
)

_STOCK_ADD_TRIGGERS = re.compile(r"\b(" + _STOCK_ADD_VERBS + r")\b", re.IGNORECASE)

_STOCK_CONSUME_TRIGGERS = re.compile(r"\b(" + _STOCK_CONSUME_VERBS + r")\b", re.IGNORECASE)

#: La negación va partida en dos, y la razón es del idioma: en castellano `no` es la
#: partícula de **todo**, así que un `\bno\b` suelto convierte cualquier frase negativa en un
#: gusto. "No queda café" es un dato de la despensa y salía como *"no te gusta 'queda
#: café'"* — una preferencia guardada contra un alimento que no existe. Lo explícito
#: ("odio", "no me gusta") vale solo; lo suelto vale **salvo** que la frase ya tenga un verbo
#: de consumo, en cuyo caso el dato de despensa manda. Ver `_parse_preference`.
_PREFERENCE_NEG_EXPLICIT = re.compile(
    r"\b(hate|dislike|avoid|not suggest|"
    r"no me gusta|no nos gusta|no me gustan|no nos gustan|imposible|no podemos|"
    r"odio|odiamos|detesto|detestamos|no soporto|no soportamos|evitamos)\b",
    re.IGNORECASE,
)

_PREFERENCE_NEG_BARE = re.compile(
    r"\b(don'?t|do not|no|never|can'?t|cannot)\b",
    re.IGNORECASE,
)

_PREFERENCE_NEG_TRIGGERS = re.compile(
    _PREFERENCE_NEG_EXPLICIT.pattern + r"|" + _PREFERENCE_NEG_BARE.pattern,
    re.IGNORECASE,
)

_PREFERENCE_POS_TRIGGERS = re.compile(
    r"\b(like|love|enjoy|prefer|we like|I like|we love|I love|nos gusta|"
    r"me gusta|me gustan|nos gustan|me encanta|nos encanta|preferimos|prefiero)\b",
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
        r"(?:(?:" + _MEAL_VERBS + r")\b|:)\s*"
        r"(?P<items>.+?)"
        r"(?=,\s*(?:diego|roc[íi]o|roci|we|both)\s*(?:" + _MEAL_VERBS + r")\b|$)",
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
    participants: list[UserKey],
    speaking_user: str,
) -> dict[str, list[FoodItemRef]]:
    """Build the items_per_user dict for a MealIntent."""
    per_user = _split_by_user(text)
    if per_user:
        return {u: _extract_items(seg) for u, seg in per_user.items()}

    # No per-user split found — all items go to all participants
    #
    # Strip leading trigger words before extracting. El `\b` del final no es decorativo: sin
    # él "eat" recorta adentro de "eaten" y "we have eaten pasta" deja un alimento llamado
    # "en pasta". La compuerta nunca lo mostró porque ahí la alternación **sí** va entre
    # `\b`, así que el defecto vivía solo en los recortes.
    stripped = re.sub(
        r"^.*?(?:" + _MEAL_VERBS + r"|about to have|going to eat|just ate|just had)\b\s*",
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


def _classify_item_type(item_name: str) -> str:
    if item_name.lower() in _ACTIVITY_NAMES:
        return "exercise"
    exercises = _extract_exercises(item_name)
    if exercises:
        return "exercise"
    return "food"


def _parse_preference(text: str, speaking_user: str) -> PreferenceIntent | None:
    """Parse a preference update statement."""
    participants = _resolve_participants(text, speaking_user)
    user_key = participants[0] if len(participants) == 1 else "both"

    # Una negación suelta no alcanza si la frase ya dice que algo se terminó: "no queda café"
    # es la despensa hablando, no un gusto. Ver `_PREFERENCE_NEG_BARE`.
    is_neg = bool(_PREFERENCE_NEG_EXPLICIT.search(text)) or (
        bool(_PREFERENCE_NEG_BARE.search(text)) and not _STOCK_CONSUME_TRIGGERS.search(text)
    )
    is_pos = bool(_PREFERENCE_POS_TRIGGERS.search(text))

    if not is_neg and not is_pos:
        return None

    has_suggest = bool(_SUGGEST_WORD.search(text))

    # Determine signal
    signal: PreferenceSignal
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
    # Try to strip negation + suggestion words to get the noun.
    #
    # Lo que queda acá es el **nombre del sujeto** de la preferencia, y con eso se busca el
    # alimento o el ejercicio: si sobra una palabra de la frase, no se encuentra nada. La
    # lista inglesa estaba completa y la castellana no existía más allá del `no`, así que
    # "no me gusta el brócoli" salía con el sujeto `"gusta brócoli"` — una preferencia
    # guardada contra un alimento que no existe, que es peor que no guardarla.
    cleaned = re.sub(
        r"\b(don'?t|do not|please|no|never|not|suggest|recommend|we|i|like|love|"
        r"enjoy|prefer|avoid|dislike|hate|can'?t|cannot|"
        r"me|nos|gusta|gustan|gustó|encanta|encantan|odio|odiamos|detesto|detestamos|"
        r"prefiero|preferimos|evitar|evitamos|sugieras|sugerir|sugieran|recomiendes|"
        r"nunca|jamás|jamas|nada|puedo|podemos|imposible|soporto|soportamos|"
        r"comer|comemos|tomar|tomamos|hacer|hacemos|encanta|encantan|"
        # Los artículos de dos letras (`el`, `la`, `de`) ya los descarta el filtro de
        # `len(w) > 2` de más abajo; los de tres no, y por eso están nombrados.
        r"los|las|del|una)\b",
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
#: Estos dos son el mismo descuido que la compuerta, un paso más adentro: la compuerta ya
#: admitía `cintura` y `dormí|dormimos`, y acá la unidad y el sustantivo seguían siendo solo
#: los ingleses. O sea que la frase entraba, se clasificaba como métrica corporal, y el
#: número no se leía nunca: "dormí 7 horas" daba una medición vacía. `hs` entra porque es
#: como se escribe acá, y `cintura de 80 cm` porque el "de" es obligatorio en castellano.
_WAIST_RE = re.compile(
    r"\b(?:waist|cintura)\s*(?:is\s*|de\s*)?(?P<val>\d+(?:[.,]\d+)?)\s*cm\b",
    re.IGNORECASE,
)
_SLEEP_RE = re.compile(
    r"\b(?:slept|sleep|dormí|dormimos)\s*(?:for\s*)?(?P<val>\d+(?:[.,]\d+)?)\s*"
    r"(?:hours?|hrs?|horas?|hs\b|h\b)\b",
    re.IGNORECASE,
)


def _parse_body_metric(text: str, speaking_user: str) -> BodyMetricIntent | None:
    participants = _resolve_participants(text, speaking_user)
    user_key = participants[0] if len(participants) == 1 else cast(UserKey, speaking_user)

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


#: Categorías de disparador que reconoce la segmentación. Cada entrada es (nombre, regex)
#: y reusa los regex de siempre —agregar una categoría nueva a `_parse_segment` sin
#: agregarla acá es el único modo de romper la segmentación en silencio.
_TRIGGER_CATEGORIES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("meal", _MEAL_TRIGGERS),
    ("workout", _WORKOUT_TRIGGERS),
    ("body_metric", _BODY_METRIC_TRIGGERS),
    ("stock_add", _STOCK_ADD_TRIGGERS),
    ("stock_consume", _STOCK_CONSUME_TRIGGERS),
    ("pref_neg", _PREFERENCE_NEG_TRIGGERS),
    ("pref_pos", _PREFERENCE_POS_TRIGGERS),
)

#: "y"/"and" y el final de oración, los únicos puntos donde `_segment_topics` considera
#: cortar. A propósito no incluye la coma: alcanza con los dos ejemplos documentados en
#: gaiapulse-v3.md §5 y una coma corta demasiado seguido dentro de un mismo tema (ver
#: `test_different_foods_per_person`).
_TOPIC_CONNECTOR = re.compile(r"(\s+y\s+|\s+and\s+|[.;]\s+)", re.IGNORECASE)


def _trigger_types(text: str) -> set[str]:
    """Qué categorías dispara `text`. Base de la segmentación: dos fragmentos que no
    comparten ninguna son dos temas distintos; si comparten alguna, son el mismo tema
    contado dos veces ("pesé 81 kg y dormí 7 horas" son dos hechos de `body_metric`).
    """
    return {name for name, trigger in _TRIGGER_CATEGORIES if trigger.search(text)}


def _segment_topics(text: str) -> list[str]:
    """Parte `text` en tramos de un solo tema para que cada parser lea el suyo, no la frase
    entera. Solo corta en un conector cuando el tramo de después dispara una categoría que
    el tramo acumulado hasta ahí no tenía —si es la misma categoría, o ninguna, se re-funde
    con el conector de vuelta y queda idéntico al texto original—.
    """
    parts = _TOPIC_CONNECTOR.split(text)
    if len(parts) == 1:
        return [text]

    segments = [parts[0]]
    for i in range(1, len(parts), 2):
        connector = parts[i]
        chunk = parts[i + 1] if i + 1 < len(parts) else ""
        chunk_types = _trigger_types(chunk)
        current_types = _trigger_types(segments[-1])
        if chunk_types and not (chunk_types & current_types):
            segments.append(chunk)
        else:
            segments[-1] = segments[-1] + connector + chunk
    return segments


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

    segments = _segment_topics(text)
    if len(segments) == 1:
        return _parse_segment(text, speaking_user)

    intents: list[Any] = [
        intent
        for segment in segments
        for intent in _parse_segment(segment.strip(), speaking_user).intents
        if intent.intent_type != "mixed"
    ]
    if not intents:
        # Ningún segmento disparó nada por su cuenta: el mismo fallback de "mixed" que
        # `_parse_segment` ya sabe construir, corrido una vez sobre el texto completo en
        # vez de duplicar acá su lógica de participantes.
        return _parse_segment(text, speaking_user)

    overall = sum(i.confidence for i in intents) / len(intents)
    return ParseResult(
        intents=intents, overall_confidence=round(overall, 3), parser_layer="rules", raw_text=text
    )


def _parse_segment(text: str, speaking_user: str) -> ParseResult:
    """El parser de un solo tema, tal como era `parse()` antes de la 7.9. `parse()` lo llama
    una vez con el texto entero cuando no hay que segmentar (salida idéntica a antes) o una
    vez por segmento cuando sí.
    """
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
            for food_items in items_per_user.values():
                for item in food_items:
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
        # Strip the trigger verb to get just the item list — misma lista que la compuerta.
        stripped = re.sub(
            r"^.*?(?:" + _STOCK_ADD_VERBS + r")\b\s*",
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
            r"^.*?(?:" + _STOCK_CONSUME_VERBS + r")\b\s*",
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
