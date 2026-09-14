"""Lo que la app ya sabe de una persona, leído una vez por corrida.

Por qué existe: el motor tenía los datos y no los miraba. `BodyMetricLog` entero
—peso, grasa, cintura, **sueño**— no lo leía nadie; `WorkoutExercise.perceived_effort`
tampoco; los macros de los 30 alimentos del catálogo estaban sembrados y nada los
sumaba nunca; `BloodAnalysis.analysis_date` existía y la antigüedad del panel no se
chequeaba, así que un análisis de hace tres años dictaba consejos de hoy. Mientras
tanto cada generador abría sus propias consultas —14 inline en
`app/recommendations/`— y algunas se contradecían entre sí: "qué comió últimamente"
tenía dos respuestas distintas según a quién se le preguntara.

`UserContext` es **una sola lectura por corrida**, de solo lectura, y **no decide
nada**. No hay umbrales acá, no hay "está durmiendo poco", no hay "le toca pierna":
esas son reglas y las reglas son de cada generador. Si el contexto empieza a opinar,
la lógica vuelve a estar en dos lados y el próximo cambio tiene que acordarse de los
dos.

Es **personal**, no del hogar: cada campo sale de consultas filtradas por el
`user_id` de la persona, que es la regla 4 de `AGENTS.md`. Lo del hogar —el stock de
la despensa— no está acá a propósito: no es de nadie en particular, y meterlo en un
contexto por persona sería exactamente la lectura mezclada que la regla prohíbe.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.clock import as_utc, local_now, local_today, to_local
from app.models.meal import MealItemConsumed
from app.models.user import User
from app.models.workout import ExerciseType
from app.recommendations.learning import normalize_muscle_group
from app.repositories.body_metric_repo import BodyMetricRepository
from app.repositories.meal_repo import MealRepository
from app.repositories.workout_repo import ExerciseTypeRepository, WorkoutRepository
from app.services.blood_analysis_service import BloodAnalysisService

logger = logging.getLogger(__name__)

#: Cuántos días de mediciones corporales se traen para medir una tendencia. Coincide
#: con el default de `get_weight_series`, que es el que alimenta el gráfico del
#: dashboard: si el gráfico dice "últimos 30 días" y el motor razona sobre otra
#: ventana, la explicación de una sugerencia no se puede verificar contra la pantalla.
_METRIC_WINDOW_DAYS = 30

#: La ventana de "qué comió últimamente". Es el mismo 7 que usaba
#: `meal_generator._RECENCY_DAYS`, y ahora es el único: tener el número acá y allá es
#: la clase de duplicación que se despega sin que falle nada.
#:
#: Público —sin guión bajo— porque el generador de comidas se lo **dice al usuario**
#: ("no comiste esto en los últimos 7 días"), así que la frase y la consulta tienen que
#: salir del mismo número o la app afirma una ventana y mide otra.
RECENT_FOOD_DAYS = 7

#: La ventana de macros. Catorce días y no siete porque el uso es comparar **hoy**
#: contra la línea de base de la propia persona, y una base de una semana sobre alguien
#: que anota tres veces por semana son tres días: cualquier día suelto la mueve entera.
_MACRO_WINDOW_DAYS = 14

#: La ventana de RPE. Dos semanas: el esfuerzo percibido dice algo sobre el estado
#: actual, no sobre el año.
_EFFORT_WINDOW_DAYS = 14

#: Hacen falta al menos dos mediciones para que haya una tendencia. Con una sola no hay
#: dirección, y devolver "estable" sería inventarla.
_MIN_TREND_POINTS = 2

#: Unidades que se pueden convertir a gramos sin inventar nada. Todo lo demás
#: —"unidad", "taza", "porción"— necesitaría `serving_size_g`, que **ninguna parte de
#: la app escribe nunca**, así que no se convierte: se cuenta como no cubierto.
_GRAMS_PER_UNIT: Mapping[str, float] = {
    "g": 1.0,
    "gr": 1.0,
    "grs": 1.0,
    "gram": 1.0,
    "grams": 1.0,
    "gramo": 1.0,
    "gramos": 1.0,
    "kg": 1000.0,
    "kilo": 1000.0,
    "kilos": 1000.0,
    "kilogram": 1000.0,
    "kilograms": 1000.0,
    "kilogramo": 1000.0,
    "kilogramos": 1000.0,
}


@dataclass(frozen=True)
class Trend:
    """Una medición que cambió (o no) entre los dos extremos de una ventana.

    Guarda los extremos y no solo la diferencia porque "bajó 400 g" y "bajó 400 g
    desde 62 kg" no son la misma frase, y la explicación de una sugerencia va a querer
    la segunda. `points` es cuántas mediciones había: una tendencia de dos puntos es
    una recta entre dos días y hay que poder decirlo.
    """

    first: float
    last: float
    days: int
    points: int

    @property
    def delta(self) -> float:
        return round(self.last - self.first, 2)


@dataclass(frozen=True)
class MacroTotals:
    """Macros sumados, **con cuántos ítems se pudieron sumar**.

    La cobertura no es un extra: sumar un macro necesita que el ítem esté resuelto
    contra el catálogo (`food_item_id`) **y** que su cantidad se pueda llevar a gramos,
    y las dos cosas son opcionales en `MealItemConsumed`. Una captura de texto libre
    ("cené milanesas") deja solo el nombre normalizado, y una cantidad en "unidades"
    necesitaría `serving_size_g`, que nadie escribe. Un total que no dice cuánto contó
    es un número con aire de precisión: 40 g de proteína sobre 2 de 9 ítems no es la
    proteína del día.
    """

    calories: float = 0.0
    protein_g: float = 0.0
    carbs_g: float = 0.0
    fat_g: float = 0.0
    fiber_g: float = 0.0
    items_counted: int = 0
    items_total: int = 0
    #: Sobre cuántos días se calculó: 1 para el total de un día, y para un promedio la
    #: cantidad de días que aportaron algo. Es lo que separa "tu promedio" de "el único día
    #: que anotaste": los dos son un número, y solo uno describe una costumbre. Quien
    #: compare contra la base tiene que poder exigir un mínimo de días en vez de creerle a
    #: una muestra de uno.
    days_counted: int = 0

    @property
    def coverage(self) -> float:
        """Qué fracción de los ítems entró en la suma. Sin ítems, cero: no uno."""
        if self.items_total == 0:
            return 0.0
        return self.items_counted / self.items_total

    @property
    def is_empty(self) -> bool:
        return self.items_counted == 0


@dataclass(frozen=True)
class BloodPanel:
    """El último panel legible, con **de cuándo es**.

    La fecha es el punto: `get_latest_values` devolvía el blob de marcadores y tiraba
    la fila, así que el generador de sangre no tenía forma de saber si estaba hablando
    de un análisis de este año o de 2021.

    `age_days` es `None` cuando el panel no tiene fecha —el parser no siempre la
    encuentra— y eso **no** es lo mismo que cero: es "no sé de cuándo es", que para
    decidir si dar un consejo pesa más parecido a viejo que a nuevo. Quién decide eso
    es el generador; acá solo se distingue el caso.
    """

    values: Mapping[str, Any]
    analysis_date: date | None
    age_days: int | None


@dataclass(frozen=True)
class UserContext:
    """Todo lo anterior junto, armado una vez y pasado a quien lo necesite."""

    user_id: int
    now: datetime
    today: date

    weight: Trend | None = None
    body_fat: Trend | None = None
    #: Horas de sueño anotadas en la ventana, de la más reciente a la más vieja.
    sleep_hours: tuple[float, ...] = ()

    days_since_last_workout: int | None = None
    #: Días desde el último estímulo, por grupo muscular. Un grupo **ausente** de este
    #: mapa nunca se entrenó, que es distinto de "hace mucho": la diferencia decide si
    #: la app propone empezar o propone volver.
    #:
    #: Las claves están en el vocabulario de `learning.MUSCLE_GROUPS` —o son el nombre
    #: normalizado, cuando la columna trae algo que el vocabulario no conoce—, así que un
    #: `triceps` grabado por el NLP y un `arms` grabado por el catálogo son **un** grupo acá.
    #: Sin eso el generador veía dos, y cada uno se perdía el estímulo del otro.
    days_since_muscle_group: Mapping[str, int] = field(default_factory=dict)
    #: RPE anotados en la ventana, del más reciente al más viejo.
    recent_effort: tuple[int, ...] = ()

    #: Cuántas veces comió cada alimento en la ventana reciente, en minúsculas.
    recent_food_counts: Mapping[str, int] = field(default_factory=dict)
    macros_today: MacroTotals = field(default_factory=MacroTotals)
    #: El promedio **por día registrado** de la ventana, sin contar hoy, y contando de
    #: cada día solo lo anotado **antes de esta hora**. Es la base contra la que se compara
    #: cuando la persona no declaró un objetivo (fase 7.6) — y para calorías, que nunca lo
    #: usa como base (`_MACRO_BASELINE_ELIGIBLE` en `meal_generator`), es directamente
    #: informativo.
    #:
    #: El corte por hora es lo que hace que la comparación sea una comparación.
    #: `macros_today` es el día **a medio andar** —a las 18:40, que es cuando corre el job
    #: de la tarde, la cena todavía no pasó—, así que medirlo contra un promedio de días
    #: completos le da "hoy vas liviano" a todo el mundo, y todos los días a quien cena
    #: fuerte. Con el corte los dos números son "hasta acá", que es la única forma de que
    #: la diferencia hable de lo que se comió y no de la hora que es.
    macros_baseline: MacroTotals = field(default_factory=MacroTotals)
    #: Objetivo nutricional declarado por la persona en `/profile/` (fase 7.6), o `None`
    #: cuando no declaró uno. Cuando existe, `meal_generator` compara contra esto en vez de
    #: contra `macros_baseline` — es la única fuente que habilita una tarjeta de calorías,
    #: porque comparar calorías contra el propio promedio no afirma nada (comer como siempre
    #: no es ni bueno ni malo sin un objetivo declarado).
    goal_protein_g: float | None = None
    goal_fiber_g: float | None = None
    goal_calories_kcal: int | None = None

    #: El catálogo de ejercicios. Puede venir **vacío** (los tests no lo siembran), y
    #: quien lo use tiene que funcionar con cero filas en vez de reponer una lista fija.
    exercise_catalog: tuple[ExerciseType, ...] = ()
    blood_panel: BloodPanel | None = None

    @property
    def average_sleep_hours(self) -> float | None:
        if not self.sleep_hours:
            return None
        return round(sum(self.sleep_hours) / len(self.sleep_hours), 1)

    @property
    def average_effort(self) -> float | None:
        if not self.recent_effort:
            return None
        return round(sum(self.recent_effort) / len(self.recent_effort), 1)

    def days_since_training(self, muscle_group: str) -> int | None:
        """Días desde el último estímulo de ese grupo, o `None` si nunca.

        Pregunta por el grupo canónico: quien pregunte por "triceps" recibe lo que sabemos de
        "arms", porque es el mismo músculo con dos nombres y el mapa está armado con uno solo.
        """
        return self.days_since_muscle_group.get(normalize_muscle_group(muscle_group))


def build_user_context(db: Session, user: User) -> UserContext:
    """Lee todo lo que el motor sabe de esta persona, una sola vez.

    Se llama una vez por corrida de `generate_for_user` y el resultado viaja a los
    generadores, al scorer y a los filtros. Todas las lecturas van por repositorio
    (regla 2 de `AGENTS.md`) y todas filtran por esta persona (regla 4).

    Nada de esto es obligatorio: una cuenta nueva no tiene mediciones, ni comidas, ni
    panel, y el contexto que sale de ahí es válido y está lleno de `None` y de tuplas
    vacías. Quien lo lea tiene que tratar la ausencia como ausencia y no como cero.
    """
    now = local_now()
    today = local_today()
    household_id = user.household_id

    metrics = BodyMetricRepository(db).get_recent_metrics(user.id, days=_METRIC_WINDOW_DAYS)
    workout_repo = WorkoutRepository(db)
    meal_repo = MealRepository(db)

    last_workout = workout_repo.get_last_session_start(user.id, household_id)
    last_by_group = workout_repo.get_last_trained_at_by_muscle_group(user.id, household_id)

    food_since = as_utc(now) - timedelta(days=RECENT_FOOD_DAYS)
    macro_since = as_utc(now) - timedelta(days=_MACRO_WINDOW_DAYS)

    macros_today, macros_baseline = _macro_totals(
        meal_repo.get_consumed_items_since(user.id, macro_since), today, now
    )

    return UserContext(
        user_id=user.id,
        now=now,
        today=today,
        weight=_trend([m.weight_kg for m in metrics], _METRIC_WINDOW_DAYS),
        body_fat=_trend([m.body_fat_pct for m in metrics], _METRIC_WINDOW_DAYS),
        #: Invertido: `get_recent_metrics` viene del más viejo al más nuevo porque una
        #: tendencia se lee en ese orden, y el sueño se lee al revés.
        sleep_hours=tuple(
            float(m.sleep_hours) for m in reversed(metrics) if m.sleep_hours is not None
        ),
        days_since_last_workout=_days_since(last_workout, now),
        days_since_muscle_group=_days_since_by_group(last_by_group, now),
        recent_effort=tuple(
            workout_repo.get_recent_perceived_effort(
                user.id, household_id, days=_EFFORT_WINDOW_DAYS
            )
        ),
        recent_food_counts=meal_repo.get_food_counts_since(user.id, food_since),
        macros_today=macros_today,
        macros_baseline=macros_baseline,
        goal_protein_g=float(user.goal_protein_g) if user.goal_protein_g is not None else None,
        goal_fiber_g=float(user.goal_fiber_g) if user.goal_fiber_g is not None else None,
        goal_calories_kcal=(
            int(user.goal_calories_kcal) if user.goal_calories_kcal is not None else None
        ),
        exercise_catalog=tuple(ExerciseTypeRepository(db).list_all()),
        blood_panel=_blood_panel(db, user.id, today),
    )


def _days_since_by_group(last_by_group: Mapping[str, datetime], now: datetime) -> dict[str, int]:
    """Días desde el último estímulo por grupo **canónico**.

    La colapsada de alias pasa acá y no en el repositorio por la regla de capas:
    `repositories/` es la capa de abajo y no puede importar de `app/recommendations/`, que es
    donde vive el vocabulario. Y no pasa en el generador porque entonces cada generador que
    quisiera leer el mapa tendría que colapsarlo de nuevo — el contrato de este módulo es
    "una sola lectura por corrida", y una clave que significa dos cosas distintas según quién
    la lea no es una sola lectura.

    Cuando dos claves crudas caen en el mismo grupo (`triceps` y `arms`) gana **la más
    reciente**, o sea el menor número de días: el músculo se entrenó ese día, y quedarse con
    la más vieja diría que hace más tiempo del que hace y propondría volver a algo que se
    hizo ayer.
    """
    collapsed: dict[str, int] = {}
    for raw_group, moment in last_by_group.items():
        days = _days_since(moment, now)
        if days is None:
            continue
        group = normalize_muscle_group(raw_group)
        if not group:
            continue
        current = collapsed.get(group)
        collapsed[group] = days if current is None else min(current, days)
    return collapsed


def _days_since(moment: datetime | None, now: datetime) -> int | None:
    """Días enteros entre `moment` y ahora, o `None`.

    `as_utc` en las dos puntas y no `.replace(tzinfo=...)`: las columnas vuelven aware
    de Postgres y naive de SQLite, y afirmarles UTC encima corre el instante tantas
    horas como tenga el offset — es el bug que `app/core/clock.py` documenta.

    Nunca negativo: una fecha futura (un registro cargado con fecha de mañana) daría
    "hace -1 días", y todo lo que lea esto lo compara contra una ventana.
    """
    if moment is None:
        return None
    return max(0, (as_utc(now) - as_utc(moment)).days)


def _trend(values: Sequence[float | None], days: int) -> Trend | None:
    """La tendencia de una columna opcional, o `None` si no alcanza para tenerla.

    Los `None` se saltean en vez de contarse: son filas donde la persona anotó otra
    cosa (sueño sin peso, peso sin grasa), no mediciones de cero. Con menos de
    `_MIN_TREND_POINTS` no hay dirección y no se inventa una.

    Extremos y no regresión: dos puntos y una recta serían la misma cuenta, y con
    mediciones esporádicas una pendiente sugiere más precisión de la que hay.
    """
    present = [float(v) for v in values if v is not None]
    if len(present) < _MIN_TREND_POINTS:
        return None
    return Trend(first=present[0], last=present[-1], days=days, points=len(present))


def _blood_panel(db: Session, user_id: int, today: date) -> BloodPanel | None:
    latest = BloodAnalysisService(db).get_latest_analysis(user_id)
    if latest is None or not latest.values_json:
        return None
    age = (today - latest.analysis_date).days if latest.analysis_date else None
    return BloodPanel(
        values=latest.values_json,
        analysis_date=latest.analysis_date,
        #: Igual que `_days_since`: un panel fechado mañana no tiene antigüedad
        #: negativa.
        age_days=max(0, age) if age is not None else None,
    )


def _macro_totals(
    items: Sequence[tuple[datetime, MealItemConsumed]], today: date, now: datetime
) -> tuple[MacroTotals, MacroTotals]:
    """Separa los ítems en "hoy" y "los días anteriores", y suma los dos lados.

    Un solo criterio de día, y es el **local** (`to_local(...).date()`): agrupar los
    días pasados por su fecha UTC mientras "hoy" se separa con los límites locales son
    dos definiciones de día en la misma función, y con el proceso en UTC —que es como
    corre en el contenedor— una cena de las 22:00 de acá le sumaba los macros al día
    siguiente. Es la clase de bug que `app/core/clock.py` existe para terminar.

    La base se devuelve **por día registrado**, no por día del calendario: dividir por
    14 a alguien que anota tres veces por semana le da una base cuatro veces más baja
    que su día real, y entonces cualquier día normal parece un exceso. Los días sin
    registro no son días de ayuno, son días sin datos.

    Y de cada día anterior entra solo lo anotado **hasta esta hora del día**, porque el
    día de hoy siempre está a medio andar. El job corre 7:40 y 18:40
    (`scheduler._SCHEDULE`): a las 18:40 la cena de hoy no pasó, así que comparar el
    parcial de hoy contra un promedio de días completos no mide lo que se comió, mide qué
    hora es — y le dice "hoy vas liviano de proteína" todas las tardes a quien cena
    fuerte, que es precisamente el consejo que la app no debería dar. Con el corte los
    dos lados son "hasta acá" y la diferencia vuelve a significar algo.

    Dos consecuencias que conviene esperar en vez de descubrir. A las 7:40 casi ningún
    día anterior tiene algo antes de esa hora, así que la base sale vacía y quien la lea
    tiene que callarse: `days_counted == 0` es su forma de decirlo. Y un día anterior
    cuyos registros son todos posteriores a la hora de corte **no cuenta como día
    registrado**, que es correcto: de ese día, hasta esta hora, no hay dato.
    """
    cutoff = to_local(now).time()
    by_day: dict[date, list[MealItemConsumed]] = {}
    today_items: list[MealItemConsumed] = []
    for moment, item in items:
        local = to_local(moment)
        if local.date() == today:
            today_items.append(item)
        elif local.time() <= cutoff:
            by_day.setdefault(local.date(), []).append(item)

    return _sum_macros(today_items), _average_macros(
        [_sum_macros(day_items) for day_items in by_day.values()]
    )


def _sum_macros(items: Sequence[MealItemConsumed]) -> MacroTotals:
    totals = {"calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0, "fiber_g": 0.0}
    counted = 0
    for item in items:
        grams = _grams_of(item)
        food = item.food_item
        if grams is None or food is None:
            continue
        portion = grams / 100.0
        totals["calories"] += (food.calories_per_100g or 0.0) * portion
        totals["protein_g"] += (food.protein_g or 0.0) * portion
        totals["carbs_g"] += (food.carbs_g or 0.0) * portion
        totals["fat_g"] += (food.fat_g or 0.0) * portion
        totals["fiber_g"] += (food.fiber_g or 0.0) * portion
        counted += 1
    return MacroTotals(
        **{key: round(value, 1) for key, value in totals.items()},
        items_counted=counted,
        items_total=len(items),
        #: Un día es un día cuando algo se pudo sumar. Los ítems que no se convirtieron
        #: a gramos dejan un día que existe y no mide nada, y contarlo como día
        #: registrado le pondría denominador a un promedio sin numerador.
        days_counted=1 if counted else 0,
    )


def _average_macros(days: Sequence[MacroTotals]) -> MacroTotals:
    """El promedio de varios días, contando solo los días que aportaron algo.

    Un día registrado en el que ningún ítem se pudo convertir a gramos no baja el
    promedio: no dice "comió menos", dice "no se pudo medir". Su cobertura sí se
    arrastra, para que quien lea la base sepa sobre cuántos ítems se calculó.
    """
    usable = [d for d in days if not d.is_empty]
    if not usable:
        return MacroTotals(
            items_counted=0,
            items_total=sum(d.items_total for d in days),
        )
    n = len(usable)
    return MacroTotals(
        calories=round(sum(d.calories for d in usable) / n, 1),
        protein_g=round(sum(d.protein_g for d in usable) / n, 1),
        carbs_g=round(sum(d.carbs_g for d in usable) / n, 1),
        fat_g=round(sum(d.fat_g for d in usable) / n, 1),
        fiber_g=round(sum(d.fiber_g for d in usable) / n, 1),
        items_counted=sum(d.items_counted for d in usable),
        items_total=sum(d.items_total for d in days),
        #: Los días que entraron en la división, que es lo mismo que `n`. Sale acá afuera
        #: porque es lo único que distingue un promedio de una anécdota: sin este número,
        #: el día único que alguien anotó hace diez días es indistinguible de una costumbre
        #: de dos semanas, y las dos cosas terminarían habilitando la misma tarjeta.
        days_counted=n,
    )


def _grams_of(item: MealItemConsumed) -> float | None:
    """Cuántos gramos fueron, o `None` si no se puede saber sin inventarlo.

    Tres caminos y ninguno más: `estimated_grams` cuando la captura lo trajo, la
    cantidad cuando su unidad es de peso, y nada en cualquier otro caso. "2 unidades"
    y "una taza" necesitarían `serving_size_g` para convertirse y esa columna está
    `NULL` en toda la base —el seed no la escribe y ningún servicio tampoco—, así que
    convertirlas sería elegir un número.

    Vale aclarar cuál es la unidad de los macros del catálogo, porque el comentario del
    modelo dice "per 100g (or per base_unit if non-weight)" y el catálogo sembrado
    dice otra cosa: la banana tiene 89 kcal con `base_unit="unit"`, que es el valor por
    100 g y no por banana. O sea que los macros son **siempre por 100 g**, y de ahí que
    el único camino sea llegar a gramos.
    """
    if item.estimated_grams is not None:
        return float(item.estimated_grams)
    if item.quantity is None or not item.unit:
        return None
    factor = _GRAMS_PER_UNIT.get(item.unit.strip().lower())
    if factor is None:
        return None
    return float(item.quantity) * factor
