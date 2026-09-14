"""La acción primaria: el mapa de destinos y las dos rutas que lo usan.

Lo que se prueba acá no es "el botón se dibuja" sino las dos cosas que pueden
desincronizarse en silencio: que el destino que calcula `app/web/actions.py` sea uno que
la app efectivamente sirve, y que las rutas `/act` graben lo que dicen grabar **antes**
de redirigir. Un destino inventado no falla en ningún test de plantilla: falla el día que
alguien lo toca.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.household import Household
from app.models.notification import Notification
from app.models.signal import BehaviorSignal
from app.models.suggestion import Suggestion
from app.models.user import User
from app.web.actions import notification_action, suggestion_action


def _notification(db: Session, **kwargs) -> Notification:
    defaults = {
        "category": "low_stock",
        "title": "Running low",
        "body": "No queda leche.",
        "source_type": "job",
    }
    n = Notification(**{**defaults, **kwargs})
    db.add(n)
    db.flush()
    return n


def _suggestion(db: Session, household: Household, user: User, **kwargs) -> Suggestion:
    defaults = {
        "scope_type": "user",
        "household_id": household.id,
        "scope_user_id": user.id,
        "category": "meal",
        #: Desde la 4.4 el sujeto es lo que se aprende: sin él la respuesta no graba
        #: señal (a propósito — no se deriva del título). Los generadores siempre lo
        #: declaran, así que un fixture sin sujeto no representa nada real.
        "subject_type": "food",
        "subject_name": "lentils",
        "title": "Try lentils tonight",
        "text": "You have lentils and haven't had legumes this week.",
        "rationale": "Dietary variety supports micronutrient balance.",
        "source_type": "rule",
        "status": "pending",
    }
    s = Suggestion(**{**defaults, **kwargs})
    db.add(s)
    db.flush()
    return s


class TestNotificationActionMap:
    """El destino de un aviso, que es una función pura: ni base ni request."""

    def test_low_stock_lands_on_that_item(self):
        action = notification_action("low_stock", "pantry_stock", 7)
        assert action is not None
        # El ancla es el `id` de la raíz de `pantry/partials/stock_card.html`, que ya era
        # el contrato de `hx-target` del ajuste de stock. Si ese `id` cambia, este test
        # sigue pasando y el ancla deja de resolver: por eso está también
        # `test_stock_card_still_carries_the_anchor_id`.
        assert action.href == "/pantry/?low=1#stock-item-7"

    def test_low_stock_without_a_subject_still_lands_on_the_pantry(self):
        # Los avisos de la v1 no grababan sujeto, y siguen en la base: la tarjeta tiene
        # que llevar a algún lado igual, no quedarse sin botón.
        action = notification_action("low_stock")
        assert action is not None
        assert action.href == "/pantry/?low=1"

    def test_low_stock_ignores_a_subject_of_another_type(self):
        # `#stock-item-4` con un id que no es de `pantry_stock` apuntaría a la tarjeta
        # de *otro* ítem: mejor sin ancla que en el ítem equivocado.
        action = notification_action("low_stock", "food_item", 4)
        assert action is not None
        assert action.href == "/pantry/?low=1"

    @pytest.mark.parametrize(
        ("category", "prefill"),
        [
            ("inactivity", "workout"),
            ("metric_reminder", "weight"),
            ("meal_reminder", "meal"),
            ("sleep_reminder", "sleep"),
        ],
    )
    def test_absence_categories_open_the_capture_prefilled(self, category, prefill):
        action = notification_action(category)
        assert action is not None
        assert action.href == f"/capture/?prefill={prefill}"

    def test_a_category_with_nothing_to_do_has_no_action(self):
        # `info` y `trend` describen; no piden nada. Devolver `None` es lo que hace que
        # la tarjeta no dibuje un botón que no sabe a dónde va.
        assert notification_action("info") is None
        assert notification_action("trend") is None


class TestSuggestionActionMap:
    def test_a_meal_suggestion_logs_a_meal(self):
        action = suggestion_action("meal", "rule")
        assert action is not None
        assert action.href == "/capture/?prefill=meal"

    def test_an_activity_suggestion_logs_a_workout(self):
        action = suggestion_action("activity", "rule")
        assert action is not None
        assert action.href == "/capture/?prefill=workout"

    def test_a_shopping_suggestion_opens_the_missing_items(self):
        # `?low=1` y no la despensa entera: las dos sugerencias de `shopping` que existen
        # **son** la lista de faltantes, así que abrir todo obliga a buscar de nuevo lo
        # que la tarjeta ya nombró.
        action = suggestion_action("shopping", "stock")
        assert action is not None
        assert action.href == "/pantry/?low=1"

    def test_advice_without_something_to_log_keeps_its_plain_yes(self):
        # `habit` no lleva a ninguna pantalla: no hay nada que registrar.
        assert suggestion_action("habit", "rule") is None

    @pytest.mark.parametrize("category", ["meal", "activity", "habit"])
    def test_blood_panel_advice_never_gets_an_action(self, category):
        # Es el caso que importa, y no se puede decidir por la categoría: el generador de
        # sangre emite 19 filas de `meal` y 5 de `activity` — "comé menos carne roja" —,
        # así que mirar solo la categoría le ponía un "Anotar una comida" a un consejo de
        # **no** comer algo, y encima reemplazando al "Me parece bien": la única respuesta
        # afirmativa posible abría la captura con "Comimos " escrito.
        assert suggestion_action(category, "blood_analysis") is None

    def test_the_exclusion_matches_what_the_generator_actually_writes(
        self, db: Session, diego: User
    ):
        # El test de arriba vale por lo que este verifica, que son los dos hechos en los
        # que se apoya la exclusión: que el generador estampe este `source_type` en cada
        # candidato, y que sus categorías no lo distingan de nada — son las mismas `meal`
        # y `activity` que emite el resto, no `habit`. Si cualquiera de las dos cosas
        # cambia, el consejo del panel vuelve a llevar botón y nada más lo nota.
        from app.recommendations.context import BloodPanel
        from app.recommendations.generators.blood_generator import (
            _BIOMARKER_SUGGESTIONS,
            generate,
        )

        declared = {
            s["category"]
            for buckets in _BIOMARKER_SUGGESTIONS.values()
            for bucket in buckets.values()
            for s in bucket
        }
        assert "meal" in declared

        produced = generate(
            diego,
            BloodPanel(
                values={"hemoglobin": {"status": "low", "value": 10, "unit": "g/dL"}},
                analysis_date=None,
                age_days=None,
            ),
        )
        assert produced, "el generador dejó de emitir para un hemograma bajo"
        assert {c["source_type"] for c in produced} == {"blood_analysis"}


def _categories_the_jobs_write() -> set[str]:
    """Las categorías de notificación que algún job escribe hoy, leídas del módulo.

    Las cuatro ausencias por persona son instancias de `_Absence` en el módulo de jobs, así
    que se leen de ahí en vez de repetirlas: es lo que hace que agregar una quinta rompa
    los tests que tienen que romperse. `low_stock` no es una ausencia y va a mano.
    """
    from app.jobs import notification_jobs

    absences = {
        value.category
        for value in vars(notification_jobs).values()
        if isinstance(value, notification_jobs._Absence)
    }
    assert len(absences) == 4, "cambió el conjunto de ausencias por persona"
    return absences | {"low_stock"}


class TestEveryDestinationExists:
    """Que el destino sea una URL que la app sirve, y no una que parece servir."""

    #: Las de sugerencia son las tres que algún generador realmente emite; las de
    #: notificación **no** se listan acá, se derivan de los jobs. Una categoría que nadie
    #: emite sería una rama inalcanzable y un test que parece cobertura sin proteger nada.
    _SUGGESTION_CATEGORIES = ("meal", "activity", "shopping")

    def _all_actions(self):
        #: Derivado y no escrito a mano: con la tupla, una quinta ausencia hacía fallar el
        #: test de los mapas de `domain.html` —que sí introspecciona—, alguien completaba
        #: los tres mapas, y `notification_action` quedaba sin tocar. El aviso salía con
        #: ícono y rótulo lindos, sin botón, y `POST /act` redirigía a `/notifications/`:
        #: justo la acción primaria que es el punto de la sub-fase, ausente y en verde.
        for category in sorted(_categories_the_jobs_write()):
            action = notification_action(category, "pantry_stock", 1)
            assert action is not None, f"{category} no tiene acción primaria"
            yield category, action

        #: Los dos mapas se recorren por separado a propósito: con un `or` entre los dos,
        #: una categoría que perdiera su acción de notificación podía quedar tapada por la
        #: de sugerencia que se llama parecido.
        for category in self._SUGGESTION_CATEGORIES:
            action = suggestion_action(category, "rule")
            assert action is not None, category
            yield category, action

    def test_no_destination_pays_a_redirect(self):
        # `/capture?prefill=x` y `/pantry?low=1` existen, pero cuestan un 307 de
        # `redirect_slashes` — y en el caso del ancla, el fragmento no sobrevive a todos
        # los clientes en un redirect. La barra va antes del `?`.
        for category, action in self._all_actions():
            path = action.href.split("?")[0].split("#")[0]
            assert path.endswith("/"), f"{category} → {action.href}"

    def test_every_destination_answers(self, authenticated_client: TestClient):
        # Contra la app de verdad: si alguien renombra `/pantry/` o cambia el prefijo de
        # la captura, esto se cae acá y no en la pantalla de una persona.
        for category, action in self._all_actions():
            response = authenticated_client.get(action.href, follow_redirects=False)
            assert response.status_code == 200, f"{category} → {action.href}"

    def test_capture_knows_every_prefill_key_we_send(self):
        # El otro lado del contrato vive en JS: `prefillMap` en `capture/index.html` es
        # el que traduce la clave a texto. Una clave que no esté ahí abre la captura con
        # el textarea vacío — que es exactamente el defecto de la Fase 1 volviendo por
        # otra puerta, y sin error visible.
        template = Path("app/templates/capture/index.html").read_text(encoding="utf-8")
        block = re.search(r"prefillMap:\s*\{(.*?)\}", template, re.DOTALL)
        assert block is not None, "prefillMap ya no está donde estaba"
        known = set(re.findall(r"(\w+):\s*'", block.group(1)))

        for category, action in self._all_actions():
            if "prefill=" in action.href:
                key = action.href.split("prefill=")[1]
                assert key in known, f"{category} → {key} no está en prefillMap"

    def test_stock_card_still_carries_the_anchor_id(self):
        # El ancla de `low_stock` apunta a este `id`. Es el mismo que usa el `hx-target`
        # del ajuste de stock, así que no es probable que cambie — pero si cambia, el
        # aviso deja de aterrizar en el ítem y nada más lo nota.
        card = Path("app/templates/pantry/partials/stock_card.html").read_text(encoding="utf-8")
        assert 'id="stock-item-{{ item.id }}"' in card


class TestEveryCategoryHasAFace:
    """Que una categoría que los jobs escriben tenga ícono, tono y rótulo.

    Los tres mapas de `components/domain.html` degradan en silencio: una categoría que no
    esté cae en `'bell'`, en el tono `info` y en un `category | replace('_',' ') | title`.
    O sea que el aviso de sueño ausente saldría con una campanita gris y el rótulo
    "Sleep Reminder" sin pasar por el catálogo, y ningún test se caería. Agregar una
    ausencia nueva y olvidarse de los mapas es el error que esto ataja.
    """

    #: El tercer mapa se ancla en su macro y no en `set labels` a secas: hay otro `labels`
    #: en el mismo archivo, el de los tipos de comida, y la búsqueda floja lo encontraba
    #: primero — el test fallaba con "breakfast, snack, other" y no con lo que mira.
    _MAPS = {
        "NOTIFICATION_ICONS": r"set NOTIFICATION_ICONS = \{(.*?)\}",
        "NOTIFICATION_TONES": r"set NOTIFICATION_TONES = \{(.*?)\}",
        "notification_category_label": (
            r"macro notification_category_label.*?set labels = \{(.*?)\}"
        ),
    }

    @pytest.mark.parametrize("block", sorted(_MAPS))
    def test_the_map_covers_them(self, block: str) -> None:
        domain = Path("app/templates/components/domain.html").read_text(encoding="utf-8")
        #: Los tres son literales de Jinja `{% set X = { … } %}` en el mismo archivo, así
        #: que alcanza con leer el archivo: no hace falta renderizar una plantilla para
        #: saber qué claves declara.
        match = re.search(self._MAPS[block], domain, re.DOTALL)
        assert match is not None, f"{block} ya no está donde estaba"
        known = set(re.findall(r"'([a-z_]+)':", match.group(1)))

        assert _categories_the_jobs_write() <= known


class TestTheButtonReachesThePage:
    """Que las tres superficies dibujen el botón, y que lo dibujen como un `<form>`.

    Se afirma sobre la estructura y no sobre el rótulo: el rótulo pasa por el catálogo
    (`Log a meal` sale "Anotar una comida"), así que un test contra el texto se rompería
    con cada traducción. Lo que importa es a dónde postea.
    """

    def test_the_notification_list_offers_the_action(
        self, authenticated_client: TestClient, db: Session, household: Household
    ):
        n = _notification(
            db,
            household_id=household.id,
            related_entity_type="pantry_stock",
            related_entity_id=31,
        )

        html = authenticated_client.get("/notifications/").text

        assert f'action="/notifications/{n.id}/act"' in html
        # Y el aviso sigue teniendo sus dos gestos de siempre.
        assert f'hx-post="/notifications/{n.id}/read"' in html
        assert f'hx-post="/notifications/{n.id}/dismiss"' in html

    def test_the_card_still_renders_on_its_own(
        self, authenticated_client: TestClient, db: Session, household: Household
    ):
        # `POST /read` devuelve la tarjeta sola, con un contexto de `{request, n}`: el
        # botón usa dos globals (`notification_action` y `csrf_token`) y el segundo
        # necesita el `request`. Si alguna vez se llama sin él, esto se cae acá y no en
        # el swap de una persona.
        n = _notification(
            db,
            household_id=household.id,
            related_entity_type="pantry_stock",
            related_entity_id=31,
        )

        response = authenticated_client.post(
            f"/notifications/{n.id}/read", headers={"HX-Request": "true"}
        )

        assert response.status_code == 200
        assert f'action="/notifications/{n.id}/act"' in response.text

    def test_a_notification_without_action_gets_no_button(
        self, authenticated_client: TestClient, db: Session, diego: User
    ):
        # El par de este test y el anterior es lo que prueba que la afirmación
        # discrimina: la misma cadena está en un caso y no está en el otro.
        n = _notification(db, user_id=diego.id, category="info")

        html = authenticated_client.get("/notifications/").text

        assert f'action="/notifications/{n.id}/act"' not in html
        assert f'hx-post="/notifications/{n.id}/dismiss"' in html

    def test_an_action_replaces_the_plain_yes(
        self,
        authenticated_client: TestClient,
        db: Session,
        household: Household,
        diego: User,
    ):
        s = _suggestion(db, household, diego, category="meal")

        html = authenticated_client.get("/suggestions/").text

        assert f'action="/suggestions/{s.id}/act"' in html
        # No hay dos botones afirmativos: el hidden de `accepted` ya no está.
        assert 'name="status" value="accepted"' not in html
        # Las dos respuestas negativas sí.
        assert 'name="status" value="dismissed"' in html
        assert 'name="status" value="rejected"' in html

    def test_a_suggestion_without_action_keeps_its_three_answers(
        self,
        authenticated_client: TestClient,
        db: Session,
        household: Household,
        diego: User,
    ):
        s = _suggestion(db, household, diego, category="habit")

        html = authenticated_client.get("/suggestions/").text

        assert f'action="/suggestions/{s.id}/act"' not in html
        assert 'name="status" value="accepted"' in html

    def test_the_home_card_offers_the_action_too(
        self,
        authenticated_client: TestClient,
        db: Session,
        household: Household,
        diego: User,
    ):
        s = _suggestion(db, household, diego, category="activity")

        html = authenticated_client.get("/").text

        assert f'action="/suggestions/{s.id}/act"' in html
        # El "Me parece bien" del Home es un `hx-post` con `hx-vals`, no un hidden.
        assert 'hx-vals=\'{"status": "accepted"}\'' not in html
        assert 'hx-vals=\'{"status": "dismissed"}\'' in html


class TestTheApiSaysWhoTheSubjectIs:
    """El otro consumidor del sujeto: `/api/v1/notifications/`.

    La pantalla web no pasa por este schema — lee la fila del ORM —, así que sin esta
    prueba los dos campos que `NotificationRead` agrega no los toca **nada** en todo el
    repo: parecen muertos, y borrarlos rompe la API documentada sin que se caiga un test.
    """

    def test_the_payload_carries_the_subject(
        self, authenticated_client: TestClient, db: Session, household: Household
    ):
        n = _notification(
            db,
            household_id=household.id,
            related_entity_type="pantry_stock",
            related_entity_id=31,
        )

        response = authenticated_client.get("/api/v1/notifications/")

        assert response.status_code == 200
        payload = next(item for item in response.json() if item["id"] == n.id)
        assert payload["related_entity_type"] == "pantry_stock"
        assert payload["related_entity_id"] == 31


class TestNotificationActRoute:
    def test_it_redirects_to_the_action_and_marks_it_read(
        self, authenticated_client: TestClient, db: Session, diego: User
    ):
        n = _notification(
            db,
            user_id=diego.id,
            category="inactivity",
            related_entity_type="user",
            related_entity_id=diego.id,
        )

        response = authenticated_client.post(f"/notifications/{n.id}/act", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["location"] == "/capture/?prefill=workout"
        # Sin esto el globito del nav seguiría contando algo que la persona acaba de
        # hacer: la app pediría dos veces el mismo entrenamiento.
        db.refresh(n)
        assert n.read_at is not None
        # Leído, no descartado: el aviso sigue siendo el registro de que esto pasó.
        assert n.dismissed_at is None

    def test_the_low_stock_redirect_carries_the_subject(
        self, authenticated_client: TestClient, db: Session, household: Household
    ):
        n = _notification(
            db,
            household_id=household.id,
            related_entity_type="pantry_stock",
            related_entity_id=31,
        )

        response = authenticated_client.post(f"/notifications/{n.id}/act", follow_redirects=False)

        assert response.headers["location"] == "/pantry/?low=1#stock-item-31"

    def test_a_category_without_action_goes_back_to_the_list(
        self, authenticated_client: TestClient, db: Session, diego: User
    ):
        n = _notification(db, user_id=diego.id, category="info")

        response = authenticated_client.post(f"/notifications/{n.id}/act", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["location"] == "/notifications/"
        db.refresh(n)
        assert n.read_at is not None

    def test_someone_elses_notification_is_a_404(
        self, authenticated_client: TestClient, db: Session, rocio: User
    ):
        # Regla 4: el hogar es de dos y un aviso personal es de uno. El scope de lectura
        # de `NotificationService` es lo que lo sostiene; esto lo verifica desde la ruta.
        n = _notification(db, user_id=rocio.id, category="inactivity")

        response = authenticated_client.post(f"/notifications/{n.id}/act", follow_redirects=False)

        assert response.status_code == 404
        db.refresh(n)
        assert n.read_at is None


class TestSuggestionActRoute:
    def test_it_records_accepted_before_redirecting(
        self,
        authenticated_client: TestClient,
        db: Session,
        household: Household,
        diego: User,
    ):
        s = _suggestion(db, household, diego, category="meal")

        response = authenticated_client.post(f"/suggestions/{s.id}/act", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["location"] == "/capture/?prefill=meal"
        # Lo que hace que reemplazar "Me parece bien" no pierda la señal positiva que
        # alimenta al scorer: hacer la cosa ya la acepta.
        db.refresh(s)
        assert s.status == "accepted"
        assert s.responded_at is not None
        # Y la señal misma, que es la razón entera por la que el botón puede reemplazar
        # al "Me parece bien". Sin esto, alguien que más adelante escriba
        # `suggestion.status = "accepted"` a mano en `/act` — o que mueva el redirect
        # antes de `respond_to_suggestion` — deja pasar toda la suite: la pantalla se ve
        # igual y el motor deja de aprender lo positivo en silencio.
        signals = db.query(BehaviorSignal).filter(BehaviorSignal.user_id == diego.id).all()
        assert [(sig.signal_type, float(sig.value)) for sig in signals] == [
            ("accepted_suggestion", 1.0)
        ]
        assert signals[0].source_entity_id == s.id

    def test_a_category_without_action_still_records_the_response(
        self,
        authenticated_client: TestClient,
        db: Session,
        household: Household,
        diego: User,
    ):
        # La plantilla no dibuja el botón para `habit`, así que esto es una URL a mano.
        # La respuesta queda grabada igual y la vuelta es la lista.
        s = _suggestion(db, household, diego, category="habit")

        response = authenticated_client.post(f"/suggestions/{s.id}/act", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["location"] == "/suggestions/"
        db.refresh(s)
        assert s.status == "accepted"

    def test_someone_elses_suggestion_is_not_actionable(
        self,
        authenticated_client: TestClient,
        db: Session,
        household: Household,
        rocio: User,
    ):
        # Regla 4: el hogar es de dos y una sugerencia personal es de uno. Sin JS la
        # salida de fallo es el redirect con flash de `_invalid` — un 302 a la lista,
        # igual que el camino feliz de una categoría sin acción —, así que lo que
        # distingue los dos casos es que la fila no se tocó.
        s = _suggestion(db, household, rocio, category="meal")

        response = authenticated_client.post(f"/suggestions/{s.id}/act", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["location"] == "/suggestions/"
        db.refresh(s)
        assert s.status == "pending"
        assert s.responded_at is None

    def test_with_htmx_someone_elses_suggestion_is_a_404(
        self,
        authenticated_client: TestClient,
        db: Session,
        household: Household,
        rocio: User,
    ):
        s = _suggestion(db, household, rocio, category="meal")

        response = authenticated_client.post(
            f"/suggestions/{s.id}/act",
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )

        assert response.status_code == 404
        db.refresh(s)
        assert s.status == "pending"

    def test_it_does_not_collide_with_the_other_post_routes(self, authenticated_client: TestClient):
        # `/generate` y `/preferences` son de un solo segmento y `/{id}/act` de dos, así
        # que no hay ambigüedad — pero el orden de declaración de FastAPI es sensible y
        # este es el test que lo nota si alguien mueve la ruta.
        response = authenticated_client.post("/suggestions/generate", follow_redirects=False)
        assert response.status_code in (200, 302)
