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
- **Se aprende en dos niveles: el sujeto y su atributo.** Rechazar brócoli, coliflor y
  kale enseña algo sobre las verduras, y eso es lo que permite acertar con una espinaca
  que la persona nunca vio en una sugerencia. El nivel atributo pide más evidencia, pesa
  menos que la evidencia directa, no se cuenta a sí mismo y **no filtra** nada: generalizar
  para ordenar es útil, generalizar para vetar es ponerle en la boca un "no" que no dijo.
- **Y en un tercer eje, que no es un nivel más sino una franja: *cuándo*.** Un gusto tiene
  hora. El café del desayuno y el café de la cena son el mismo sujeto con dos respuestas
  distintas, y hasta acá la app las promediaba en una sola. Lo que se compara es el sujeto
  **en** la franja contra el mismo sujeto **fuera** de ella, así que lo que se aprende es
  la diferencia entre horas y no la popularidad del sujeto, que ya la cobra el nivel
  puntual. Es un refinamiento, no una generalización: no pesa menos, solo sabe menos.
- **Y una última cosa que no es un gusto: la saciedad.** Las mismas filas que dicen "esto
  le gusta" dicen también "esto lo comió ayer", y son dos cosas con dos relojes. Hasta acá
  solo se leía la primera, así que el alimento de todos los días acumulaba decenas de
  positivos y el bucle empujaba a repetir, no a variar. La saciedad se descuenta con
  semivida de día y medio, la producen solo las señales de **acto** —lo que se comió, se
  hizo o se compró— y nunca las de **dicho**, y como los otros ejes no filtra: haber comido
  milanesas ayer no es un "no" a las milanesas, es un "hoy otra cosa".
- **Y un negativo que nadie tuvo que apretar: la ausencia.** Sugerir un alimento y que no
  aparezca en ninguna comida durante una semana no es un rechazo, pero tampoco es un
  neutro: es la diferencia entre "no me interesa" y "todavía no lo vi". La escribe un
  barrido diario (`run_absence_sweep`, en `app/jobs/suggestion_jobs.py`) porque nada corre
  "siete días después" por sí solo, pesa un quinto de un "no" deliberado, y es la razón por
  la que el veto dejó de ser "hay un negativo vivo" y pasó a ser "hay medio punto de
  negativo vivo": una ausencia sola no puede sacar nada de la lista y tres sí. Sin ese
  umbral la señal sería peor que no tenerla —un veto permanente por una tarjeta que la
  persona quizá no llegó a ver—, y por eso las dos mitades se escribieron juntas.

No hay LLM acá ni modelo entrenado: es aritmética determinista sobre una tabla, que es lo
que la 4.4 se propuso y todo lo que hace falta para dos personas.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.clock import as_utc
from app.models.signal import BehaviorSignal
from app.nlp import rules
from app.repositories.food_repo import FoodRepository
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
#:
#: `unused_suggestion` sí está, y es el único negativo que nadie apretó: lo escribe el
#: barrido de ausencias (`run_absence_sweep`) cuando una sugerencia con sujeto pasó su
#: semana sin que el sujeto apareciera en ningún acto. Pesa `ABSENCE_VALUE`, un quinto de
#: un "no" deliberado, porque no dice "no quiero" sino "no pasó", y por eso el veto mide
#: peso negativo acumulado (`_FILTER_EVIDENCE_FLOOR`) en vez de "hay uno vivo".
ABSENCE_SIGNAL_TYPE = "unused_suggestion"

NEGATIVE_SIGNAL_TYPES: frozenset[str] = frozenset({"rejected_suggestion", ABSENCE_SIGNAL_TYPE})

#: Las señales que son un **acto** y no un dicho: lo que la persona comió, hizo o compró.
#: No es un vocabulario nuevo —son las mismas filas que ya cuentan como positivas—, es una
#: segunda lectura de las mismas: además de "le gusta" dicen "acabo de tener esto".
#:
#: `accepted_suggestion` y `explicit_preference` quedan afuera a propósito: decir que algo
#: te gusta no te llena. Y las tres que están, están todas, aunque la que motivó la 4.4.6
#: sea la comida: comprar leche ayer es una razón para no sugerir comprar leche hoy, y
#: repetir el mismo ejercicio tres días seguidos es la misma clase de error con otro cuerpo.
#: Que este conjunto no se despegue del otro lo cuida un test que verifica que sea un
#: subconjunto de `POSITIVE_SIGNAL_TYPES`.
CONSUMPTION_SIGNAL_TYPES: frozenset[str] = frozenset(
    {
        "repeated_meal_choice",
        "repeated_purchase",
        "repeated_activity",
    }
)

#: Los tipos de sujeto que existen **solo como atributo**: son a lo que generaliza un
#: sujeto puntual, y a propósito **no** están en `SUBJECT_TYPES`, así que `record_signal`
#: los rechaza y nunca hay una fila con estos tipos.
#:
#: Que no se graben es la decisión, no un pendiente. La otra opción era escribir una
#: segunda fila por comida con la categoría del alimento —es lo que la 4.4.1 dejó
#: anotado— y es peor por dos razones: congelaría la categoría del día en que se comió
#: (recategorizar la palta de "fat" a "fruit" no arreglaría nada de lo ya aprendido), y
#: solo aprendería de las comidas futuras, cuando lo que la app ya tiene son meses de
#: señales de alimentos cuya categoría el catálogo sabe hoy. Derivar en cada lectura es
#: retroactivo y se corrige solo. Y de paso no hay escritor que pueda olvidarse: el
#: atributo sale del catálogo, no de que tres servicios se acuerden de grabarlo.
ATTRIBUTE_SUBJECT_TYPES: frozenset[str] = frozenset(
    {
        "food_category",  # `FoodItem.category`: vegetable/fruit/protein/grain/dairy/…
    }
)

#: Las franjas horarias que una señal puede declarar en su `context_json["meal_type"]`.
#: Es una lista explícita y no "cualquier string" por un valor en particular: `"other"`,
#: que es el default de `MealEvent.meal_type` y por lo tanto lo que queda cuando la captura
#: no dijo la hora. Tratarlo como una franja más haría que cada comida sin hora argumentara
#: contra todas las franjas reales, que es lo contrario de lo que significa. Una señal sin
#: franja no dice nada sobre el reloj y no entra en el cómputo por ningún lado.
#:
#: `"brunch"` sí está, aunque ningún generador lo sugiera: lo produce `app/nlp/rules.py` al
#: leer una captura, y es una hora real del día. Que no se sugiera no lo hace mudo — sirve
#: como evidencia de *otra* franja cuando se evalúa el desayuno.
MEAL_SLOTS: frozenset[str] = frozenset({"breakfast", "brunch", "lunch", "snack", "dinner"})


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

#: Lo que se usa para un `source_type` que no está en el mapa —hoy `"inferred"`, que es lo
#: que graba el barrido de ausencias: nadie dijo nada y nadie hizo nada, la señal la deduce
#: la app—. Es el más corto a propósito: si no sabemos de dónde salió una señal, que se
#: desvanezca rápido es el error más barato. Que la ausencia caiga acá es deliberado y no
#: un olvido: una semana sin comer algo caduca antes que una preferencia dicha.
_DEFAULT_HALF_LIFE_DAYS = 21.0

#: Hasta qué edad vale la pena traer señales de la base. Se deriva de la semivida más
#: larga en vez de ser un número aparte: la ventana de 30 días estaba escrita a mano en
#: `engine.py` **y** en `scorer.py`, y dos copias del mismo umbral es cómo empiezan a
#: discrepar. A cuatro semividas una señal conserva el 6% de su valor, que con el tope de
#: boost de 0.12 mueve el score menos de una centésima: el corte existe para acotar la
#: consulta, no para decidir nada.
SIGNAL_HORIZON_DAYS: int = int(4 * max(_HALF_LIFE_DAYS.values()))

#: Qué tan fresca tiene que estar una señal negativa para poder sacar a un sujeto de la
#: lista: mientras conserve al menos la mitad de su peso, o sea durante una semivida.
#: Después solo pesa en el score. Es el mismo número que ya está declarado arriba y no un
#: umbral nuevo: filtrar es más caro que puntuar (el candidato no llega a existir), así que
#: deja de hacerse antes.
#:
#: Es una condición sobre **cada** señal, no sobre el conjunto: acota qué entra a la suma
#: que después mide `_FILTER_EVIDENCE_FLOOR`. Para un rechazo deliberado las dos son la
#: misma condición y por eso hasta la 4.4.10 alcanzaba con esta sola.
_FILTER_DECAY_FLOOR = 0.5

#: Cuánto pesa una ausencia. Negativo porque argumenta en contra, y un quinto de un "no"
#: deliberado (que vale `-1.0`) porque no es lo mismo: quien rechaza vio la tarjeta y dijo
#: que no; quien no usó una sugerencia pudo no haberla visto nunca. Cinco ausencias dicen
#: lo que un rechazo dice solo.
ABSENCE_VALUE = -0.2

#: Cuánto se espera antes de leer una ausencia como ausencia. Una semana es lo que tarda
#: un menú en dar la vuelta: menos que eso y "no lo comió" solo significa "todavía no le
#: tocó". Coincide con `scorer._RECENT_SUGGESTION_DAYS` —la ventana de la penalización por
#: diversidad— y la coincidencia no es casual (las dos preguntan "¿esto es reciente?"),
#: pero no se deriva de ella a propósito: esa mide cuánto tiempo una sugerencia estorba a
#: la siguiente, y esta cuánto tiempo hay que darle a alguien antes de contarle un no.
#: Si una cambia, la otra no tiene por qué seguirla.
ABSENCE_GRACE_DAYS = 7

#: De qué sujetos se puede afirmar una ausencia. Es un subconjunto de `SUBJECT_TYPES` y no
#: todos ellos porque una ausencia solo es falsable si el acto correspondiente se registra:
#: `food` lo escribe cada comida y `exercise` cada entrenamiento, así que "no apareció" es
#: una observación. `muscle_group` se graba solo cuando la captura trae la columna, así que
#: su ausencia sería falsa seguido; y `habit` y `biomarker` no tienen escritor de acto
#: ninguno, así que su ausencia no diría nada sobre la persona, solo sobre el esquema.
ABSENCE_SUBJECT_TYPES: frozenset[str] = frozenset({"food", "exercise"})

#: Cuánto peso negativo vivo hace falta para vetar un sujeto, además de la frescura que ya
#: pide `_FILTER_DECAY_FLOOR`. Existe para que la ausencia pueda entrar al vocabulario
#: negativo sin poder de veto propio: con este valor un rechazo deliberado se comporta
#: **exactamente** como antes (pesa `1.0 * decay`, y `decay >= 0.5` es la misma condición
#: que `peso >= 0.5`). Es el mismo número que el piso de decaimiento, y no está derivado de
#: él porque miden cosas distintas —uno es una fracción de peso, el otro una suma de
#: pesos—: que coincidan es lo que hace que el caso viejo no cambie.
#:
#: Y con estos valores la ausencia **nunca** llega a vetar, ni de a tres ni de a treinta.
#: No es una casualidad de la calibración sino una consecuencia de dos números que ya
#: estaban: entre dos ausencias del mismo sujeto no puede haber menos de 10 días
#: (`ABSENCE_GRACE_DAYS` para que la tarjeta se barra, más `SuggestionService._SNOOZE_DAYS`
#: para que el sujeto se destrabe y nazca otra), y el piso de frescura descarta todo lo que
#: pase de una semivida —21 días—. Caben tres ausencias vivas a la vez y suman como máximo
#: `0.2 * (1 + 0.5**(10/21) + 0.5**(20/21)) ≈ 0.447`. Tampoco pueden empujar a un rechazo
#: viejo por encima del umbral, porque un rechazo que conserva la mitad de su peso ya veta
#: solo y uno que no la conserva lo descarta el piso de frescura antes de sumar.
#:
#: O sea: hoy una ausencia solo puntúa. Se escribe así igual —y la suma es la regla, no un
#: caso especial de la ausencia— porque lo que el umbral tiene que garantizar es que un
#: negativo débil no vete, y esa garantía tiene que sobrevivir al día en que las tarjetas
#: caduquen o la gracia se acorte. Los tests que arman tres ausencias a mano fijan la
#: aritmética, no un camino alcanzable; el que fija lo alcanzable es
#: `test_the_sweep_can_never_veto_a_subject_on_its_own`.
_FILTER_EVIDENCE_FLOOR = 0.5

#: Cuánta evidencia hace falta para que la app esté a mitad de camino de estar segura.
#: Con este valor una sola observación pesa un tercio de lo que pesaría la certeza, dos
#: pesan la mitad y seis tres cuartos: **un descarte es una pista, seis son una regla**.
#:
#: Sin esto el ajuste era `knob * min(|suma|, 1.0)`, así que un único tap —accidental o
#: no— movía el score tanto como diez observaciones consistentes, y el tope estaba puesto
#: para que la suma no se desbordara, no para modelar cuánto sabemos. Ahora la fuerza de
#: lo aprendido es *para qué lado* (el promedio de las señales) por *cuánto lo sostiene*
#: (esto), que son dos preguntas distintas y hasta acá estaban sumadas en un solo número.
_EVIDENCE_HALF_SATURATION = 2.0

#: Lo mismo, pero para el nivel atributo: el triple de evidencia para llegar a la misma
#: confianza. Se deriva del valor puntual en vez de ser un número aparte, y el factor es 3
#: porque es el mínimo que se lee como patrón y no como coincidencia: rechazar brócoli
#: alcanza para aprender sobre el brócoli, y hacen falta tres verduras distintas para que
#: valga concluir algo sobre las verduras. Con dos, cualquier semana rara reescribiría una
#: categoría entera —y una categoría son treinta alimentos, no uno—.
_ATTRIBUTE_EVIDENCE_HALF_SATURATION = 3 * _EVIDENCE_HALF_SATURATION

#: Con qué semivida se olvida un "ya comí de esto": día y medio. Lo de ayer pesa dos
#: tercios, lo de anteayer un tercio, lo de la semana pasada nada. Es catorce veces más
#: corta que la semivida de una afinidad implícita (21 días) y ahí está toda la 4.4.6: la
#: misma fila mide dos cosas, y lo que las separa no es el dato sino el reloj con el que se
#: lo lee. Una comida es evidencia lenta de un gusto y evidencia rápida de que ya hubo
#: suficiente de eso; hasta acá solo se leía la primera.
_SATIETY_HALF_LIFE_DAYS = 1.5

#: Cuánto consumo reciente es "a mitad de camino de estar lleno". La misma vara que la
#: evidencia puntual, y no por comodidad: en una casa que come tres veces por día, dos
#: raciones descontadas son más o menos un día de haber comido eso, que es justo donde la
#: app debería empezar a ofrecer otra cosa.
_SATIETY_HALF_SATURATION = _EVIDENCE_HALF_SATURATION

#: A partir de qué dirección se dice "vas para este lado" en vez de "depende". No mueve
#: ningún score —el scorer usa la dirección entera— y existe solo para poder decirla en
#: palabras: es el ancho de la zona donde las señales se contradicen entre sí.
_DIRECTION_BAND_EDGE = 0.2


def half_life_days(source_type: str) -> float:
    """La semivida que le corresponde a una señal según su `source_type`."""
    return _HALF_LIFE_DAYS.get(source_type, _DEFAULT_HALF_LIFE_DAYS)


def decay_factor(
    signal: BehaviorSignal, *, now: datetime | None = None, half_life: float | None = None
) -> float:
    """Qué fracción de su valor original conserva *signal* hoy: `0.5 ** (edad/semivida)`.

    `created_at` lo pone la base (`server_default=func.now()`), así que una señal recién
    grabada y todavía no volcada no tiene fecha: se la cuenta entera, que es lo que es.

    *half_life* pisa la semivida que le tocaría por su `source_type`. Existe para un solo
    lector —la saciedad—, que mira exactamente las mismas filas con otro reloj. Es un
    parámetro y no una segunda función porque el decaimiento es el mismo: dos copias de
    `0.5 ** (edad/vida)` es cómo empiezan a discrepar.
    """
    if signal.created_at is None:
        return 1.0
    reference = now or datetime.now(tz=timezone.utc)
    age_days = (reference - as_utc(signal.created_at)).total_seconds() / 86400.0
    if age_days <= 0:
        return 1.0
    days = half_life if half_life is not None else half_life_days(signal.source_type)
    return float(0.5 ** (age_days / days))


def signal_weight(
    signal: BehaviorSignal, *, now: datetime | None = None, half_life: float | None = None
) -> float:
    """El valor de *signal* descontado por su edad. Conserva el signo."""
    return float(signal.value) * decay_factor(signal, now=now, half_life=half_life)


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


def food_vocabulary(db: Session) -> dict[str, str]:
    """Los nombres de alimento contra los que se busca un sujeto dentro de una frase.

    La contraparte de `attribute_index` para el matcher de texto: la consulta vive acá y no
    en el servicio que la necesita, por la misma razón —quien lee señales pide el
    vocabulario a este módulo, así que hay un solo lugar donde mirar qué se compara contra
    qué—. Es el catálogo completo, incluidos los alimentos sin categoría; el por qué está en
    `FoodRepository.known_names`, y ahí también está por qué es un mapa nombre → canónico y
    no una lista de nombres.
    """
    return FoodRepository(db).known_names()


def subjects_in_text(text: str, *, foods: Mapping[str, str]) -> list[tuple[str, str]]:
    """Los sujetos que *text* menciona por su nombre, en la forma de `subject_key`.

    Sirve para leer el motivo de texto libre de un rechazo: *"no me gusta el brócoli"* tiene
    que enseñar sobre el brócoli y no sobre el título de la sugerencia, que es lo único que
    el feedback sabía mirar hasta acá.

    Dos vocabularios, los dos **cerrados**: los alimentos que el catálogo conoce
    (`food_vocabulary`) y las actividades que el parser de reglas conoce
    (`rules.find_known_activities`). Cerrados es la decisión de diseño entera. La
    alternativa —quedarse con las palabras de la frase, que es lo que hace
    `rules._parse_preference`— inventa sujetos: de *"no es para nosotros"* saldría un
    alimento llamado "para nosotros", y una vez grabado nadie lo borra y nunca matchea nada.
    Si un nombre no está en ningún catálogo, la frase simplemente no enseña, que es el error
    barato: la sugerencia igual quedó rechazada por su propio sujeto.

    El match es de frase entera y de la más larga primero, y cada coincidencia se **consume**
    del texto: sin eso, "queso crema" grabaría también un rechazo de "queso", que es un
    alimento distinto y probablemente sí querido.

    **Se busca por cualquiera de sus nombres y se graba por el canónico.** El sujeto que
    declaran los candidatos es `food.canonical_name`, así que grabar el alias que matcheó
    —"palta" cuando el catálogo dice `avocado`— escribía una señal que ningún candidato iba a
    encontrar nunca: aprendida y no leída, y justamente en el caso normal de una casa que
    escribe en castellano sobre un catálogo en inglés. De ahí que *foods* sea el mapa
    nombre → canónico de `food_vocabulary` y no una lista.

    La dirección de esta dependencia —el recomendador usa el NLP— es la que corresponde y no
    hace ciclo: `app/nlp/` no importa nada de `app/recommendations/`.
    """
    haystack = f" {normalize_subject(text)} "
    if not haystack.strip():
        return []

    found: list[tuple[str, str]] = []
    canonical_by_name: dict[str, str] = {}
    for name, canonical in foods.items():
        normalized = normalize_subject(name)
        if normalized:
            canonical_by_name[normalized] = canonical
    for name in sorted(canonical_by_name, key=len, reverse=True):
        needle = f" {name} "
        if needle in haystack:
            haystack = haystack.replace(needle, "  ")
            key = subject_key("food", canonical_by_name[name])
            #: Dos alias del mismo alimento en la misma frase —"ni palta ni aguacate"— son
            #: un solo sujeto, no dos señales del mismo peso.
            if key not in found:
                found.append(key)

    #: Las actividades se buscan sobre el texto original: el matcher de reglas trae su propio
    #: `\b` y sus nombres son de una palabra o dos sin tildes, así que normalizar antes no
    #: aporta nada y perdería los guiones de "pull-ups".
    for activity in rules.find_known_activities(text):
        key = subject_key("exercise", activity)
        if key not in found:
            found.append(key)
    return found


def _slot(value: Any) -> str | None:
    """*value* como franja horaria conocida, o `None`. Ver `MEAL_SLOTS`."""
    slot = str(value or "").strip().lower()
    return slot if slot in MEAL_SLOTS else None


def signal_slot(signal: BehaviorSignal) -> str | None:
    """La franja horaria en la que ocurrió *signal*, o `None` si no la declaró.

    Sale de `context_json["meal_type"]`, que `MealService.log_meal` viene escribiendo desde
    la 4.4.1 en cada `repeated_meal_choice`. Es el único escritor de una hora, así que el
    eje temporal aprende de lo implícito —lo que se comió y cuándo— y no del feedback
    explícito: `Suggestion` no tiene columna de contexto, y agregarla es una migración que
    la v3 no tiene disponible. No es un lector sin escritor: el escritor ya existe.
    """
    context = signal.context_json
    if not isinstance(context, dict):
        return None
    return _slot(context.get("meal_type"))


def candidate_slot(candidate: dict[str, Any]) -> str | None:
    """La franja para la que se está ofreciendo un candidato, o `None`.

    La declara el generador, que ya la calculó (`meal_generator._current_meal_type`) y
    además admite que se la pasen por parámetro. Re-derivarla acá desde el reloj sería
    duplicar la lógica de ventanas, acoplar el scorer a la hora del sistema, y discrepar
    con el generador justo cuando alguien usa el override. Los candidatos que no son de
    comida no declaran franja, y por eso este eje se limita solo.
    """
    return _slot(candidate.get("meal_type"))


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
    ocupa lugar, entra en la ventana de lectura (`SIGNAL_HORIZON_DAYS`) y no puede matchear
    nada.

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


@dataclass(frozen=True)
class SubjectAffinity:
    """Lo que la app aprendió sobre un sujeto: para qué lado, y con cuánto respaldo.

    Son dos preguntas distintas y hasta la 4.4.3 vivían sumadas en un solo número. "Comí
    pollo una vez" y "comí pollo veinte veces" apuntan para el mismo lado con confianza
    muy distinta, y "comí pollo diez veces y una vez rechacé una sugerencia de pollo"
    apunta para el mismo lado que "comí pollo una vez", pero sabiendo mucho más.
    """

    #: La suma firmada de los pesos descontados: para qué lado, con cuánta fuerza bruta.
    net: float
    #: Cuántas observaciones descontadas lo sostienen, en valor absoluto. Una señal de
    #: `value=1.0` de hoy aporta 1; la misma de hace una semivida aporta 0.5; un
    #: `repeated_purchase` (0.5) aporta medio. Es "cuánto vio la app", no "para qué lado".
    evidence: float
    #: Cuánta evidencia hace falta para estar a mitad de camino de la certeza. Es un campo
    #: y no una constante del módulo porque el nivel atributo (4.4.4) pide más que el
    #: puntual: la misma aritmética, con la vara más alta.
    half_saturation: float = _EVIDENCE_HALF_SATURATION

    @property
    def direction(self) -> float:
        """El promedio de las señales, en `[-1, 1]`: la opinión, sin la certeza."""
        if self.evidence <= 0:
            return 0.0
        return max(-1.0, min(1.0, self.net / self.evidence))

    @property
    def confidence(self) -> float:
        """Cuánto confiar en esa opinión: satura hacia 1 y nunca llega.

        `n / (n + k)`: una observación 0.33, dos 0.5, seis 0.75, veinte 0.91. Satura a
        propósito —la diferencia entre seis y veinte observaciones no debería mover el
        score— y no llega nunca a 1 porque la app no termina de estar segura de nada.
        """
        if self.evidence <= 0:
            return 0.0
        return self.evidence / (self.evidence + self.half_saturation)

    @property
    def strength(self) -> float:
        """Lo que el scorer usa: la opinión ponderada por la certeza, en `[-1, 1]`."""
        return self.direction * self.confidence

    @property
    def direction_band(self) -> str:
        """La dirección en una de tres palabras: `toward`, `away` o `mixed`.

        El corte existe porque el punto medio no es "neutro" sino "las dos cosas": comer
        algo seis veces y rechazar una sugerencia de eso mismo da una dirección cerca de
        cero, y decir "te da igual" sobre eso sería falso — lo honesto es "depende".

        Vive acá y no en la plantilla, que es donde estaba: los cortes se derivan de esta
        aritmética, así que puestos en un `{% if %}` de Jinja quedaban a un `git grep` de
        distancia de la constante que los justifica, y cambiar `_EVIDENCE_HALF_SATURATION`
        hacía que el rótulo empezara a mentir sin que nada fallara. La plantilla mapea la
        palabra a un msgid, que es lo suyo.
        """
        if self.direction >= _DIRECTION_BAND_EDGE:
            return "toward"
        if self.direction <= -_DIRECTION_BAND_EDGE:
            return "away"
        return "mixed"

    @property
    def confidence_band(self) -> str:
        """Cuánto respaldo hay, en una de tres palabras: `plenty`, `some` o `new`.

        Los cortes están puestos en evidencia y no en confianza porque es la misma cosa
        dicha donde se entiende: `n / (n + k) >= 0.5` es exactamente `n >= k`, y `>= 0.75`
        es `n >= 3k`. O sea que las tres palabras son "menos de dos observaciones", "dos o
        más" y "seis o más" con la vara de `half_saturation` —la puntual o la de atributo,
        que es el triple—, y no dos números sueltos que hay que volver a derivar.
        """
        if self.evidence >= 3 * self.half_saturation:
            return "plenty"
        if self.evidence >= self.half_saturation:
            return "some"
        return "new"

    def without(self, part: SubjectAffinity) -> SubjectAffinity:
        """Lo mismo, descontando lo que *part* aportó. Conserva la vara de evidencia.

        Sirve para una sola cosa y es la que hace que el nivel atributo agregue
        información en vez de repetirla: lo que la app sabe de las verduras **sin contar
        la espinaca** es lo único que la espinaca no sabía ya de sí misma.
        """
        return SubjectAffinity(
            net=self.net - part.net,
            #: Un piso en cero porque restar dos flotantes que deberían cancelarse deja
            #: residuos del orden de 1e-16, y un `evidence` negativo daría una confianza
            #: negativa: una opinión al revés por un error de redondeo.
            evidence=max(0.0, self.evidence - part.evidence),
            half_saturation=self.half_saturation,
        )


def subject_affinities(
    signals: list[BehaviorSignal], *, now: datetime | None = None
) -> dict[tuple[str, str], SubjectAffinity]:
    """Qué aprendió la app de cada sujeto, según *signals*.

    Se agrega en vez de clasificar cada señal por separado porque un mismo sujeto puede
    tener las dos cosas —comió pollo seis veces y rechazó una sugerencia de pollo— y con
    dos listas separadas el candidato recibía el boost **y** la penalización, que es un
    resultado que no significa nada.

    Cada señal entra con su peso descontado por la edad (`signal_weight`), así que lo
    reciente manda y lo viejo se apaga solo. Antes cada fila valía su `value` crudo para
    siempre, y el resultado era un promedio de toda la historia: seis meses de pollo no
    los podían mover dos semanas de otra cosa.

    Y se cuenta **cuántas** además de **cuánto**: hasta la 4.4.3 esto devolvía solo la
    suma, el scorer la recortaba con `min(suma, 1.0)` y un único tap movía el score igual
    que diez observaciones consistentes. Ver `SubjectAffinity`.

    Quién decide el signo es `value`, no el tipo: `POSITIVE_SIGNAL_TYPES` y
    `NEGATIVE_SIGNAL_TYPES` están para que el vocabulario sea revisable de un lado solo, y
    para dejar afuera del cómputo lo que no es una opinión —`ignored_suggestion`, que graba
    tanto descartar como posponer—.
    """
    reference = now or datetime.now(tz=timezone.utc)
    nets: dict[tuple[str, str], float] = {}
    evidences: dict[tuple[str, str], float] = {}
    for signal in signals:
        if signal.signal_type not in POSITIVE_SIGNAL_TYPES | NEGATIVE_SIGNAL_TYPES:
            continue
        key = subject_key(signal.entity_type, signal.entity_name)
        if not key[1]:
            continue
        weight = signal_weight(signal, now=reference)
        nets[key] = nets.get(key, 0.0) + weight
        evidences[key] = evidences.get(key, 0.0) + abs(weight)
    return {key: SubjectAffinity(net=net, evidence=evidences[key]) for key, net in nets.items()}


@dataclass(frozen=True)
class LearnedSubject:
    """Lo aprendido sobre un sujeto, contado para que una **persona** lo pueda leer.

    `SubjectAffinity` es la forma que necesita el scorer: dos flotantes descontados por la
    edad, de los que salen una dirección y una confianza. Sirven para ordenar candidatos y
    no sirven para mostrarle a nadie: "evidencia 4.31" no es una frase.

    Esto agrega lo que hace falta para que el panel de la 4.4.8 no sea una caja negra con
    otro color —**cuántas** veces se vio, **cuántas** de esas las dijo la persona con
    palabras, **cuántas** son sugerencias que pasaron de largo, y **cuándo** fue la última
    vez que algo se registró—, sin recalcular la opinión por otro camino: la dirección y la
    confianza son las mismas de `subject_affinities`, así que el panel no puede discrepar de
    lo que el motor hace. Si discrepara, el panel sería una segunda implementación del
    aprendizaje, que es peor que no tener panel.
    """

    subject_type: str
    subject_name: str
    #: El nombre como se escribió, para mostrar: "brócoli" y no "brocoli". `subject_name`
    #: es la clave —sin tildes ni puntuación, que es lo que permite que las dos mitades del
    #: nombre matcheen— y sirve para comparar y para volver a encontrar las filas, pero
    #: impreso en una app en español se lee como un error de la app. Sale de la señal más
    #: reciente: si dos filas lo escribieron distinto, gana la última, que es la que la
    #: persona vio cuando lo registró.
    display_name: str
    #: La misma opinión que lee el scorer, sin recalcular: dirección, confianza, fuerza.
    affinity: SubjectAffinity
    #: Cuántas filas la sostienen, **sin descontar por edad**. La evidencia descontada es
    #: la que decide cuánto pesa; esta es la que se puede decir en voz alta ("6 registros").
    observations: int
    #: Cuántas de esas fueron **palabras** y no conducta: `signal_type="explicit_preference"`,
    #: que es lo que escriben una preferencia declarada y los nombres minados del motivo que
    #: la persona escribió al responder una tarjeta.
    #:
    #: Contaba `source_type == "explicit"`, y eso era falso: `respond_to_suggestion` marca
    #: así el accepted/rejected de un **tap** en una tarjeta, sin una palabra de por medio.
    #: Un solo descarte imprimía "1 de lo que dijiste" al lado de un alimento sobre el que
    #: la persona nunca escribió nada — y la distinción que el panel promete es justo esa,
    #: porque separa lo que se corrige escribiendo de lo que se corrige con conducta.
    said_observations: int
    #: Cuántas sugerencias de este sujeto pasaron su semana sin que nada lo registrara
    #: (`ABSENCE_SIGNAL_TYPE`). Va en un contador aparte y **no** entra en `observations`
    #: porque no es un registro: nadie hizo nada, y contarlo como "6 registros" al lado de
    #: cinco comidas reales convertiría el número que el panel promete —cuántas veces esto
    #: pasó— en la suma de dos cosas distintas. Tampoco mueve `last_seen`: la última vez que
    #: se vio el sujeto es la última vez que apareció, no la última vez que faltó.
    absence_observations: int
    #: Si esto además está declarado en `recommendation_preferences`, o sea si es una fila
    #: de "lo que nos dijiste". Se detecta por el `explicit_preference` que **no** viene de
    #: una sugerencia: `save_preference` escribe la preferencia y la señal juntas, y es el
    #: único camino que hace las dos cosas.
    #:
    #: El panel lo necesita para no ofrecer un botón que no puede cumplir: olvidar borra
    #: señales, y la preferencia declarada seguiría ahí —filtrando, no ordenando, si es un
    #: "no me gusta" (`filters.py`)— sin ninguna ruta que la borre. Un "listo, lo olvidé"
    #: sobre algo que sigue filtrando es la peor de las dos formas de fallar.
    declared: bool
    #: Cuándo fue la última vez que algo lo confirmó. `None` solo si ninguna de las filas
    #: tiene `created_at` —una señal recién grabada y todavía sin volcar—, que es un borde
    #: de tests y no de producción, pero un `None` es más honesto que la fecha de hoy.
    last_seen: datetime | None
    #: Hace cuántos días fue eso, ya calculado. Es un campo y no una propiedad que lea el
    #: reloj porque el descuento por edad de `affinity` se calculó contra el `now` que
    #: recibió `learned_subjects`: una propiedad con su propio `datetime.now()` diría "hace
    #: 3 días" al lado de una confianza calculada para otro instante, y en los tests —que
    #: pasan un `now` fijo— las dos cifras hablarían de fechas distintas.
    days_since: int | None

    @property
    def logged_observations(self) -> int:
        """Las que salieron de la conducta. Se deriva para que no puedan discrepar."""
        return self.observations - self.said_observations


def _days_since(moment: datetime | None, reference: datetime) -> int | None:
    """Cuántos días enteros pasaron, con piso en cero.

    El piso existe porque una señal grabada en el mismo request que la lectura puede tener
    un `created_at` unos microsegundos posterior al `reference` —y un "hace -1 días" es
    peor que redondear a "hoy"—.
    """
    if moment is None:
        return None
    return max(0, (reference - as_utc(moment)).days)


def learned_subjects(
    signals: list[BehaviorSignal], *, now: datetime | None = None
) -> list[LearnedSubject]:
    """Todo lo que la app aprendió, ordenado por cuánto está moviendo las sugerencias.

    Mismo filtro que `subject_affinities` —lo que no es una opinión no aparece, así que
    posponer una tarjeta no se muestra como si fuera un gusto— y mismo horizonte que lee el
    motor, que lo pone quien llama. Las dos cosas juntas son lo que hace que el panel sea
    *el* aprendizaje y no un informe parecido.

    El orden es por `|strength|` y no por evidencia ni por fecha: lo primero que la persona
    ve es lo que más le está cambiando las sugerencias, que es lo que querría corregir si
    estuviera mal. Con desempate por evidencia y después por nombre, para que dos corridas
    con los mismos datos den la misma lista —una tabla que se reordena sola entre dos
    visitas parece que estuviera aprendiendo cuando no pasó nada—.

    Y **solo** los tipos de `SUBJECT_TYPES`, que es lo que el scorer sabe leer. Hasta la 4.4
    una preferencia declarada de tipo "ingredient" escribía señales con ese `entity_type`
    (`suggestion_service.py`), y el scorer —que pregunta por `"food"`— nunca las miraba: son
    filas inertes. Mostrarlas en una lista cuyo orden dice "esto es lo que más te está
    cambiando las sugerencias", con un grupo titulado "Ingredient" por el rótulo de
    emergencia de la plantilla, es exactamente la clase de cosa que el panel vino a
    arreglar. No aparecen, y el `record_signal` de la 4.4 ya no las escribe.
    """
    reference = now or datetime.now(tz=timezone.utc)
    affinities = subject_affinities(signals, now=reference)
    observations: dict[tuple[str, str], int] = {}
    said: dict[tuple[str, str], int] = {}
    absences: dict[tuple[str, str], int] = {}
    declared: set[tuple[str, str]] = set()
    display: dict[tuple[str, str], str] = {}
    last_seen: dict[tuple[str, str], datetime] = {}
    for signal in signals:
        if signal.signal_type not in POSITIVE_SIGNAL_TYPES | NEGATIVE_SIGNAL_TYPES:
            continue
        key = subject_key(signal.entity_type, signal.entity_name)
        if key[0] not in SUBJECT_TYPES or not key[1]:
            continue
        if signal.signal_type == ABSENCE_SIGNAL_TYPE:
            #: Una ausencia mueve la opinión —`subject_affinities` la sumó— pero no es un
            #: registro ni una fecha en que el sujeto se vio, así que sale del contador
            #: general y del reloj. El nombre para mostrar sí lo puede aportar: si lo único
            #: que hay de un sujeto son ausencias, sin esto el panel imprimiría la clave
            #: normalizada.
            absences[key] = absences.get(key, 0) + 1
            display.setdefault(key, signal.entity_name)
            continue
        observations[key] = observations.get(key, 0) + 1
        if signal.signal_type == "explicit_preference":
            said[key] = said.get(key, 0) + 1
            #: Sin sugerencia de origen es `save_preference`, que escribió también la fila
            #: de `recommendation_preferences`; con sugerencia son los nombres minados del
            #: motivo escrito, que son palabras igual pero no están declaradas en ninguna
            #: parte —y por eso sí se pueden olvidar desde acá—.
            if signal.source_entity_type is None:
                declared.add(key)
        if signal.created_at is not None:
            seen = as_utc(signal.created_at)
            if key not in last_seen or seen > last_seen[key]:
                last_seen[key] = seen
                display[key] = signal.entity_name
        display.setdefault(key, signal.entity_name)

    rows = [
        LearnedSubject(
            subject_type=subject_type,
            subject_name=subject_name,
            display_name=display.get((subject_type, subject_name), subject_name),
            affinity=affinity,
            observations=observations.get((subject_type, subject_name), 0),
            said_observations=said.get((subject_type, subject_name), 0),
            absence_observations=absences.get((subject_type, subject_name), 0),
            declared=(subject_type, subject_name) in declared,
            last_seen=last_seen.get((subject_type, subject_name)),
            days_since=_days_since(last_seen.get((subject_type, subject_name)), reference),
        )
        for (subject_type, subject_name), affinity in affinities.items()
        if subject_type in SUBJECT_TYPES
    ]
    rows.sort(
        key=lambda row: (
            -abs(row.affinity.strength),
            -row.affinity.evidence,
            row.subject_type,
            row.subject_name,
        )
    )
    return rows


def attribute_index(db: Session) -> dict[tuple[str, str], tuple[str, str]]:
    """El atributo al que generaliza cada sujeto puntual que el catálogo sepa clasificar.

    Hoy solo alimentos: `("food", "espinaca") → ("food_category", "vegetable")`, y los
    alias del catálogo entran con la misma categoría que su nombre canónico, porque el
    texto libre de una captura escribe el alias y la señal quedó guardada con ese nombre.

    Se arma una vez por corrida del motor y se pasa al scorer: es una consulta sobre una
    tabla de decenas de filas, y el scorer no toca la base —lo que le llega es un
    diccionario, así que sigue siendo una función de sus argumentos y se puede testear sin
    sesión—.

    Los ejercicios **no** están todavía, y no es un olvido: los candidatos de actividad
    salen de una lista de ocho actividades escrita a mano en `activity_generator`, cuyos
    nombres en su mayoría no existen en el catálogo de `ExerciseType` ("biking" contra
    "Cycling", "gym" y "swimming" que no están), así que el atributo resolvería para unos y
    para otros no, en silencio. Reemplazar esa lista por el catálogo es la 4.5, y ahí los
    ejercicios entran acá con el mismo shape —una entrada más en este índice, ninguna otra
    cosa cambia—. Cuál de sus dos atributos discrimina depende de ese mismo reemplazo: para
    una actividad es la intensidad, para un ejercicio de gimnasio es el grupo muscular.
    """
    index: dict[tuple[str, str], tuple[str, str]] = {}
    for canonical_name, category, aliases in FoodRepository(db).name_categories():
        attribute = subject_key("food_category", category)
        if not attribute[1]:
            continue
        for name in (canonical_name, *aliases):
            point = subject_key("food", name)
            if point[1]:
                index[point] = attribute
    return index


def attribute_affinities(
    signals: list[BehaviorSignal],
    attributes: Mapping[tuple[str, str], tuple[str, str]],
    *,
    now: datetime | None = None,
) -> dict[tuple[str, str], SubjectAffinity]:
    """Lo mismo que `subject_affinities`, pero agrupando cada sujeto en su atributo.

    Es lo que permite acertar con algo que la persona **nunca vio**: sin esto, un alimento
    sin señales propias no recibía ni boost ni penalización, aunque la app supiera de sobra
    qué opina de su categoría. Rechazar brócoli, coliflor y kale no enseñaba nada sobre la
    espinaca.

    Una señal cuyo sujeto el catálogo no clasifica no entra —de un alimento de texto libre
    no sabemos la categoría, y adivinarla es exactamente el match difuso que la 4.4 vino a
    sacar—. Y la vara de evidencia es la del atributo: más alta que la puntual.
    """
    reference = now or datetime.now(tz=timezone.utc)
    nets: dict[tuple[str, str], float] = {}
    evidences: dict[tuple[str, str], float] = {}
    for signal in signals:
        if signal.signal_type not in POSITIVE_SIGNAL_TYPES | NEGATIVE_SIGNAL_TYPES:
            continue
        attribute = attributes.get(subject_key(signal.entity_type, signal.entity_name))
        if attribute is None:
            continue
        weight = signal_weight(signal, now=reference)
        nets[attribute] = nets.get(attribute, 0.0) + weight
        evidences[attribute] = evidences.get(attribute, 0.0) + abs(weight)
    return {
        key: SubjectAffinity(
            net=net,
            evidence=evidences[key],
            half_saturation=_ATTRIBUTE_EVIDENCE_HALF_SATURATION,
        )
        for key, net in nets.items()
    }


def generalized_affinity(
    subject: tuple[str, str],
    *,
    attributes: Mapping[tuple[str, str], tuple[str, str]],
    points: Mapping[tuple[str, str], SubjectAffinity],
    groups: Mapping[tuple[str, str], SubjectAffinity],
) -> SubjectAffinity | None:
    """Lo que la app sabe del atributo de *subject* **sin contar a *subject* mismo**.

    Descontarse es lo que hace que los dos niveles no digan lo mismo dos veces. Un alimento
    que se come todos los días es el que más aporta a su categoría, así que sin la resta
    cobraría el ajuste puntual y otra vez, en chico, por su propia evidencia — y un
    favorito terminaba con un ajuste más grande que el knob, por partida doble, sin que
    hubiera aparecido ni un dato nuevo.

    Devuelve `None` cuando el catálogo no sabe clasificar el sujeto o cuando la app no vio
    nada de esa categoría: no hay generalización que hacer.
    """
    attribute = attributes.get(subject)
    if attribute is None:
        return None
    group = groups.get(attribute)
    if group is None:
        return None
    own = points.get(subject)
    return group if own is None else group.without(own)


def slot_affinities(
    signals: list[BehaviorSignal], *, now: datetime | None = None
) -> dict[tuple[str, str], dict[str, SubjectAffinity]]:
    """Qué aprendió la app de cada sujeto **en cada franja horaria**: sujeto → franja → lo
    aprendido.

    La misma aritmética de `subject_affinities`, partida por hora. Las señales sin franja
    quedan afuera del todo: no dicen nada sobre el reloj, y meterlas en un grupo "sin hora"
    sería inventar una franja que después argumentaría contra las reales.
    """
    reference = now or datetime.now(tz=timezone.utc)
    nets: dict[tuple[tuple[str, str], str], float] = {}
    evidences: dict[tuple[tuple[str, str], str], float] = {}
    for signal in signals:
        if signal.signal_type not in POSITIVE_SIGNAL_TYPES | NEGATIVE_SIGNAL_TYPES:
            continue
        slot = signal_slot(signal)
        if slot is None:
            continue
        key = subject_key(signal.entity_type, signal.entity_name)
        if not key[1]:
            continue
        weight = signal_weight(signal, now=reference)
        nets[(key, slot)] = nets.get((key, slot), 0.0) + weight
        evidences[(key, slot)] = evidences.get((key, slot), 0.0) + abs(weight)

    by_subject: dict[tuple[str, str], dict[str, SubjectAffinity]] = {}
    for (subject, slot), net in nets.items():
        by_subject.setdefault(subject, {})[slot] = SubjectAffinity(
            net=net, evidence=evidences[(subject, slot)]
        )
    return by_subject


def slot_contrast(
    subject: tuple[str, str],
    slot: str,
    *,
    slots: Mapping[tuple[str, str], Mapping[str, SubjectAffinity]],
) -> float:
    """Cuánto mejor —o peor— le cae *subject* en *slot* que en el resto de las horas.

    Es una **diferencia**, no un promedio, y ahí está todo el punto: el nivel puntual ya
    cobra que el café guste, así que si esto midiera otra vez cuánto gusta el café en el
    desayuno estaría cobrando dos veces el mismo dato. Lo que agrega es la comparación
    contra las demás franjas — veinte cafés al desayuno y ninguno a la cena dan `+0.91` al
    desayuno y `−0.91` a la cena, mientras que diez almuerzos y diez cenas del mismo plato
    dan `0` en las dos: un plato indiferente a la hora no debería moverse por la hora.

    La formulación alternativa —dirección en la franja menos dirección global— no servía:
    con los datos que la app tiene hoy toda señal de comida es positiva, así que las dos
    direcciones valen `+1` y la resta da `0` siempre. La 4.4.10 agregó una señal negativa
    inferida (`ABSENCE_SIGNAL_TYPE`), pero no la que haría falta acá: dice que una
    sugerencia no se usó, no que la cena no tuvo café, y sin franja no entra a este eje.
    Comparar franja contra franja no la necesita.

    El recorte a `[-1, 1]` no es decorativo: la resta de dos fuerzas vive en `[-2, 2]`, y
    sin el tope este eje podría mover el score el doble de su perilla, que es justo la
    invariante que sostiene que una perilla acote un eje.
    """
    by_slot = slots.get(subject)
    if not by_slot:
        return 0.0
    inside = by_slot.get(slot)
    net = 0.0
    evidence = 0.0
    for other, affinity in by_slot.items():
        if other == slot:
            continue
        net += affinity.net
        evidence += affinity.evidence
    outside = SubjectAffinity(net=net, evidence=evidence)
    in_strength = inside.strength if inside is not None else 0.0
    return max(-1.0, min(1.0, in_strength - outside.strength))


def satiety_pressure(
    signals: list[BehaviorSignal], *, now: datetime | None = None
) -> dict[tuple[str, str], float]:
    """Cuánto de cada sujeto se consumió hace muy poco, en `[0, 1]`.

    No es una opinión y por eso no devuelve un `SubjectAffinity`: no tiene dirección, no
    hay un "para qué lado". Es una sola pregunta —*cuánto ya hubo de esto*— y siempre
    resta. Un favorito sigue siendo un favorito; lo que deja de ser es una buena idea para
    hoy.

    Las mismas filas que `subject_affinities`, filtradas a las de **acto**
    (`CONSUMPTION_SIGNAL_TYPES`) y descontadas con `_SATIETY_HALF_LIFE_DAYS` en lugar de la
    semivida de su `source_type`. Que sean las mismas filas leídas dos veces —y no una
    segunda escritura por comida— es la misma decisión que en el nivel atributo: un lector
    nuevo no se puede olvidar de nada, y funciona retroactivamente sobre los meses de
    señales que la app ya tiene.

    Se suma en valor absoluto porque acá interesa el volumen y no el signo, y se satura con
    la misma curva `n / (n + k)` de `SubjectAffinity.confidence`: la diferencia entre comer
    algo tres veces y diez veces en dos días no debería seguir moviendo el score, del mismo
    modo que no lo mueve la diferencia entre seis y veinte observaciones.

    Una señal sin `created_at` —recién grabada y todavía no volcada— cuenta entera, igual
    que en el resto del módulo: un acto que está pasando ahora es exactamente el caso de
    saciedad máxima.
    """
    reference = now or datetime.now(tz=timezone.utc)
    recent: dict[tuple[str, str], float] = {}
    for signal in signals:
        if signal.signal_type not in CONSUMPTION_SIGNAL_TYPES:
            continue
        key = subject_key(signal.entity_type, signal.entity_name)
        if not key[1]:
            continue
        amount = abs(signal_weight(signal, now=reference, half_life=_SATIETY_HALF_LIFE_DAYS))
        recent[key] = recent.get(key, 0.0) + amount
    return {
        key: amount / (amount + _SATIETY_HALF_SATURATION)
        for key, amount in recent.items()
        if amount > 0
    }


def rejected_subjects(
    signals: list[BehaviorSignal], *, now: datetime | None = None
) -> set[tuple[str, str]]:
    """Los sujetos con suficiente negativo vivo encima: lo que no se vuelve a ofrecer.

    Más estricto que un neto negativo a propósito. El neto lo puede poner negativo un
    descarte —"lo vi y lo cerré"—, y eso baja el score; sacar un candidato de la lista
    antes de puntuarlo pide un tipo de señal que signifique "no".

    Y el "no" caduca. Cada señal cuenta mientras conserve al menos la mitad de su peso, o
    sea durante una semivida: pasado eso el sujeto vuelve a la lista y el rechazo sigue
    contando, pero solo en el score. Sin esto, ampliar la ventana de lectura para que el
    decaimiento tenga de qué decaer habría convertido un "no" de hace once meses en un
    veto permanente — el problema que la 4.4.2 vino a resolver, al revés.

    Sobre esa frescura hay un segundo umbral: **cuánto** negativo hay. Existe porque no
    todos los negativos son un "no" —la ausencia pesa `ABSENCE_VALUE`, un quinto— y una
    sugerencia que la persona quizá nunca vio no puede vetar sola. Para un rechazo
    deliberado los dos umbrales son la misma condición y el comportamiento anterior no
    cambia; para una ausencia, con los valores de hoy, no alcanza nunca — ver la cuenta en
    `_FILTER_EVIDENCE_FLOOR`. Lo que sale de acá es entonces "los sujetos que alguien
    rechazó a propósito y hace poco", igual que antes de que la ausencia existiera.
    """
    reference = now or datetime.now(tz=timezone.utc)
    negative: dict[tuple[str, str], float] = {}
    for signal in signals:
        if signal.signal_type not in NEGATIVE_SIGNAL_TYPES or float(signal.value) >= 0:
            continue
        if decay_factor(signal, now=reference) < _FILTER_DECAY_FLOOR:
            continue
        key = subject_key(signal.entity_type, signal.entity_name)
        negative[key] = negative.get(key, 0.0) + abs(signal_weight(signal, now=reference))
    return {key for key, weight in negative.items() if weight >= _FILTER_EVIDENCE_FLOOR}
