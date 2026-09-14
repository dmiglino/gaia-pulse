"""Page tests for /capture.

Estos tests miran **las filas que quedan en la base**, no solo el HTML. Es la
pantalla donde el usuario consiente una escritura, y el bug que motivó la mayoría
de estos casos era exactamente una divergencia entre las dos cosas: el preview
mostraba los dos avatares y el servicio le anotaba el dato a uno solo.
"""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import local_today, to_local
from app.core.security import hash_password
from app.i18n import _
from app.models.body_metric import BodyMetricLog
from app.models.household import Household
from app.models.meal import MealEvent, MealParticipant
from app.models.nlp import NLPIngestionEvent
from app.models.suggestion import RecommendationPreference
from app.models.user import User
from app.models.workout import WorkoutParticipant


def _pending(user: User, text: str, intents: list[dict], db: Session) -> NLPIngestionEvent:
    """Una captura pendiente con intents dados, sin pasar por el parser.

    Para los casos que no se pueden producir con una frase (un `mixed`, un nombre
    ajeno al hogar): lo que se está probando es la ejecución, no el parseo.
    """
    event = NLPIngestionEvent(
        user_id=user.id,
        input_type="text",
        original_input=text,
        parsed_intent_json=intents,
        parse_confidence=0.8,
        parser_layer="rules",
        status="pending_confirmation",
    )
    db.add(event)
    db.flush()
    return event


def test_capture_prefill_carries_real_openers(authenticated_client: TestClient) -> None:
    """The quick actions link here with ?prefill=…; the textarea must not end up empty.

    The prefill lives in template-embedded JS, so this asserts on the served
    source: the openers are present and the old bracket placeholders are gone.
    """
    r = authenticated_client.get("/capture/?prefill=meal")
    assert r.status_code == 200, r.text[:500]
    for opener in ("'We had '", "'I did '", "'I bought '", "'My weight today is '"):
        assert opener in r.text, opener
    assert "[meal_type]" not in r.text
    assert "this.captureText = this.prefillMap[prefill]" in r.text


def test_capture_openers_are_sentence_starts_not_whole_sentences(
    authenticated_client: TestClient,
) -> None:
    """The chips used to inject finished sentences with invented numbers.

    One extra tap and a dinner nobody ate was queued for saving, weight included.
    They now share the quick actions' openers, so the person still has to finish
    the sentence.
    """
    r = authenticated_client.get("/capture/")
    assert r.status_code == 200, r.text[:500]
    for invented in ("We had pasta for dinner", "74.5 kg", "My weight this morning is"):
        assert invented not in r.text, invented
    # Y el positivo: los chips existen y arrancan una frase que el usuario termina.
    assert "'We had '" in r.text
    assert 'name="text"' in r.text


def test_preview_attributes_a_meal_to_the_person_named_in_the_sentence(
    authenticated_client: TestClient, db: Session, diego: User, rocio: User
) -> None:
    """The accent bug: Rocío's meal used to be saved under Diego's name.

    The parser writes the accent-free key ``rocio``, but the service's user map was
    keyed on ``User.name`` verbatim — ``rocío``. The lookup missed and fell through
    to "whoever comes first in the household", which is Diego. Both sides now go
    through ``normalize_user_key``, so the preview and the write agree on her.
    """
    r = authenticated_client.post("/capture/parse", data={"text": "Rocío ate a banana"})
    assert r.status_code == 200, r.text[:500]
    # Atribuida a ella con el patrón de atribución (avatar + nombre), no con la
    # clave cruda del intent — y sin nombrar a Diego, que es a quien se le guardaba.
    assert f'title="{rocio.display_name}"' in r.text
    assert diego.display_name not in r.text
    assert 'hx-target="#nlp-preview"' in r.text

    event = db.scalars(select(NLPIngestionEvent)).one()
    r = authenticated_client.post(f"/capture/confirm/{event.id}", headers={"HX-Request": "true"})
    assert r.status_code == 200, r.text[:500]

    participants = list(db.scalars(select(MealParticipant)).all())
    assert [p.user_id for p in participants] == [rocio.id]


def test_a_shared_meal_is_saved_for_every_person_the_preview_showed(
    authenticated_client: TestClient, db: Session, diego: User, rocio: User
) -> None:
    """ "We had pasta" → clave ``both``, y ``both`` no es nadie del hogar.

    Ninguna de las cuatro ramas resolvía esa clave, y la de comidas terminaba en
    ``next(iter(user_map.values()))``: la cena compartida se guardaba como una sola
    participación, la del primero que devolvía la query, mientras la pantalla de
    confirmación mostraba a los dos. Ahora ``both`` se abre al hogar entero.
    """
    r = authenticated_client.post("/capture/parse", data={"text": "We had pasta for dinner"})
    assert r.status_code == 200, r.text[:500]
    for person in (diego, rocio):
        assert f'title="{person.display_name}"' in r.text, person.name

    event = db.scalars(select(NLPIngestionEvent)).one()
    r = authenticated_client.post(f"/capture/confirm/{event.id}", headers={"HX-Request": "true"})
    assert r.status_code == 200, r.text[:500]
    assert _("Saved") in r.text

    participants = list(db.scalars(select(MealParticipant)).all())
    assert sorted(p.user_id for p in participants) == sorted([diego.id, rocio.id])


def test_a_weigh_in_for_two_people_is_not_banked_on_one_of_them(
    authenticated_client: TestClient, db: Session, diego: User, rocio: User
) -> None:
    """ "Los dos nos pesamos hoy, Rocío 60" traía **un** número para dos personas.

    Se guardaba 60 kg en el registro de salud de Diego, que es el que hablaba. Un
    pesaje no se reparte: sin destino único no se escribe nada, y eso se avisa en el
    preview — donde todavía se puede corregir la frase — y en el resultado.
    """
    r = authenticated_client.post(
        "/capture/parse", data={"text": "We both weigh in today, Rocío is 60 kg"}
    )
    assert r.status_code == 200, r.text[:500]
    assert (
        _("I could not tell whose this is, so I will not save it. Name the person and try again.")
        in r.text
    )
    # Y no promete a nadie: ni el avatar de él ni el de ella sobre un dato que no va.
    for person in (diego, rocio):
        assert f'title="{person.display_name}"' not in r.text, person.name

    event = db.scalars(select(NLPIngestionEvent)).one()
    r = authenticated_client.post(f"/capture/confirm/{event.id}", headers={"HX-Request": "true"})
    assert r.status_code == 200, r.text[:500]
    assert _("Nothing was saved") in r.text
    assert _("not saved: I could not tell whose this is.") in r.text

    assert list(db.scalars(select(BodyMetricLog)).all()) == []


def test_a_weigh_in_for_the_person_named_lands_in_her_record_and_only_hers(
    authenticated_client: TestClient, db: Session, diego: User, rocio: User
) -> None:
    """El control positivo del pesaje, que faltaba: la fila, con su dueña y su número.

    Las dos aserciones de body metrics de este archivo son negativas (`== []`), así
    que la red no distinguía "se niega a repartir un pesaje ambiguo" de "no registra
    un pesaje nunca", ni "se lo anota a ella" de "se lo anota al primero que
    devuelve la query" — que es exactamente el bug original.
    """
    event = _pending(
        diego,
        "Rocío weighs 60 kg",
        [{"intent_type": "log_body_metric", "user_key": "rocio", "weight_kg": 60.0}],
        db,
    )
    r = authenticated_client.post(f"/capture/confirm/{event.id}", headers={"HX-Request": "true"})
    assert r.status_code == 200, r.text[:500]
    assert rocio.display_name in r.text
    assert diego.display_name not in r.text

    log = db.scalars(select(BodyMetricLog)).one()
    assert (log.user_id, log.weight_kg) == (rocio.id, 60.0)


def test_a_shared_preference_is_saved_for_both_people(
    authenticated_client: TestClient, db: Session, diego: User, rocio: User
) -> None:
    """Un gusto sí se comparte: ``both`` en una preferencia se guarda para los dos.

    Es la otra mitad de la regla — lo que no se puede repartir es una medición, no
    una opinión —, y antes esta rama también caía en "nadie" y no guardaba nada.
    """
    event = _pending(
        diego,
        "we both love pasta",
        [
            {
                "intent_type": "update_preference",
                "user_key": "both",
                "item_type": "food",
                "item_name": "pasta",
                "preference_signal": "likes",
            }
        ],
        db,
    )
    r = authenticated_client.post(f"/capture/confirm/{event.id}", headers={"HX-Request": "true"})
    assert r.status_code == 200, r.text[:500]

    prefs = list(db.scalars(select(RecommendationPreference)).all())
    assert sorted(p.user_id for p in prefs) == sorted([diego.id, rocio.id])
    assert {p.item_name for p in prefs} == {"pasta"}


def test_two_members_with_the_same_first_name_fail_closed(
    db: Session, client: TestClient, household: Household, diego: User
) -> None:
    """Dos "Diego" normalizan a la misma clave, y ahí no hay a quién anotarle.

    El mapa se quedaba con el último y le escribía el dato de salud a esa persona
    con la misma confianza que si la frase la hubiera nombrado. Entre anotárselo a
    quien no es y no anotarlo, no anotarlo es lo único que no falsea el registro de
    nadie: la clave se cae del mapa y la captura queda sin atribuir.
    """
    other = User(
        household_id=household.id,
        name="Diego Martín",
        email="diego.m@test.com",
        password_hash=hash_password("x"),
        baseline_activity_level="light",
        onboarding_completed=True,
    )
    db.add(other)
    db.flush()

    from app.core.config import get_settings
    from app.core.security import create_session_token

    client.cookies.set(get_settings().session_cookie_name, create_session_token(diego.id))

    event = _pending(
        diego,
        "Diego weighs 80 kg",
        [{"intent_type": "log_body_metric", "user_key": "diego", "weight_kg": 80.0}],
        db,
    )
    r = client.post(f"/capture/confirm/{event.id}", headers={"HX-Request": "true"})
    assert r.status_code == 200, r.text[:500]
    assert _("Nothing was saved") in r.text
    assert list(db.scalars(select(BodyMetricLog)).all()) == []


def test_a_name_from_outside_the_household_is_not_saved_to_a_member(
    authenticated_client: TestClient, db: Session, diego: User
) -> None:
    """Un nombre que no es de nadie del hogar no se resuelve por descarte."""
    event = _pending(
        diego,
        "Martina ate a banana",
        [
            {
                "intent_type": "log_meal",
                "meal_type": "other",
                "items_per_user": {"martina": [{"food_name": "banana", "qty": 1}]},
            }
        ],
        db,
    )
    r = authenticated_client.post(f"/capture/confirm/{event.id}", headers={"HX-Request": "true"})
    assert r.status_code == 200, r.text[:500]
    assert _("Nothing was saved") in r.text
    assert list(db.scalars(select(MealParticipant)).all()) == []


def test_confirming_the_same_capture_twice_writes_once(
    authenticated_client: TestClient, db: Session, rocio: User
) -> None:
    """Un doble submit, o un "reenviar" del navegador, duplicaba la comida.

    ``confirm_event`` solo chequeaba dueño, no estado: el segundo POST volvía a
    ejecutar todos los intents. La segunda vez ahora contesta como para un id que no
    existe — y con `HX-Reswap`, que es lo que hace que HTMX muestre el 404.
    """
    r = authenticated_client.post("/capture/parse", data={"text": "Rocío ate a banana"})
    assert r.status_code == 200, r.text[:500]
    event = db.scalars(select(NLPIngestionEvent)).one()

    first = authenticated_client.post(
        f"/capture/confirm/{event.id}", headers={"HX-Request": "true"}
    )
    assert first.status_code == 200, first.text[:500]

    second = authenticated_client.post(
        f"/capture/confirm/{event.id}", headers={"HX-Request": "true"}
    )
    assert second.status_code == 404, second.text[:500]
    assert second.headers.get("HX-Reswap") == "outerHTML"
    assert _("That capture is no longer available.") in second.text

    assert len(list(db.scalars(select(MealParticipant)).all())) == 1


def test_discarding_an_already_confirmed_capture_does_not_claim_it_was_not_saved(
    authenticated_client: TestClient, db: Session, rocio: User
) -> None:
    """El guardia de estado estaba solo en confirm, y descartar no borra nada.

    Confirmar y después descartar — una segunda pestaña con el preview viejo, un
    "atrás" y otro toque — contestaba "Descartado. No se guardó nada." con la comida
    de Rocío ya escrita, y dejaba el `NLPIngestionEvent` en `discarded` sobre una
    escritura que ocurrió. Es la única constancia de quién dictó esa fila.
    """
    authenticated_client.post("/capture/parse", data={"text": "Rocío ate a banana"})
    event = db.scalars(select(NLPIngestionEvent)).one()
    first = authenticated_client.post(
        f"/capture/confirm/{event.id}", headers={"HX-Request": "true"}
    )
    assert first.status_code == 200, first.text[:500]

    late = authenticated_client.post(f"/capture/discard/{event.id}", headers={"HX-Request": "true"})
    assert late.status_code == 404, late.text[:500]
    assert _("Discarded") not in late.text
    assert _("That capture is no longer available.") in late.text

    db.refresh(event)
    assert event.status == "confirmed"
    assert [p.user_id for p in db.scalars(select(MealParticipant)).all()] == [rocio.id]


def test_nothing_recognisable_does_not_report_a_save(
    authenticated_client: TestClient, db: Session, diego: User
) -> None:
    """Un intent salteado también es un resultado, y se contaba como guardado.

    ``mixed`` mostraba un tilde verde y "1 cosa registrada" arriba de "nada que
    hacer con esto". `saved_count` cuenta filas escritas, no resultados.
    """
    event = _pending(diego, "uh, stuff happened", [{"intent_type": "mixed"}], db)
    r = authenticated_client.post(f"/capture/confirm/{event.id}", headers={"HX-Request": "true"})
    assert r.status_code == 200, r.text[:500]
    assert _("Nothing was saved") in r.text
    assert _("Saved, with some gaps") not in r.text
    assert _("nothing to do with this one.") in r.text


def test_preview_wires_both_actions_at_the_preview_itself(
    authenticated_client: TestClient,
) -> None:
    """Discard carried no ``hx-target``, so HTMX fell back to the button itself:
    the discard fragment rendered *inside* the button and the preview stayed put."""
    r = authenticated_client.post("/capture/parse", data={"text": "I ran for 30 minutes"})
    assert r.status_code == 200, r.text[:500]
    assert 'id="nlp-preview"' in r.text
    # Los dos botones postean y los dos reemplazan el preview. Sin asumir en qué
    # orden emite Jinja los atributos: lo que importa es que estén los dos pares.
    assert r.text.count('hx-target="#nlp-preview"') == 2
    assert r.text.count('hx-swap="outerHTML"') == 2
    assert 'hx-post="/capture/confirm/' in r.text
    assert 'hx-post="/capture/discard/' in r.text
    # The dead inline-edit state is gone.
    assert "editing" not in r.text


def test_preview_shows_a_date_correction_field_wired_into_confirm(
    authenticated_client: TestClient,
) -> None:
    """A meal/workout/body-metric intent gets a date input; Confirm must include it
    explicitly (`hx-include`) — there is no `<form>` wrapping these buttons."""
    r = authenticated_client.post("/capture/parse", data={"text": "I ate pasta"})
    assert r.status_code == 200, r.text[:500]
    assert 'id="nlp-override-date"' in r.text
    assert 'hx-include="#nlp-override-date"' in r.text


def test_add_stock_only_shows_no_date_field(authenticated_client: TestClient) -> None:
    """Stock has no timestamp to correct (`_execute_intent` never uses `now` for it)."""
    r = authenticated_client.post("/capture/parse", data={"text": "We bought 6 bananas"})
    assert r.status_code == 200, r.text[:500]
    assert 'id="nlp-override-date"' not in r.text


def test_override_date_from_the_confirmation_screen_wins(
    authenticated_client: TestClient, db: Session, diego: User
) -> None:
    event = _pending(
        diego,
        "I ate pasta yesterday",
        [
            {
                "intent_type": "log_meal",
                "meal_type": "dinner",
                "items_per_user": {"diego": [{"food_name": "pasta"}]},
                "participants": ["diego"],
                "time_reference": "yesterday",
                "confidence": 0.85,
            }
        ],
        db,
    )
    chosen_day = local_today() - timedelta(days=5)
    r = authenticated_client.post(
        f"/capture/confirm/{event.id}",
        data={"override_date": chosen_day.isoformat()},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200, r.text[:500]

    meal = db.scalars(select(MealEvent)).one()
    assert to_local(meal.timestamp).date() == chosen_day


def test_discarding_says_so_and_leaves_nothing_behind(
    authenticated_client: TestClient, db: Session
) -> None:
    """El positivo del test de abajo: descartar sí descarta, y lo dice."""
    r = authenticated_client.post("/capture/parse", data={"text": "I ran for 30 minutes"})
    assert r.status_code == 200, r.text[:500]
    event = db.scalars(select(NLPIngestionEvent)).one()

    r = authenticated_client.post(f"/capture/discard/{event.id}", headers={"HX-Request": "true"})
    assert r.status_code == 200, r.text[:500]
    assert _("Discarded") in r.text
    db.refresh(event)
    assert event.status == "discarded"


def test_cannot_confirm_or_discard_someone_elses_capture(
    authenticated_client: TestClient, db: Session, rocio: User
) -> None:
    """The id in the URL must not be enough, and a miss must not claim success.

    ``discard_event`` already returned ``False`` for a foreign event; the route threw
    that away and rendered "Discarded — nothing was saved" over something it never
    touched.
    """
    hers = _pending(
        rocio,
        "I ran for 30 minutes",
        [{"intent_type": "log_workout", "participants": ["rocio"]}],
        db,
    )

    for action in ("confirm", "discard"):
        r = authenticated_client.post(
            f"/capture/{action}/{hers.id}", headers={"HX-Request": "true"}
        )
        assert r.status_code == 404, (action, r.text[:500])
        assert _("Discarded") not in r.text, action
        assert _("That capture is no longer available.") in r.text, action
        # El 404 se muestra: sin este header HTMX lo descarta y la persona se queda
        # con el preview viejo y sus botones ya muertos.
        assert r.headers.get("HX-Reswap") == "outerHTML", action
    db.refresh(hers)
    assert hers.status == "pending_confirmation"
    #: Y el 404 tampoco escribió: sin esto el test pasa igual si el confirm ajeno
    #: ejecuta los intents y después contesta 404.
    assert list(db.scalars(select(WorkoutParticipant)).all()) == []
