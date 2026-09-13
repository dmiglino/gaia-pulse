"""La acción primaria de cada cosa que la app dice.

Hasta acá una notificación era un callejón sin salida: `Notification` **ya** tenía
`related_entity_type`/`related_entity_id` — desde la 4.2 los jobs los llenan —, pero la
plantilla dibujaba título, cuerpo y dos glifos para marcar como leído o descartar. O
sea que las dos únicas cosas que se podían hacer con "no queda leche" eran esconderla y
esconderla. Con las sugerencias pasaba lo mismo del otro lado: las tres respuestas eran
las tres para el motor (aceptar, posponer, rechazar) y ninguna para la persona.

Acá vive el mapa de "esto, ¿a dónde lleva?". Es un módulo de la capa web porque lo que
produce es una URL de esta UI, y son funciones puras — sin base, sin request — para que
el destino sea una sola verdad: lo lee la plantilla para dibujar el botón y lo leen
`POST /notifications/{id}/act` y `POST /suggestions/{id}/act` para saber a dónde mandar
a la persona después de anotar que actuó. Si el destino se calculara dos veces, el botón
y el redirect podrían no coincidir.

Devolver `None` es parte del contrato: hay cosas que no tienen nada que hacer —un
consejo derivado del análisis de sangre no se "registra"—, y para esas la tarjeta se
queda como estaba en vez de ofrecer un botón que no sabe a dónde va.
"""

from dataclasses import dataclass

from app.i18n import _


@dataclass(frozen=True)
class PrimaryAction:
    """A dónde lleva, cómo se llama el botón y con qué ícono.

    `label` viene ya traducido: estas funciones se llaman en tiempo de render (como
    global de Jinja) o dentro del handler, nunca en tiempo de import.
    """

    label: str
    href: str
    icon: str


def _capture(prefill: str) -> str:
    """La pantalla de captura con el comienzo de frase ya puesto.

    Las claves que se mandan desde acá — `meal`, `workout`, `weight` — tienen que estar
    en el `prefillMap` de `capture/index.html`, que es el que traduce la clave a texto;
    una clave que no esté ahí abre el textarea vacío y sin error visible. Eso lo verifica
    `tests/test_actions.py::test_capture_knows_every_prefill_key_we_send`.

    Con la barra final: sin ella FastAPI cobra un 307 de `redirect_slashes` antes de
    llegar a la ruta.
    """
    return f"/capture/?prefill={prefill}"


def notification_action(
    category: str, entity_type: str | None = None, entity_id: int | None = None
) -> PrimaryAction | None:
    """Qué hacer con esta notificación.

    El sujeto que la 4.2 empezó a grabar es lo que hace que el destino sea exacto y no
    una categoría: el aviso de la leche aterriza en **ese** ítem de la despensa, no en
    "la despensa".
    """
    if category == "low_stock":
        #: `?low=1` y no la despensa entera: el ancla apunta a una tarjeta, y la
        #: tarjeta tiene que estar en la página para que el navegador la encuentre.
        #: Filtrar por faltantes es además la vista en la que la lista de cosas por
        #: reponer se lee de una. Si el ítem ya se repuso el ancla no resuelve y la
        #: página igual abre en la lista de faltantes, que es la degradación correcta.
        #: `int()` sobre el id: sale de una columna `Integer` y no hay ruta que lo deje
        #: escribir a mano, pero de acá va a un `Location:` sin pasar por ninguna
        #: plantilla que lo escape, y el único control es el tipo de la columna.
        anchor = ""
        if entity_type == "pantry_stock" and entity_id:
            anchor = f"#stock-item-{int(entity_id)}"
        return PrimaryAction(_("Update the pantry"), f"/pantry/?low=1{anchor}", "pantry")
    if category == "inactivity":
        return PrimaryAction(_("Log a workout"), _capture("workout"), "bolt")
    if category == "metric_reminder":
        return PrimaryAction(_("Log your weight"), _capture("weight"), "trend-up")
    return None


def suggestion_action(category: str, source_type: str) -> PrimaryAction | None:
    """Qué hacer con esta sugerencia.

    Cuando hay una, **reemplaza** al "Me parece bien" en vez de sumarse: las dos serían
    dos botones afirmativos al lado del otro, y el que además hace algo es este. La
    señal positiva no se pierde — `POST /suggestions/{id}/act` graba `accepted` antes
    de redirigir —, así que aceptar y hacerlo dejan de ser dos gestos.

    *source_type* no es opcional a propósito, y es lo que decide primero. Lo que emite el
    generador de sangre no es `category="habit"`: son 19 filas de `meal` y 5 de
    `activity` —"comé menos carne roja", "evitá los suplementos de hierro"—, así que
    mirar solo la categoría le ponía un "Anotar una comida" a un consejo de **no** comer
    algo, y encima reemplazando al "Me parece bien": la única respuesta afirmativa
    posible abría la captura con "Comimos " escrito. Un consejo de un panel no se
    registra en ninguna pantalla; es la parte de la app que más cuidado pide y la que
    menos tiene que empujar a nada. Con el parámetro obligatorio, un llamador nuevo que
    lo olvide falla en el test, no en la pantalla de alguien.

    Las categorías son las tres que algún generador realmente emite. `Suggestion.category`
    documenta también `variety`, `recovery`, `pantry` y `reminder`, y ninguna se escribe
    nunca: sumarlas acá sería una rama que no se puede alcanzar, y agregarlas el día que
    un generador las emita es una línea.
    """
    if source_type == "blood_analysis":
        return None
    if category == "meal":
        return PrimaryAction(_("Log a meal"), _capture("meal"), "meal")
    if category == "activity":
        return PrimaryAction(_("Log a workout"), _capture("workout"), "bolt")
    if category == "shopping":
        #: `?low=1` por lo mismo que el aviso de stock: las dos sugerencias de `shopping`
        #: que existen **son** la lista de lo que se acabó y lo que está por acabarse
        #: (`pantry_generator`), así que la despensa entera obliga a buscar de nuevo lo
        #: que la tarjeta ya nombró.
        return PrimaryAction(_("Open the pantry"), "/pantry/?low=1", "pantry")
    return None
