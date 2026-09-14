"""Page tests for /profile.

`profile_update` armaba una lista `errors` y la tiraba, y las dos listas de
preferencias solo se podían agrandar. Los tres casos de acá son eso: que un valor
inválido se avise, que una lista se pueda vaciar, y que el nivel de actividad se
lea en el idioma de la app en vez de salir crudo de la base.
"""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.i18n import _
from app.models.user import User


def test_an_out_of_range_height_says_so_and_saves_nothing(
    authenticated_client: TestClient, db: Session, diego: User
) -> None:
    """La altura fuera de rango se descartaba en silencio, con el resto del formulario.

    `errors` se llenaba y nunca llegaba a la pantalla: la persona veía "guardado" y
    su altura sin cambiar. Y es todo o nada — `get_db` cierra la sesión sin
    commitear —, así que el nombre válido del mismo envío tampoco se guarda: eso es
    justamente lo que el mensaje tiene que decir.
    """
    #: La ruta hace `db.rollback()`, y en los tests la sesión de la ruta **es** la del
    #: fixture: sin este commit el rollback se lleva puestas también las filas que
    #: armaron el hogar, porque el savepoint de la sesión es anterior a ellas. El
    #: commit las baja al `connection.begin()` externo, que el teardown descarta igual.
    db.commit()

    resp = authenticated_client.post(
        "/profile/update",
        data={
            "name": "Diego Nuevo",
            "height_cm": "999",
            "baseline_activity_level": "moderate",
            "dietary_restrictions": "",
            "impossible_activities": "swimming",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text[:500]

    landing = authenticated_client.get(resp.headers["location"], follow_redirects=False)
    assert landing.status_code == 200, resp.headers["location"]
    assert _("Height must be between 50 and 280 cm.") in landing.text
    assert _("Nothing was saved.") in landing.text
    assert _("Profile saved.") not in landing.text
    #: Y la pantalla vuelve con el nombre viejo, que es la otra mitad del "todo o nada".
    assert "Diego Nuevo" not in landing.text

    db.refresh(diego)
    assert diego.name == "Diego"
    assert diego.height_cm is None


def test_an_out_of_range_protein_goal_says_so_and_saves_nothing(
    authenticated_client: TestClient, db: Session, diego: User
) -> None:
    db.commit()

    resp = authenticated_client.post(
        "/profile/update",
        data={
            "name": "Diego",
            "baseline_activity_level": "moderate",
            "goal_protein_g": "9999",
            "dietary_restrictions": "",
            "impossible_activities": "",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text[:500]

    landing = authenticated_client.get(resp.headers["location"], follow_redirects=False)
    assert landing.status_code == 200
    assert _("Protein goal must be between 10 and 400 g.") in landing.text
    assert _("Nothing was saved.") in landing.text

    db.refresh(diego)
    assert diego.goal_protein_g is None


def test_declaring_and_then_clearing_a_nutrition_goal(
    authenticated_client: TestClient, db: Session, diego: User
) -> None:
    """A diferencia de la altura o el peso objetivo, vaciar la casilla sí borra el valor:
    la tarjeta de macros decide contra qué comparar según si esto es `None`."""
    resp = authenticated_client.post(
        "/profile/update",
        data={
            "name": "Diego",
            "baseline_activity_level": "moderate",
            "goal_protein_g": "120",
            "goal_fiber_g": "30",
            "goal_calories_kcal": "2200",
            "dietary_restrictions": "",
            "impossible_activities": "",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text[:500]
    db.refresh(diego)
    assert float(diego.goal_protein_g) == 120.0
    assert float(diego.goal_fiber_g) == 30.0
    assert diego.goal_calories_kcal == 2200

    resp = authenticated_client.post(
        "/profile/update",
        data={
            "name": "Diego",
            "baseline_activity_level": "moderate",
            "dietary_restrictions": "",
            "impossible_activities": "",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text[:500]
    db.refresh(diego)
    assert diego.goal_protein_g is None
    assert diego.goal_fiber_g is None
    assert diego.goal_calories_kcal is None


def test_emptying_the_dietary_restrictions_box_actually_clears_them(
    authenticated_client: TestClient, db: Session, diego: User
) -> None:
    """Una restricción alimentaria no se podía sacar nunca desde la interfaz.

    La asignación estaba detrás de `if dietary_restrictions:`, así que mandar la
    casilla vacía dejaba la lista anterior intacta. Importa más que un campo de
    texto cualquiera: la restricción es un filtro duro sobre lo que el motor puede
    sugerir de comer, y `impossible_activities` lo mismo con las actividades.
    """
    diego.dietary_restrictions_json = ["gluten"]
    db.flush()

    resp = authenticated_client.post(
        "/profile/update",
        data={
            "name": "Diego",
            "baseline_activity_level": "moderate",
            "dietary_restrictions": "",
            "impossible_activities": "",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text[:500]

    db.refresh(diego)
    assert diego.dietary_restrictions_json == []
    assert diego.impossible_activities_json == []

    landing = authenticated_client.get(resp.headers["location"], follow_redirects=False)
    assert landing.status_code == 200
    assert _("Profile saved.") in landing.text


def test_the_activity_level_is_readable_in_both_places_it_appears(
    authenticated_client: TestClient, db: Session, diego: User, rocio: User
) -> None:
    """La misma columna se imprimía de dos formas distintas en la misma pantalla.

    `very_active` salía como "Very Active" en el select (`|replace|title`, que
    `pybabel` no puede extraer) y crudo en la lista del hogar. Ahora las dos salen
    del mismo macro de `dm`, traducido.
    """
    diego.baseline_activity_level = "very_active"
    db.flush()

    r = authenticated_client.get("/profile/")
    assert r.status_code == 200, r.text[:500]

    # El rótulo traducido, en el select y en la fila del hogar.
    assert r.text.count(_("Very active")) >= 2, r.text.count(_("Very active"))
    # Y la glosa del nivel elegido, que es lo que explica contra qué se compara.
    assert _("Daily training or physical job") in r.text
    # Las dos formas viejas ya no están. `value="very_active"` sí: es el valor que
    # viaja en el POST, no un rótulo.
    assert "Very Active" not in r.text
    assert ">very_active" not in r.text
    assert _("Light") in r.text, "el nivel de Rocío, en la lista del hogar"
