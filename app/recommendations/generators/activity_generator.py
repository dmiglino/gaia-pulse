"""Activity / workout suggestion generator.

Generates activity suggestions for a user based on:
- Days since last workout (rest vs. activity nudge)
- Per-muscle-group recovery windows (what is rested, what is still recovering)
- The seeded `ExerciseType` catalog, filtered by what the person can and wants to do
- Explicit RecommendationPreference entries

Las dos ideas que hay que tener a mano al leerlo:

**Una ventana es una ventana.** Hasta la 4.5.2 `_MUSCLE_RECOVERY` se usaba solo como lista
de claves: se restaba el conjunto de grupos recientes al conjunto de todos y se tomaba
`sorted(...)[0]`, así que el músculo sugerido era **casi siempre "back"** y los números de
recuperación —que estaban escritos— no decidían nada. Ahora cada grupo se compara contra *su*
ventana y gana el que hace más tiempo que la pasó.

**El catálogo es de ejercicios, la lista era de actividades.** Las ocho actividades
hardcodeadas ("walking", "gym", "hiit") no son la misma clase de cosa que las veinte filas de
`ExerciseType` ("Bench Press", "Lat Pulldown"): "andá en bici" y "hacé Barbell Row" no son la
misma tarjeta. El eje que las ordena es `ExerciseType.category` (`strength`/`cardio`/
`flexibility`), no el nombre.
"""

from __future__ import annotations

import logging
from typing import Any

from app.models.suggestion import RecommendationPreference
from app.models.user import User
from app.models.workout import ExerciseType
from app.recommendations.context import UserContext
from app.recommendations.learning import normalize_muscle_group

logger = logging.getLogger(__name__)

_REST_DAY_THRESHOLD = 3  # Suggest workout if inactive for this many days
_MAX_SUGGESTIONS = 6

#: Cuántos días tarda cada grupo en estar listo otra vez. Ahora se usa **como ventana** y no
#: como lista de claves, que es todo el punto de la 4.5.2. Las claves son el vocabulario de
#: `learning.MUSCLE_GROUPS` —un test lo fija— y los dos valores nuevos respecto de la versión
#: vieja son los que el catálogo trae y el mapa del NLP no tenía: `arms` hereda los 2 días que
#: antes estaban repetidos en `triceps` y `biceps`, y `full_body` toma 3, el más largo de los
#: que lo componen, porque una sesión de cuerpo entero incluye piernas.
#:
#: Hay **tres** lugares que tienen que hablar del mismo conjunto de grupos: este mapa, el
#: orden de `_ROTATION_PRIORITY` y `learning.MUSCLE_GROUPS`. Que coincidan lo fija
#: `tests/test_activity_generator.py::TestVocabularyAndWindowsAgree` y no un `assert` al
#: importar: un módulo que explota al cargar se lleva la app entera por un typo en un
#: diccionario, y el test falla antes de que el typo salga del árbol.
_RECOVERY_DAYS: dict[str, int] = {
    "chest": 2,
    "shoulders": 2,
    "arms": 2,
    "back": 2,
    "legs": 3,
    "core": 1,
    "cardio": 1,
    "full_body": 3,
}

#: La ventana de un grupo que no está en el mapa. Existe para que un grupo desconocido
#: —`WorkoutExercise.muscle_group` es texto libre y `normalize_muscle_group` deja pasar lo que
#: no reconoce— tenga un comportamiento y no un `KeyError` ni un silencio. Dos días es el valor
#: más común del mapa: para algo que no sabemos qué es, la ventana típica.
_DEFAULT_RECOVERY_DAYS = 2

#: El orden en que se rompen los empates. Es lo que reemplaza al `sorted(...)[0]` alfabético:
#: cuando dos grupos hace exactamente el mismo tiempo que pasaron su ventana, gana el de más
#: arriba. El criterio es músculo grande primero —una sesión de piernas o de espalda mueve más
#: que una de core— y `cardio` y `full_body` van al final porque son las que se cubren solas
#: haciendo cualquier otra cosa. Está declarado y no derivado del mapa a propósito: un `dict`
#: ordenado por inserción convertiría "cuánto tarda en recuperarse" en "qué tan importante es",
#: que son dos cosas distintas.
_ROTATION_PRIORITY: tuple[str, ...] = (
    "legs",
    "back",
    "chest",
    "shoulders",
    "arms",
    "core",
    "cardio",
    "full_body",
)

#: Hasta cuántos días después de entrenar se considera que una sesión de alta intensidad cae
#: demasiado encima de la anterior.
_HIGH_INTENSITY_REST_DAYS = 1

#: Cuántas tarjetas de catálogo salen por corrida. Dos y no veinte: el catálogo entero
#: convertido en tarjetas sería una lista, y una lista no es una sugerencia.
_MAX_CATALOG_CARDS = 2

#: Lo que se considera "hace mucho" para un grupo que nunca se entrenó. `inf` y no un número
#: grande porque no es "hace 999 días": es que la pregunta no aplica, y el orden tiene que
#: ponerlo primero sin que ningún historial real pueda alcanzarlo.
_NEVER_TRAINED = float("inf")


def _recovery_window(group: str) -> int:
    """Los días de recuperación de *group*, o el default documentado si no se conoce."""
    return _RECOVERY_DAYS.get(group, _DEFAULT_RECOVERY_DAYS)


def _recovering_groups(context: UserContext) -> set[str]:
    """Los grupos cuyo último estímulo está **todavía dentro de su propia ventana**.

    Acá había una consulta —y `days_since_last_workout` era otra— que preguntaba lo mismo que
    el contexto ya trae. El corte es `< ventana` y no `<= ventana` porque eso es lo que hacía
    el `WHERE timestamp >= ahora − days` que reemplazó: los días se truncan, así que un
    estímulo de hace 2.5 días da `days_since == 2` y queda **fuera** de una ventana de 2.

    Lo que cambió en la 4.5.2 es de dónde sale el número: antes era `_OVERTRAINING_DAYS = 2`
    plano para los ocho grupos, y ahora es la ventana de cada uno. Con eso `core` (1 día) se
    libera al día siguiente y `legs` (3) no.
    """
    return {
        group
        for group, since in context.days_since_muscle_group.items()
        if since < _recovery_window(group)
    }


def _overdue_days(context: UserContext, group: str) -> float:
    """Cuánto hace que *group* pasó su ventana. Negativo = todavía recuperando."""
    since = context.days_since_training(group)
    if since is None:
        return _NEVER_TRAINED
    return since - _recovery_window(group)


def _priority_index(group: str) -> int:
    """La posición de *group* en el desempate. Un grupo desconocido va al final."""
    try:
        return _ROTATION_PRIORITY.index(group)
    except ValueError:
        return len(_ROTATION_PRIORITY)


def _rotation_target(context: UserContext) -> tuple[str, int | None] | None:
    """El grupo que más hace que pasó su ventana, y hace cuántos días se entrenó.

    Devuelve `(grupo, días_desde)` con `días_desde=None` cuando **nunca** se entrenó, o `None`
    cuando todos los grupos están dentro de su ventana (que es lo que pasa el día después de
    una sesión de cuerpo entero: no hay nada honesto que proponer).

    Recorre `MUSCLE_GROUPS` en el orden de `_ROTATION_PRIORITY` y no el mapa del contexto: el
    conjunto de lo que se **propone** es cerrado, así que un grupo desconocido que llegó por la
    columna de una captura cuenta como estímulo (lo lee `_recovering_groups`) pero nunca sale
    propuesto. Las dos direcciones son distintas a propósito y están documentadas en
    `learning.normalize_muscle_group`.
    """
    best_key: tuple[float, int] | None = None
    best_group: str | None = None
    for group in _ROTATION_PRIORITY:
        overdue = _overdue_days(context, group)
        if overdue < 0:
            continue
        # `-overdue` para que el máximo sea el mínimo de la clave, y el índice de prioridad
        # como segundo criterio: es el desempate estable y no alfabético que pide la 4.5.2.
        key = (-overdue, _priority_index(group))
        if best_key is None or key < best_key:
            best_key, best_group = key, group
    if best_group is None:
        return None
    return best_group, context.days_since_training(best_group)


def _catalog_rows(
    context: UserContext,
    *,
    recovering: set[str],
    excluded: set[str],
    days_since: int | None,
) -> list[ExerciseType]:
    """Las filas del catálogo que se pueden proponer hoy, ya ordenadas y una por categoría.

    El orden es por cuánto hace que el grupo del ejercicio pasó su ventana, con el mismo
    desempate que la rotación. Ordenar por nombre —que es lo que devuelve
    `ExerciseTypeRepository.list_all()`— reproduciría el bug de la 4.5.2 con otra cara: las dos
    primeras filas del seed en orden alfabético son "Barbell Row" y "Bench Press", así que la
    tarjeta de catálogo sería *siempre* la misma dos.

    Una por `category` porque es el eje que distingue una clase de tarjeta de otra: dos
    ejercicios de fuerza son la misma propuesta con distinto nombre, y fuerza más cardio son
    dos propuestas.
    """
    eligible: list[tuple[tuple[float, int, str], ExerciseType]] = []
    for row in context.exercise_catalog:
        name = row.name.strip().lower()
        if not name or name in excluded:
            continue
        group = normalize_muscle_group(row.muscle_group or "")
        if group and group in recovering:
            continue
        if (
            days_since is not None
            and days_since <= _HIGH_INTENSITY_REST_DAYS
            and (row.intensity or "").lower() == "high"
        ):
            continue
        #: Sin grupo declarado no hay nada que esté esperando su turno, así que la fila entra
        #: con `0.0`: por detrás de todo lo atrasado y por delante de nada. Ordenar sin grupo
        #: como si nunca se hubiera entrenado le daría la primera tarjeta a la única fila del
        #: catálogo que no sabemos qué trabaja.
        overdue = _overdue_days(context, group) if group else 0.0
        eligible.append(((-overdue, _priority_index(group), name), row))

    chosen: list[ExerciseType] = []
    seen_categories: set[str] = set()
    for _, row in sorted(eligible, key=lambda item: item[0]):
        category = (row.category or "other").strip().lower()
        if category in seen_categories:
            continue
        seen_categories.add(category)
        chosen.append(row)
        if len(chosen) >= _MAX_CATALOG_CARDS:
            break
    return chosen


def _get_impossible_activities(user: User, preferences: list[RecommendationPreference]) -> set[str]:
    impossible: set[str] = set(a.lower() for a in (user.impossible_activities_json or []))
    for pref in preferences:
        if pref.item_type == "exercise" and pref.preference_signal == "impossible":
            impossible.add(pref.item_name.lower())
    return impossible


def _get_disliked_activities(user: User, preferences: list[RecommendationPreference]) -> set[str]:
    disliked: set[str] = set(a.lower() for a in (user.disliked_activities_json or []))
    for pref in preferences:
        if pref.item_type == "exercise" and pref.preference_signal in ("dislikes", "avoid"):
            disliked.add(pref.item_name.lower())
    return disliked


def _get_preferred_activities(user: User, preferences: list[RecommendationPreference]) -> list[str]:
    preferred = list(a.lower() for a in (user.preferred_activities_json or []))
    for pref in preferences:
        if (
            pref.item_type == "exercise"
            and pref.preference_signal in ("likes", "preferred")
            and pref.item_name.lower() not in preferred
        ):
            preferred.append(pref.item_name.lower())
    return preferred


def generate(
    user: User,
    preferences: list[RecommendationPreference],
    context: UserContext,
    limit: int = _MAX_SUGGESTIONS,
) -> list[dict[str, Any]]:
    """Generate activity suggestions for *user*.

    Returns a list of suggestion dicts.

    Sin `db`: todo lo que este generador leía de la base lo trae el contexto, así que pedir
    una sesión sería pedir permiso para volver a consultar por su cuenta.
    """
    suggestions: list[dict[str, Any]] = []

    days_since = context.days_since_last_workout
    recovering = _recovering_groups(context)
    impossible = _get_impossible_activities(user, preferences)
    disliked = _get_disliked_activities(user, preferences)
    preferred = _get_preferred_activities(user, preferences)

    # ── 1. Rest nudge: if trained yesterday / same day ─────────────────────
    if days_since is not None and days_since == 0:
        suggestions.append(
            {
                "category": "activity",
                #: Un hábito, no un ejercicio: esta tarjeta no propone una actividad,
                #: propone no hacer ninguna. Con `subject_type="exercise"` un rechazo acá
                #: habría enseñado que no le gusta el descanso *como ejercicio*, y el
                #: sujeto "rest" habría chocado con un ejercicio que se llamara igual.
                "subject_type": "habit",
                "subject_name": "rest day",
                "title": "Consider a rest or light activity today",
                "text": (
                    "You already worked out today. A short walk or yoga session can support "
                    "recovery without over-training."
                ),
                #: Por qué esta tarjeta: `days_since == 0`. No hay número más que ese, y decir
                #: "el descanso es esencial para la reparación muscular" era afirmar
                #: fisiología general en el lugar donde va la razón de **esta** tarjeta.
                "rationale": (
                    "You already logged a session today, so what is missing is recovery, "
                    "not another stimulus."
                ),
                "evidence_summary": "Workout logged today.",
                "confidence": 0.75,
                "source_type": "rule",
            }
        )

    # ── 2. Activity nudge: if been resting for too long ────────────────────
    if days_since is None or days_since >= _REST_DAY_THRESHOLD:
        reason = (
            "No workouts logged yet"
            if days_since is None
            else f"{days_since} days since last workout"
        )
        #: Los dos casos se dicen distinto por lo mismo que la rotación los distingue: "no hay
        #: nada registrado" y "hace ocho días" son dos situaciones, y el umbral solo aplica a
        #: la segunda. Nombrarlo es lo que el texto no dice y lo que contesta por qué hoy.
        nudge_why = (
            "You have no sessions on record at all, which is where this nudge starts."
            if days_since is None
            else (
                f"{days_since} days without a session, past the {_REST_DAY_THRESHOLD}-day "
                "mark where this nudge starts."
            )
        )
        suggestions.append(
            {
                "category": "activity",
                #: El candidato que le da nombre al bug: rechazar *"Time to get moving!"*
                #: guardaba la frase entera como entidad aprendida, y "moving" y "boost"
                #: y "energy" alcanzaban para penalizar o filtrar sugerencias de comida.
                #: Su sujeto real es la constancia, y rechazarlo enseña una sola cosa:
                #: esta persona no quiere que la empujen a entrenar.
                "subject_type": "habit",
                "subject_name": "workout consistency",
                "title": "Time to get moving!",
                "text": (
                    "It's been a few days since your last workout. "
                    "Even a 30-minute session can boost your mood and energy."
                ),
                "rationale": nudge_why,
                "evidence_summary": reason + ".",
                "confidence": 0.8,
                "source_type": "rule",
            }
        )

    # ── 3. Muscle group rotation: the group most overdue for a stimulus ────
    target = _rotation_target(context)
    if target is not None:
        group, group_days_since = target
        #: Dos tarjetas distintas y no una con el número interpolado: "nunca entrenaste esto"
        #: y "hace ocho días que no entrenás esto" son propuestas diferentes —una es empezar y
        #: la otra es volver—, y el mapa del contexto distingue el caso a propósito (un grupo
        #: ausente nunca se entrenó). Decirle "volvé" a alguien que nunca fue es cómo una app
        #: revela que no está mirando.
        if group_days_since is None:
            text = (
                f"You haven't logged any {group} work yet. Adding it rounds out your "
                "routine and helps keep both sides of your body evenly loaded."
            )
            evidence = f"No {group} stimulus on record."
            rotation_why = (
                f"{group} has no stimulus on record, which puts it ahead of every group "
                "that has one."
            )
        else:
            text = (
                f"It's been {group_days_since} days since your last {group} session, past "
                f"the {_recovery_window(group)}-day recovery window. It's rested and ready."
            )
            evidence = (
                f"Last {group} stimulus: {group_days_since} days ago "
                f"(recovery window: {_recovery_window(group)} days). "
                f"Still recovering: {', '.join(sorted(recovering)) or 'none'}."
            )
            rotation_why = (
                f"Of the groups outside their recovery window, {group} is the one that has "
                f"waited longest past its own {_recovery_window(group)} days."
            )
        suggestions.append(
            {
                "category": "activity",
                "subject_type": "muscle_group",
                "subject_name": group,
                "title": f"Train {group} today",
                "text": text,
                "rationale": rotation_why,
                "evidence_summary": evidence,
                "confidence": 0.65,
                "source_type": "rule",
            }
        )

    # ── 4. Preferred activities ─────────────────────────────────────────────
    for activity in preferred[:3]:
        if activity in impossible or activity in disliked:
            continue
        suggestions.append(
            {
                "category": "activity",
                "subject_type": "exercise",
                "subject_name": activity,
                "title": f"Go {activity}",
                "text": f"You enjoy {activity} — it's a great option for today's workout.",
                #: Lo que decide esta tarjeta es la **precedencia**, no la actividad: la
                #: lista de preferidas se recorre antes que el catálogo y sus nombres se
                #: excluyen de él (`excluded=... | set(preferred)`), así que estar en la
                #: lista es la razón entera por la que esta salió y no una del catálogo.
                "rationale": (
                    f"{activity} is on the list of activities you said you like, which is "
                    "ranked ahead of the catalog."
                ),
                "evidence_summary": f"User preference signal: likes/preferred for '{activity}'.",
                "confidence": 0.8,
                "source_type": "preference",
            }
        )

    # ── 5. Catalog suggestions, from `ExerciseType` ─────────────────────────
    if not context.exercise_catalog:
        #: Catálogo vacío → **ninguna** tarjeta con nombre de ejercicio, y no una lista de
        #: repuesto: las ocho actividades hardcodeadas que la 4.5.2 borró volverían disfrazadas
        #: de fallback y el bug seguiría vivo en la rama que nadie mira. Las tarjetas de
        #: descanso, constancia y rotación no necesitan un nombre, así que el generador sigue
        #: teniendo algo que decir. Un `warning` y no un `error` porque es una base sin sembrar
        #: —el caso normal en los tests—, no una falla.
        logger.warning(
            "Exercise catalog is empty for user %s: skipping named activity suggestions. "
            "Run seed.py to populate ExerciseType.",
            user.id,
        )
    else:
        for row in _catalog_rows(
            context,
            recovering=recovering,
            excluded=impossible | disliked | set(preferred),
            days_since=days_since,
        ):
            name = row.name.strip().lower()
            group = normalize_muscle_group(row.muscle_group or "")
            intensity = (row.intensity or "moderate").strip().lower()
            category = (row.category or "other").strip().lower()
            #: Por qué **esta** fila y no otra del catálogo, dicho sin exagerar lo que el
            #: orden garantiza. `_catalog_rows` ordena todo el pool elegible por atraso y
            #: después se queda con la primera de cada `category`, así que lo cierto de la
            #: segunda tarjeta no es "el grupo más atrasado de todos" —eso es la primera—
            #: sino "el más atrasado **de su categoría**". Y "abiertas hoy" y no "del
            #: catálogo": lo imposible, lo que no gusta, lo preferido y lo de alta
            #: intensidad después de una sesión ya quedaron afuera antes de ordenar.
            catalog_why = (
                (
                    f"{group} is not inside its recovery window, and of the {category} "
                    "options open to you today it is the one that has waited longest."
                )
                if group
                else (
                    "It declares no muscle group, so there is nothing overdue to rank it "
                    f"by: it is simply the first {category} option in today's order."
                )
            )
            suggestions.append(
                {
                    "category": "activity",
                    #: El ejercicio, no su grupo: el grupo ya es el sujeto de la tarjeta de
                    #: rotación, y dos tarjetas con el mismo sujeto se estorban entre sí en el
                    #: dedup y se aprenden como una sola cosa.
                    "subject_type": "exercise",
                    "subject_name": name,
                    "title": f"Try {row.name} today",
                    "text": (
                        f"A {intensity}-intensity {category} exercise"
                        + (f" for {group}" if group else "")
                        + ". It fits what your week is missing."
                    ),
                    "rationale": catalog_why,
                    "evidence_summary": (
                        f"Catalog exercise ({category}, {intensity} intensity"
                        + (f", {group}" if group else "")
                        + "). Days since last workout: "
                        + ("none on record" if days_since is None else str(days_since))
                        + "."
                    ),
                    "confidence": 0.55,
                    "source_type": "rule",
                }
            )

    # De-duplicate and respect limit
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for s in suggestions:
        if s["title"] not in seen:
            seen.add(s["title"])
            unique.append(s)

    return unique[:limit]
