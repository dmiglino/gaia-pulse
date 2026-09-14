"""El panel de "lo que GaiaPulse aprendió" y el botón de olvidar (4.4.8).

Hasta acá `behavior_signals` no tenía **ninguna** lectura de usuario: las cuatro rutas de
aprendizaje movían el orden de las sugerencias y lo único que la casa podía hacer con una
deducción equivocada era recibir sugerencias raras. Estos tests cubren las tres cosas que
hacen que el panel sea una corrección y no una decoración:

- que muestre lo que el motor efectivamente lee, con las cifras que lo hacen discutible;
- que olvidar **borre filas** y lo diga con la verdad — incluido el caso de cero borrados,
  que es un doble clic y no un éxito;
- que el nombre con el que la pantalla pide olvidar algo encuentre la fila que lo guarda,
  que es donde esto podía fallar en silencio: `record` guarda "brócoli" con tilde y el
  panel imprime "brocoli" sin tilde, así que un `WHERE entity_name = ?` con lo que la
  pantalla mandó borraba cero filas y contestaba que todo bien.

Y el filtro por `user_id`, que en un hogar de dos no es una formalidad: Diego y Rocío no
tienen los mismos gustos, y olvidar el brócoli de uno no puede tocar el del otro.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.i18n import _, get_translations
from app.models.food import FoodItem
from app.models.signal import BehaviorSignal
from app.models.user import User
from app.models.workout import ExerciseType
from app.recommendations import learning
from app.repositories.suggestion_repo import BehaviorSignalRepository
from app.web.helpers import templates


def _record(
    db: Session,
    user: User,
    entity_name: str,
    *,
    signal_type: str = "repeated_meal_choice",
    entity_type: str = "food",
    value: float = 1.0,
    source_type: str = "implicit",
    source_entity_type: str | None = None,
    days_ago: int = 0,
) -> BehaviorSignal:
    """Una señal de *user*, con la edad que el test necesite.

    `created_at` se escribe a mano porque el default lo pone la base y todas las filas de
    un test quedarían en el mismo instante: la recencia y el descuento por edad son dos de
    las cifras que el panel muestra, así que sin poder envejecer una fila no se pueden
    probar.

    *source_entity_type* separa las dos clases de `explicit_preference`, que el panel trata
    distinto: `None` es una preferencia declarada (`save_preference` escribió también la
    fila de `recommendation_preferences`) y `"suggestion"` es un nombre minado del motivo
    que la persona escribió al responder una tarjeta.
    """
    signal = BehaviorSignalRepository(db).record(
        user.id,
        signal_type=signal_type,
        entity_type=entity_type,
        entity_name=entity_name,
        value=value,
        source_type=source_type,
        source_entity_type=source_entity_type,
    )
    signal.created_at = datetime.now(tz=timezone.utc) - timedelta(days=days_ago)
    db.flush()
    return signal


def _plural(singular: str, plural: str, num: int) -> str:
    """El mismo texto que rinde la plantilla para un `ngettext`.

    Hace falta porque `app.i18n` expone `_` y no un `ngettext` para Python: los plurales
    solo se usan desde las plantillas, donde Jinja está instalado en `newstyle` y sustituye
    `%(num)d` por su cuenta. Construir la cadena acá —en lugar de buscar el número suelto—
    es la diferencia entre verificar la frase y verificar que en algún lado de la página
    aparezca un "4": `space-y-4` lo hace aparecer trescientas veces.
    """
    translations = get_translations(get_settings().default_locale)
    return translations.ungettext(singular, plural, num) % {"num": num}


#: Los dos rótulos del nivel atributo, pedidos a los macros que la página usa. Ver el
#: comentario dentro del test que los compara: no se pueden escribir como msgid suelto.
_dm = templates.env.globals["dm"]


def _muscle_group_label(muscle_group: str) -> str:
    return str(_dm.muscle_group_label(muscle_group)).strip()


def _kind_label(attribute_type: str) -> str:
    return str(_dm.learned_attribute_kind_label(attribute_type)).strip()


def _forget(client: TestClient, subject_type: str, subject_name: str, *, htmx: bool = True):
    return client.post(
        "/profile/learned/forget",
        data={"subject_type": subject_type, "subject_name": subject_name},
        headers={"HX-Request": "true"} if htmx else {},
        follow_redirects=False,
    )


def test_the_panel_shows_what_the_engine_deduced_with_the_numbers_behind_it(
    authenticated_client: TestClient, db: Session, diego: User
) -> None:
    """Las tres cifras que hacen discutible una deducción tienen que estar en la pantalla.

    "Evidencia 4.31" no es una frase: lo que se puede discutir es cuántas veces se vio,
    cuántas de esas las dijo la persona con palabras, y cuándo fue la última. Sin eso el
    panel sería una caja negra de otro color.
    """
    for days_ago in (1, 2, 3):
        _record(db, diego, "pollo", days_ago=days_ago)
    #: Palabras, pero no una preferencia declarada: es un nombre minado del motivo escrito
    #: al responder una tarjeta. La distinción importa para este test porque una preferencia
    #: declarada no lleva botón de olvido (ver el test de más abajo), y acá se verifica la
    #: fila completa, cifras y control incluidos.
    _record(
        db,
        diego,
        "pollo",
        signal_type="explicit_preference",
        source_type="explicit",
        source_entity_type="suggestion",
    )

    r = authenticated_client.get("/profile/")
    assert r.status_code == 200, r.text[:500]

    assert _("What GaiaPulse figured out") in r.text
    assert "pollo" in r.text
    # Cuatro señales positivas y ningún rechazo: la dirección es +1.
    assert _("You go for it") in r.text
    # La evidencia descontada de las cuatro es ~3.8 (semividas de 21 y 90 días), o sea
    # entre `half_saturation` (2) y su triple (6): "unas cuantas veces" y no "muchas". El
    # valor es determinista, así que el test lo fija en vez de aceptar cualquiera de los dos
    # —un `or` entre dos rótulos excluyentes pasa aunque el corte esté mal puesto—.
    assert _("Seen it a few times") in r.text
    assert _("Seen it plenty of times") not in r.text
    assert _plural("%(num)d record", "%(num)d records", 4) in r.text
    # La dicha con palabras se cuenta aparte porque es la que se corrige escribiendo.
    assert _("%(num)d from what you said", num=1) in r.text
    # Y el horizonte, para que "no aparece" no se confunda con "se olvidó".
    assert str(learning.SIGNAL_HORIZON_DAYS) in r.text
    # La fila es corregible: el control de olvido está, con el sujeto en el aria-label.
    assert _("Forget %(subject)s", subject="pollo") in r.text


def test_the_empty_panel_says_nothing_is_learned_instead_of_disappearing(
    authenticated_client: TestClient, db: Session, diego: User
) -> None:
    """Sin señales, la sección sigue estando: es la única forma de que se sepa que existe.

    Un panel que solo aparece cuando ya hay algo aprendido no se puede encontrar antes de
    aprender nada, que es justamente cuando alguien se pregunta si la app lo está mirando.
    """
    r = authenticated_client.get("/profile/")
    assert r.status_code == 200, r.text[:500]
    assert _("What GaiaPulse figured out") in r.text
    assert _("Nothing learned yet") in r.text


def test_the_category_block_counts_only_the_signals_that_are_an_opinion(
    authenticated_client: TestClient, db: Session, diego: User, banana: FoodItem
) -> None:
    """La cifra que hace creíble una generalización tiene que ser cierta: de 1 ítem, 1.

    El respaldo de una categoría es de cuántos sujetos **distintos** salió, y se cuenta
    sobre las mismas señales que movieron su dirección. Posponer una tarjeta
    (`ignored_suggestion`) no dice nada del sujeto y por eso no entra en la afinidad: si
    entrara en el conteo, la pantalla diría "2 ítems" al lado de una conclusión sacada de
    uno. Y la categoría va sin botón de olvido a propósito —no tiene filas propias—, así
    que la pantalla lo explica en vez de dejar un botón que no podría hacer nada.
    """
    #: El melón necesita su fila en el catálogo, y ese es el punto del test: el respaldo de
    #: una categoría se cuenta contra `attribute_index`, que sale de `FoodItem`. Sin la fila,
    #: `index.get(key)` es `None` y el melón no contaba **por no estar en el catálogo**, no
    #: por ser un `ignored_suggestion` — o sea que la aserción pasaba sin haber llegado nunca
    #: al filtro que dice verificar. Con la fila en la misma categoría que la banana, contar
    #: el pospuesto diría "2 ítems".
    db.add(
        FoodItem(
            canonical_name="melon",
            category=banana.category,
            base_unit="unit",
            perishable=True,
        )
    )
    db.flush()

    _record(db, diego, "banana")
    _record(db, diego, "melon", signal_type="ignored_suggestion", value=-0.3)

    r = authenticated_client.get("/profile/")
    assert r.status_code == 200, r.text[:500]
    assert _("Patterns across categories") in r.text
    assert _plural("%(num)d item", "%(num)d items", 1) in r.text
    assert _plural("%(num)d item", "%(num)d items", 2) not in r.text
    #: Y tampoco aparece como fila propia: posponer una tarjeta no es una opinión sobre el
    #: sujeto, así que no se muestra como si fuera un gusto. Se verifica contra el campo
    #: oculto del formulario de olvidar, que es el único lugar donde el nombre aparece como
    #: dato y no dentro de una frase.
    assert 'value="melon"' not in r.text
    no_button = _(
        "These are not stored on their own. Forget the items that back one and it goes"
        " with them."
    )
    assert no_button in r.text


def test_a_muscle_group_conclusion_says_it_is_a_muscle_group_and_not_a_food_one(
    authenticated_client: TestClient, db: Session, diego: User
) -> None:
    """Desde la 4.5.8 la lista de categorías mezcla dos vocabularios, y tiene que decir cuál.

    Un ejercicio del catálogo generaliza a su grupo muscular, así que "Pecho" sale en la
    misma lista que "Verduras". Hasta la 4.5.8 el rótulo estaba clavado al mapa de
    categorías de alimento, que a `chest` le contestaba el valor crudo capitalizado: la
    conclusión salía en inglés y sin decir de qué era.

    Y el caso que hace falta el rótulo de tipo: el mismo grupo muscular aparece **dos
    veces** en el panel —arriba como sujeto, por lo que la persona entrenó, y acá como
    conclusión, por lo que opinó de los ejercicios de ese grupo—. Son dos filas distintas,
    una con botón de olvido y la otra sin él, y sin el rótulo se leen como una repetición.
    """
    db.add(ExerciseType(name="Bench Press", category="strength", muscle_group="chest"))
    db.flush()

    #: La conclusión: una opinión sobre el ejercicio del catálogo, que es lo que
    #: `attribute_index` sabe mapear a su grupo.
    _record(
        db,
        diego,
        "bench press",
        entity_type="exercise",
        signal_type="rejected_suggestion",
        value=-1.0,
    )
    #: Y el sujeto: la captura de un entrenamiento escribe el grupo con la clave ya
    #: normalizada, que sí tiene filas propias — a diferencia de una categoría de alimento.
    _record(db, diego, "chest", entity_type="muscle_group", signal_type="repeated_activity")

    r = authenticated_client.get("/profile/")
    assert r.status_code == 200, r.text[:500]

    assert _("Patterns across categories") in r.text
    #: Los rótulos esperados se piden a los macros y no se escriben acá: los ocho grupos van
    #: con `pgettext('muscle group', …)` —porque `_('Back')` ya era el "Volver" de los
    #: botones— y un `_("Chest")` en el test compararía contra otra entrada del catálogo, que
    #: hoy coincide y en la fase 5, cuando se traduzcan, deja de coincidir.
    assert _muscle_group_label("chest") in r.text, "el grupo rotulado con su propio mapa"
    assert _kind_label("muscle_group") in r.text, "y diciendo de qué clase de grupo es"
    #: Sin señales de alimentos no hay ninguna categoría de alimento, así que el otro
    #: rótulo no puede estar: si estuviera, el macro habría caído en el mapa equivocado.
    assert _kind_label("food_category") not in r.text
    #: Las dos filas conviven: la de sujeto lleva su campo oculto de olvido y la de
    #: conclusión no tiene ninguno propio.
    assert 'value="chest"' in r.text


def test_the_two_screens_no_longer_call_declared_preferences_learned(
    authenticated_client: TestClient, db: Session, diego: User
) -> None:
    """`recommendation_preferences` es lo que la persona **dijo**, no lo que se aprendió.

    Las dos pantallas rotulaban esa tabla como "aprendido" —y `/suggestions/` decía además
    que venía "de lo que registrás", que es la otra tabla—. Con el panel nuevo mostrando lo
    que el motor sí dedujo, dos secciones tituladas igual serían indistinguibles: una se
    corrige escribiendo otra cosa, la otra olvidándola.
    """
    from app.schemas.suggestion import RecommendationPreferenceCreate
    from app.services.suggestion_service import SuggestionService

    SuggestionService(db).save_preference(
        diego.id,
        RecommendationPreferenceCreate(
            item_type="food", item_name="hígado", preference_signal="dislikes"
        ),
    )

    #: Los rótulos viejos se buscan **traducidos** y no por su msgid: la app rinde en es_AR,
    #: así que "Learned Preferences" no aparecía en la página ni cuando el título estaba
    #: puesto, y las dos aserciones pasaban sin poder fallar. `_()` devuelve la traducción
    #: mientras el msgid siga en el catálogo, y el msgid en inglés si ya salió — las dos
    #: formas son lo que habría en la página si el rótulo volviera.
    retired = (_("Learned Preferences"), _("What we learned about you"))
    for url in ("/profile/", "/suggestions/"):
        r = authenticated_client.get(url)
        assert r.status_code == 200, (url, r.text[:500])
        assert _("What you told us") in r.text, url
        for label in retired:
            assert label not in r.text, (url, label)


def test_forgetting_a_subject_deletes_its_rows_and_returns_the_panel_without_it(
    authenticated_client: TestClient, db: Session, diego: User
) -> None:
    """Olvidar borra filas: no hay columna de "olvidado" y tampoco haría falta.

    Lo aprendido *es* el conjunto de señales, así que sacarlas es lo que significa olvidar.
    Y con HTMX vuelve el panel recalculado y no un flash, porque el objetivo del gesto es
    ver que la fila ya no está — un flash lo rinde `base.html`, que no está en la respuesta
    cuando el swap reemplaza una sección.
    """
    _record(db, diego, "pollo")
    _record(db, diego, "lentejas")

    resp = _forget(authenticated_client, "food", "pollo")
    assert resp.status_code == 200, resp.text[:500]

    assert (
        _("Forgotten: %(subject)s. If it happens again, it gets learned again.", subject="pollo")
        in resp.text
    )
    # Contra el hidden del formulario de olvidar y no contra el texto suelto: el nombre
    # aparece dentro del propio mensaje de confirmación, que es correcto que lo nombre.
    assert 'value="pollo"' not in resp.text
    # Lo otro sigue: olvidar es por sujeto, no un borrón y cuenta nueva.
    assert 'value="lentejas"' in resp.text
    # Y es el fragmento, no la página: el swap apunta a una sección.
    assert "<html" not in resp.text

    remaining = {s.entity_name for s in db.query(BehaviorSignal).filter_by(user_id=diego.id)}
    assert remaining == {"lentejas"}


def test_forgetting_finds_the_row_even_when_the_accents_differ(
    authenticated_client: TestClient, db: Session, diego: User
) -> None:
    """El nombre que la pantalla imprime no es el que la fila guarda, y ahí estaba el bug.

    `BehaviorSignalRepository.record` guarda `entity_name.lower()` con los acentos que
    traía ("brócoli"), y el panel muestra la forma comparable de `normalize_subject`
    ("brocoli"). Un `DELETE ... WHERE entity_name = ?` con lo que el formulario manda
    borraba cero filas y contestaba que todo bien, que es la peor de las dos formas de
    fallar: la persona cree que corrigió algo y el motor sigue igual.
    """
    _record(db, diego, "brócoli", value=-1.0, signal_type="rejected_suggestion")

    r = authenticated_client.get("/profile/")
    #: A la vista va el nombre **como se escribió**, y en el campo oculto la clave con la
    #: que se compara. Las dos cosas: el panel imprimía la forma normalizada, así que una
    #: casa que escribe en castellano leía sus propios alimentos mal escritos y no
    #: reconocía lo que la app había aprendido; y si la clave dejara de viajar, el borrado
    #: no encontraría la fila — que es el bug que este test cuida.
    assert "brócoli" in r.text, "a la vista, el nombre como se escribió"
    assert 'value="brocoli"' in r.text, "en el formulario, la clave normalizada"
    assert _("You pass on it") in r.text

    resp = _forget(authenticated_client, "food", "brocoli")
    assert resp.status_code == 200, resp.text[:500]
    assert db.query(BehaviorSignal).filter_by(user_id=diego.id).count() == 0


def test_forgetting_nothing_says_so_instead_of_claiming_success(
    authenticated_client: TestClient, db: Session, diego: User
) -> None:
    """Cero borrados no es un error, pero tampoco es "listo".

    Puede ser un doble clic, o una fila que se fue con otra pestaña. Lo que no puede es
    afirmar que pasó algo que no pasó.
    """
    resp = _forget(authenticated_client, "food", "pollo")
    assert resp.status_code == 200, resp.text[:500]
    assert _("There was nothing left to forget about that.") in resp.text
    assert (
        _("Forgotten: %(subject)s. If it happens again, it gets learned again.", subject="pollo")
        not in resp.text
    )


def test_forgetting_only_touches_your_own_signals(
    authenticated_client: TestClient, db: Session, diego: User, rocio: User
) -> None:
    """Regla 4 de `AGENTS.md`, en el caso que la vuelve concreta.

    Diego y Rocío están en el mismo hogar y no tienen los mismos gustos: que uno olvide el
    pollo no puede tocar lo que el motor aprendió del otro. El filtro está en el servicio
    al leer **y** en el `DELETE`, que es la única capa donde un id ajeno no se puede colar
    por un bug de arriba.
    """
    _record(db, diego, "pollo")
    _record(db, rocio, "pollo")

    resp = _forget(authenticated_client, "food", "pollo")
    assert resp.status_code == 200, resp.text[:500]

    survivors = db.query(BehaviorSignal).all()
    assert [s.user_id for s in survivors] == [rocio.id]


def test_without_htmx_forgetting_redirects_with_the_message_in_the_flash(
    authenticated_client: TestClient, db: Session, diego: User
) -> None:
    """Sin JS el gesto tiene que funcionar igual, con el mecanismo de siempre.

    El formulario lleva `action` además de `hx-post` justamente para esto, y la barra final
    del destino no es un detalle: `/profile` existe solo como el 307 de `redirect_slashes`.
    """
    _record(db, diego, "pollo")

    resp = _forget(authenticated_client, "food", "pollo", htmx=False)
    assert resp.status_code == 302, resp.text[:500]
    assert resp.headers["location"] == "/profile/"

    landing = authenticated_client.get("/profile/")
    assert (
        _("Forgotten: %(subject)s. If it happens again, it gets learned again.", subject="pollo")
        in landing.text
    )
    assert db.query(BehaviorSignal).filter_by(user_id=diego.id).count() == 0


def test_an_over_long_subject_name_is_trimmed_instead_of_echoed_back(
    authenticated_client: TestClient, db: Session, diego: User
) -> None:
    """El recorte reemplaza a un `Form(max_length=...)`, y no es lo mismo.

    Un `max_length` de FastAPI contesta 422 con el valor recibido dentro de
    `detail[0].input`: un texto de 10 MB vuelve al navegador dentro de la respuesta de
    error. Recortar no tiene ese problema y además hace lo que corresponde con un nombre de
    sujeto, que es un dato acotado por la columna que lo guarda.
    """
    resp = _forget(authenticated_client, "food", "x" * 5000)
    assert resp.status_code == 200, resp.text[:500]
    assert "x" * 500 not in resp.text
    assert _("There was nothing left to forget about that.") in resp.text


def test_the_recency_the_panel_prints_is_measured_against_the_same_instant(
    db: Session, diego: User
) -> None:
    """`days_since` es un campo y no una propiedad que lea el reloj, y por eso.

    El descuento por edad de la afinidad se calcula contra el `now` que recibió
    `learned_subjects`. Una propiedad con su propio `datetime.now()` diría "hace 3 días" al
    lado de una confianza calculada para otro instante, y con un `now` fijo las dos cifras
    hablarían de fechas distintas.
    """
    _record(db, diego, "pollo", days_ago=10)
    signals = BehaviorSignalRepository(db).get_user_signals(diego.id, limit=None)

    reference = datetime.now(tz=timezone.utc) + timedelta(days=5)
    rows = learning.learned_subjects(signals, now=reference)
    assert [row.days_since for row in rows] == [15]

    # Y una señal grabada en el mismo request que la lectura redondea a "hoy" en vez de
    # decir "hace -1 días".
    rows = learning.learned_subjects(
        signals, now=datetime.now(tz=timezone.utc) - timedelta(days=20)
    )
    assert [row.days_since for row in rows] == [0]


def test_a_declared_preference_is_shown_without_a_button_that_cannot_deliver(
    authenticated_client: TestClient, db: Session, diego: User
) -> None:
    """Una preferencia declarada aparece en el panel, y ahí no se puede "olvidar".

    `save_preference` escribe **dos** cosas: la fila de `recommendation_preferences` que se
    ve arriba en "lo que nos dijiste" y una señal `explicit_preference` que el motor agrega
    como cualquier otra. La señal tiene que seguir contándose acá —el panel es lo que el
    scorer lee, y si se la salteara las cifras del panel dejarían de ser las del motor—,
    pero el botón de olvido no puede ofrecerse: borra señales, y la preferencia seguiría
    filtrando las sugerencias (`apply_hard_constraints` la lee sin descuento) sin ninguna
    ruta que la borre. La pantalla diría "listo, lo olvidé" sobre algo que sigue filtrando,
    que es la peor de las dos formas de fallar.
    """
    from app.schemas.suggestion import RecommendationPreferenceCreate
    from app.services.suggestion_service import SuggestionService

    SuggestionService(db).save_preference(
        diego.id,
        RecommendationPreferenceCreate(
            item_type="food", item_name="hígado", preference_signal="dislikes"
        ),
    )

    r = authenticated_client.get("/profile/")
    assert r.status_code == 200, r.text[:500]

    # Está en las dos secciones, y eso es correcto: es lo que se dijo *y* lo que el motor
    # lee. Con dos formas del nombre, que es una consecuencia de que las dos tablas lo
    # guarden distinto: la preferencia guarda lo escrito en minúsculas y la señal la pasa por
    # `normalize_subject`, que le saca la tilde.
    assert _("What you told us") in r.text
    assert "hígado" in r.text, "la preferencia declarada, como se escribió"
    assert "higado" in r.text, "la fila del panel, con el nombre que guardó la señal"

    # Sin control de olvido, y con el puntero a donde eso sí se cambia.
    assert _("Forget %(subject)s", subject="higado") not in r.text
    assert 'action="/profile/learned/forget"' not in r.text
    assert _("You told us this") in r.text
    assert 'id="what-you-told-us"' in r.text

    # Y sin el subtítulo que decía lo contrario de esta fila por partida doble: que nada de
    # esto viene de lo que dijiste, y que se puede soltar cualquiera.
    assert (
        _("Deduced from what you log, not from what you told us. You can drop any of it.")
        not in r.text
    )


def test_a_tap_on_a_card_is_not_counted_as_something_you_said(
    authenticated_client: TestClient, db: Session, diego: User
) -> None:
    """Un "1 de lo que dijiste" tiene que significar palabras, no un clic.

    La cifra contaba `source_type == "explicit"`, y `respond_to_suggestion` marca así el
    accepted/rejected de un **tap** en una tarjeta. Un solo "no, gracias" imprimía "1 de lo
    que dijiste" al lado de un alimento sobre el que la persona no había escrito nada — y la
    distinción que el panel promete es justo esa, porque separa lo que se corrige
    escribiendo de lo que se corrige con conducta.
    """
    _record(
        db,
        diego,
        "pollo",
        signal_type="rejected_suggestion",
        value=-1.0,
        source_type="explicit",
    )

    r = authenticated_client.get("/profile/")
    assert r.status_code == 200, r.text[:500]
    assert "pollo" in r.text
    assert _("%(num)d from what you said", num=1) not in r.text
    #: La fila sí existe y sí se puede olvidar: lo que no existe es el rótulo de "dicho".
    assert 'value="pollo"' in r.text


def test_the_panel_only_shows_your_own_signals(
    authenticated_client: TestClient, db: Session, diego: User, rocio: User
) -> None:
    """Regla 4 de `AGENTS.md` del lado de la lectura, que es el lado que expone datos.

    El test de olvidar cubre el `DELETE`; este cubre el `SELECT`, que es donde un filtro de
    hogar en lugar de uno de usuario le mostraría a Diego lo que el motor aprendió de Rocío.
    Están en la misma casa y no tienen los mismos gustos: el panel de uno no puede nombrar
    los alimentos del otro.
    """
    _record(db, diego, "pollo")
    _record(db, rocio, "lentejas")

    r = authenticated_client.get("/profile/")
    assert r.status_code == 200, r.text[:500]
    assert 'value="pollo"' in r.text
    assert "lentejas" not in r.text


def test_a_signal_past_the_horizon_is_not_shown_but_is_still_forgotten(
    authenticated_client: TestClient, db: Session, diego: User
) -> None:
    """El horizonte acota lo que se **muestra**, y no lo que se puede borrar.

    Son dos preguntas distintas y la diferencia es la razón por la que el servicio no
    reutiliza la misma consulta para las dos: una señal más vieja que la ventana ya no pesa
    nada en el score y mostrarla sería decir que sí, pero sigue siendo un dato personal
    guardado sobre esa persona. Si pide olvidar el pollo, se van todas las del pollo.
    """
    _record(db, diego, "pollo", days_ago=learning.SIGNAL_HORIZON_DAYS + 40)

    r = authenticated_client.get("/profile/")
    assert r.status_code == 200, r.text[:500]
    assert "pollo" not in r.text
    assert _("Nothing learned yet") in r.text

    resp = _forget(authenticated_client, "food", "pollo")
    assert resp.status_code == 200, resp.text[:500]
    assert (
        _("Forgotten: %(subject)s. If it happens again, it gets learned again.", subject="pollo")
        in resp.text
    )
    assert db.query(BehaviorSignal).filter_by(user_id=diego.id).count() == 0


def test_the_message_names_what_was_deleted_and_not_what_was_typed(
    authenticated_client: TestClient, db: Session, diego: User
) -> None:
    """La confirmación nombra la fila que se fue, no el texto que llegó del formulario.

    La comparación es por forma normalizada, así que cualquier texto cuya forma coincida
    llega hasta el borrado: un `PÓLLO!!!` escrito a mano borra las filas de "pollo". Que la
    pantalla contestara "Olvidado: PÓLLO!!!" era devolverle un texto que la persona nunca
    guardó, sobre una acción que no se puede deshacer.
    """
    _record(db, diego, "pollo")

    resp = _forget(authenticated_client, "food", "PÓLLO!!!")
    assert resp.status_code == 200, resp.text[:500]
    assert (
        _("Forgotten: %(subject)s. If it happens again, it gets learned again.", subject="pollo")
        in resp.text
    )
    assert "PÓLLO" not in resp.text
    assert db.query(BehaviorSignal).filter_by(user_id=diego.id).count() == 0
