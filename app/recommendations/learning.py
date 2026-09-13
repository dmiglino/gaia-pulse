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
NEGATIVE_SIGNAL_TYPES: frozenset[str] = frozenset({"rejected_suggestion"})

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
