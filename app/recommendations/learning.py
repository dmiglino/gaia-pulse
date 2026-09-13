"""El único lugar donde se escribe una señal de aprendizaje, y donde se leen.

Antes de esto había cuatro escrituras repartidas —el feedback explícito y las preferencias
en `SuggestionService`, la comida en `MealService`, el entrenamiento en `WorkoutService`—
cada una eligiendo por su cuenta el `signal_type` y el `entity_type`. El síntoma de que eso
no se sostiene está en el scorer: `repeated_purchase` figura en su lista de señales
positivas y **ningún código lo escribe nunca**, porque el lector y los escritores no
tenían un vocabulario en común que alguien pudiera revisar de un lado solo.

Lo que este módulo define es ese vocabulario:

- **El sujeto es `(subject_type, subject_name)`**, y es lo único contra lo que se aprende.
  `entity_type`/`entity_name` de `behavior_signals` son sus dos columnas —la tabla ya
  estaba diseñada para esto, no hizo falta migrarla—, y `suggestions.subject_type` /
  `subject_name` son las mismas dos del otro lado. Hasta la 4.4 el feedback se guardaba
  contra `title.lower()[:200]` y el scorer lo comparaba por bolsa de palabras contra
  título + texto + racional, así que rechazar *"Time to get moving!"* penalizaba —con 30%
  de solape— o filtraba —con 60%— cualquier candidato que compartiera esas palabras,
  cruzando categorías. Ahora el match es igualdad normalizada del sujeto.
- **La comparación es exacta después de normalizar**, no difusa. Un match difuso entre
  nombres de sujeto es la misma clase de error una capa más abajo: "pollo" y "pollo al
  horno" son dos alimentos distintos, y si conviene tratarlos igual eso es un problema de
  catálogo, no de scoring.
- **El tipo entra en la clave.** Un `muscle_group` llamado "core" y un alimento llamado
  "core" no son el mismo sujeto, y sin el tipo un negativo de uno silenciaba al otro.

No hay LLM acá ni modelo entrenado: es aritmética determinista sobre una tabla, que es lo
que la 4.4 se propuso y todo lo que hace falta para dos personas.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.clock import as_utc
from app.models.signal import BehaviorSignal
from app.repositories.suggestion_repo import BehaviorSignalRepository

logger = logging.getLogger(__name__)

#: Los tipos de sujeto que la app sabe aprender. No es decorativo: `record_signal` valida
#: contra esto, así que un `subject_type` mal escrito falla al escribir en vez de escribir
#: una señal que después nunca matchea nada y no le avisa a nadie. Es exactamente lo que
#: le pasó a `repeated_purchase` del otro lado del vocabulario.
SUBJECT_TYPES: frozenset[str] = frozenset(
    {
        "food",  # un alimento del catálogo, o el texto libre normalizado de una comida
        "exercise",  # una actividad o un ejercicio
        "muscle_group",  # el grupo muscular de una rotación
        "biomarker",  # la clave de un marcador de sangre
        "habit",  # una conducta sin objeto: constancia, descanso, registrar
    }
)

#: Las señales que valen como "esto le gusta / esto hace". La lista vivía dentro del
#: scorer, donde el escritor no la veía.
POSITIVE_SIGNAL_TYPES: frozenset[str] = frozenset(
    {
        "accepted_suggestion",
        "explicit_preference",
        "repeated_meal_choice",
        "repeated_purchase",
        "repeated_activity",
    }
)

#: Las que valen como "esto no". `ignored_suggestion` **no** está: es lo que graba tanto
#: descartar como posponer, y tratar un "más tarde" como un "no" es el bug que arregla la
#: 4.4.7. Que un descarte pese como negativo se decide por el signo de `value`, no por el
#: tipo.
#:
#: `rejected_activity` **tampoco** está, y no es un olvido: el scorer y el filtro de la v1
#: lo leían y ningún código lo escribió nunca —el mismo caso que `repeated_purchase`, del
#: que este módulo habla arriba—. Rechazar una sugerencia de actividad graba
#: `rejected_suggestion` con `subject_type="exercise"`, que es la misma información sin un
#: segundo tipo que mantener sincronizado. Que no vuelva a aparecer un tipo que se lee y no
#: se escribe lo cuida `test_every_signal_type_the_reader_knows_has_a_writer`.
NEGATIVE_SIGNAL_TYPES: frozenset[str] = frozenset({"rejected_suggestion"})


#: Cuántos días tarda una señal en pesar la mitad, según de dónde salió. Sin esto una
#: señal de hace ocho meses pesaba **exactamente igual** que la de ayer, así que un gusto
#: que cambió no se podía desaprender nunca: la app acumulaba, no aprendía.
#:
#: Las explícitas duran mucho más porque dicen algo sobre la persona ("no me gusta el
#: hígado"), mientras que una implícita dice algo sobre la semana ("comí pollo el martes").
#: Y las preferencias duras —lo imposible, lo que se evita— no viven acá: viven en
#: `RecommendationPreference`, que `apply_hard_constraints` lee sin descuento alguno. Esta
#: tabla es la parte blanda, la que tiene derecho a quedar vieja.
_HALF_LIFE_DAYS: dict[str, float] = {
    "explicit": 90.0,
    "implicit": 21.0,
}

#: Lo que se usa para un `source_type` que no está en el mapa —hoy `"inferred"`, que la
#: columna admite y nadie escribe—. Es el más corto a propósito: si no sabemos de dónde
#: salió una señal, que se desvanezca rápido es el error más barato.
_DEFAULT_HALF_LIFE_DAYS = 21.0

#: Hasta qué edad vale la pena traer señales de la base. Se deriva de la semivida más
#: larga en vez de ser un número aparte: la ventana de 30 días estaba escrita a mano en
#: `engine.py` **y** en `scorer.py`, y dos copias del mismo umbral es cómo empiezan a
#: discrepar. A cuatro semividas una señal conserva el 6% de su valor, que con el tope de
#: boost de 0.12 mueve el score menos de una centésima: el corte existe para acotar la
#: consulta, no para decidir nada.
SIGNAL_HORIZON_DAYS: int = int(4 * max(_HALF_LIFE_DAYS.values()))

#: Un "no" explícito saca al sujeto de la lista mientras conserve al menos la mitad de su
#: peso — es decir, durante una semivida— y después solo pesa en el score. Es el mismo
#: número que ya está declarado arriba y no un umbral nuevo: filtrar es más caro que
#: puntuar (el candidato no llega a existir), así que deja de hacerse antes.
_FILTER_DECAY_FLOOR = 0.5


def half_life_days(source_type: str) -> float:
    """La semivida que le corresponde a una señal según su `source_type`."""
    return _HALF_LIFE_DAYS.get(source_type, _DEFAULT_HALF_LIFE_DAYS)


def decay_factor(signal: BehaviorSignal, *, now: datetime | None = None) -> float:
    """Qué fracción de su valor original conserva *signal* hoy: `0.5 ** (edad/semivida)`.

    `created_at` lo pone la base (`server_default=func.now()`), así que una señal recién
    grabada y todavía no volcada no tiene fecha: se la cuenta entera, que es lo que es.
    """
    if signal.created_at is None:
        return 1.0
    reference = now or datetime.now(tz=timezone.utc)
    age_days = (reference - as_utc(signal.created_at)).total_seconds() / 86400.0
    if age_days <= 0:
        return 1.0
    return float(0.5 ** (age_days / half_life_days(signal.source_type)))


def signal_weight(signal: BehaviorSignal, *, now: datetime | None = None) -> float:
    """El valor de *signal* descontado por su edad. Conserva el signo."""
    return float(signal.value) * decay_factor(signal, now=now)


def normalize_subject(name: str) -> str:
    """El nombre de un sujeto en su forma comparable.

    Minúsculas, sin tildes, sin puntuación y con los espacios colapsados. Sin tildes
    porque los dos lados del match tienen origen distinto —el catálogo de `FoodItem`
    escribe "brócoli" y el texto libre de una captura escribe "brocoli"— y un sujeto que
    no matchea consigo mismo no enseña nada y no se queja.
    """
    decomposed = unicodedata.normalize("NFD", name.strip().lower())
    without_accents = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", without_accents)).strip()


def subject_key(subject_type: str, subject_name: str) -> tuple[str, str]:
    """La clave con la que se comparan dos sujetos: el tipo, más el nombre normalizado."""
    return (subject_type.strip().lower(), normalize_subject(subject_name))


def candidate_subject(candidate: dict[str, Any]) -> tuple[str, str] | None:
    """El sujeto de un candidato de generador, o `None` si no declaró ninguno.

    `None` significa "esta sugerencia no participa del aprendizaje": no recibe boost, no
    recibe penalización y no se puede filtrar por señales. Deliberadamente **no** hay
    fallback al título — el fallback al título es el bug que la 4.4 arregla, y dejarlo
    como red de contención lo dejaría vivo para siempre en la rama que nadie mira.
    Que ningún generador se olvide de declararlo lo cuida un test que los recorre a todos
    (`TestEveryCandidateDeclaresItsSubject`), no un `or` acá.
    """
    subject_type = candidate.get("subject_type")
    subject_name = candidate.get("subject_name")
    if not subject_type or not subject_name:
        return None
    return subject_key(str(subject_type), str(subject_name))


def record_signal(
    db: Session,
    *,
    user_id: int,
    signal_type: str,
    subject_type: str,
    subject_name: str,
    value: float,
    source_type: str,
    source_entity_type: str | None = None,
    source_entity_id: int | None = None,
    context: dict[str, Any] | None = None,
) -> BehaviorSignal | None:
    """Graba una señal de *user_id* sobre un sujeto. Devuelve `None` si no había sujeto.

    Único punto de escritura. Valida el `subject_type` contra `SUBJECT_TYPES` y normaliza
    el nombre antes de guardarlo, así que lo que queda en la tabla ya está en la forma en
    que se va a leer: normalizar solo del lado de la lectura deja que dos filas que son el
    mismo sujeto se cuenten como dos.

    Un nombre vacío devuelve `None` en vez de grabar: una señal sin sujeto es una fila que
    ocupa lugar, entra en la ventana de 30 días y no puede matchear nada.

    *user_id* siempre, nunca `household_id`: el aprendizaje es por persona porque Diego y
    Rocío no tienen los mismos gustos, y porque es la regla 4 de `AGENTS.md`.
    """
    if subject_type not in SUBJECT_TYPES:
        raise ValueError(
            f"subject_type {subject_type!r} desconocido; los válidos son "
            f"{sorted(SUBJECT_TYPES)}"
        )
    normalized = normalize_subject(subject_name)
    if not normalized:
        logger.debug(
            "Señal %s de user %d descartada: sujeto vacío (%r)",
            signal_type,
            user_id,
            subject_name,
        )
        return None

    return BehaviorSignalRepository(db).record(
        user_id=user_id,
        signal_type=signal_type,
        entity_type=subject_type,
        entity_name=normalized,
        value=value,
        source_type=source_type,
        source_entity_type=source_entity_type,
        source_entity_id=source_entity_id,
        context=context,
    )


def net_affinity(
    signals: list[BehaviorSignal], *, now: datetime | None = None
) -> dict[tuple[str, str], float]:
    """Cuánto le gusta cada sujeto, sumando lo positivo y lo negativo de *signals*.

    Se suma en vez de clasificar cada señal por separado porque un mismo sujeto puede
    tener las dos cosas —comió pollo seis veces y rechazó una sugerencia de pollo— y con
    dos listas separadas el candidato recibía el boost **y** la penalización, que es un
    resultado que no significa nada. La suma neta al menos dice para qué lado.

    Cada señal entra con su peso descontado por la edad (`signal_weight`), así que lo
    reciente manda y lo viejo se apaga solo. Antes cada fila valía su `value` crudo para
    siempre, y el resultado era un promedio de toda la historia: seis meses de pollo no
    los podían mover dos semanas de otra cosa.

    Quién decide el signo es `value`, no el tipo: `POSITIVE_SIGNAL_TYPES` y
    `NEGATIVE_SIGNAL_TYPES` están para que el vocabulario sea revisable de un lado solo, y
    para dejar afuera del cómputo lo que no es una opinión —`ignored_suggestion`, que graba
    tanto descartar como posponer—.
    """
    reference = now or datetime.now(tz=timezone.utc)
    totals: dict[tuple[str, str], float] = {}
    for signal in signals:
        if signal.signal_type not in POSITIVE_SIGNAL_TYPES | NEGATIVE_SIGNAL_TYPES:
            continue
        key = subject_key(signal.entity_type, signal.entity_name)
        if not key[1]:
            continue
        totals[key] = totals.get(key, 0.0) + signal_weight(signal, now=reference)
    return totals


def rejected_subjects(
    signals: list[BehaviorSignal], *, now: datetime | None = None
) -> set[tuple[str, str]]:
    """Los sujetos con un rechazo explícito reciente: lo que no se vuelve a ofrecer.

    Más estricto que un neto negativo a propósito. El neto lo puede poner negativo un
    descarte —"lo vi y lo cerré"—, y eso baja el score; sacar un candidato de la lista
    antes de puntuarlo pide que la persona haya dicho que no.

    Y el "no" caduca. Vale mientras la señal conserve al menos la mitad de su peso, o sea
    durante una semivida: pasado eso el sujeto vuelve a la lista y el rechazo sigue
    contando, pero solo en el score. Sin esto, ampliar la ventana de lectura para que el
    decaimiento tenga de qué decaer habría convertido un "no" de hace once meses en un
    veto permanente — el problema que la 4.4.2 vino a resolver, al revés.
    """
    reference = now or datetime.now(tz=timezone.utc)
    return {
        subject_key(s.entity_type, s.entity_name)
        for s in signals
        if s.signal_type in NEGATIVE_SIGNAL_TYPES
        and float(s.value) < 0
        and decay_factor(s, now=reference) >= _FILTER_DECAY_FLOOR
    }
