"""Test configuration and shared fixtures."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.csrf import CSRF_COOKIE_NAME
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.food import FoodItem
from app.models.household import Household
from app.models.user import User

# Use SQLite in-memory for tests
TEST_DB_URL = "sqlite:///:memory:"
engine = create_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False},
)


# Enable foreign keys in SQLite
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
    # `isolation_level = None` apaga las transacciones implícitas de pysqlite, que
    # emite el BEGIN por su cuenta y **después** del SAVEPOINT: sin esto, liberar el
    # savepoint más externo commitea de verdad y el rollback del fixture no revierte
    # nada. Con el BEGIN explícito de abajo, los savepoints funcionan — y de eso
    # depende que el `db` de más abajo pueda sobrevivir a un `db.rollback()` de la
    # ruta bajo prueba. Es la receta que documenta SQLAlchemy para pysqlite.
    dbapi_conn.isolation_level = None


@event.listens_for(engine, "begin")
def emit_explicit_begin(conn):
    conn.exec_driver_sql("BEGIN")


TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="session", autouse=True)
def create_tables():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db() -> Session:
    connection = engine.connect()
    transaction = connection.begin()
    # `join_transaction_mode`: con el default esta sesión queda en `rollback_only`, y
    # ahí un `db.rollback()` de la ruta bajo prueba — `app/web/profile.py` y
    # `app/web/health.py` lo hacen en sus caminos de error — se lleva puesta la
    # transacción externa y con ella las filas de los fixtures, así que el test se cae
    # con la conexión desasociada en vez de probar el camino de error. Con un savepoint,
    # ese rollback llega hasta el `db.commit()` del test y no más atrás.
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    yield session
    session.close()
    transaction.rollback()
    connection.close()


TEST_CSRF_TOKEN = "test-fixture-csrf-token"


@pytest.fixture
def client(db: Session) -> TestClient:
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=False) as c:
        # Every real page already renders the matching hidden `csrf_token` field
        # (see app/core/csrf.py) — mirror that here once instead of adding it to
        # every one of this suite's `.post()` calls. A test that wants to exercise
        # the CSRF check itself calls `TestClient.post(c, ...)` to bypass this.
        c.cookies.set(CSRF_COOKIE_NAME, TEST_CSRF_TOKEN)
        c.post = _with_csrf_token(c.post)  # type: ignore[method-assign]
        yield c
    app.dependency_overrides.clear()


def _with_csrf_token(post: Any) -> Any:
    def wrapped(url: str, *args: Any, **kwargs: Any) -> Any:
        if not str(url).startswith("/api/"):
            data = kwargs.get("data")
            if data is None:
                kwargs["data"] = {"csrf_token": TEST_CSRF_TOKEN}
            elif isinstance(data, dict) and "csrf_token" not in data:
                kwargs["data"] = {**data, "csrf_token": TEST_CSRF_TOKEN}
        return post(url, *args, **kwargs)

    return wrapped


@pytest.fixture
def household(db: Session) -> Household:
    h = Household(name="Test Home", timezone="UTC")
    db.add(h)
    db.flush()
    return h


@pytest.fixture
def diego(db: Session, household: Household) -> User:
    u = User(
        household_id=household.id,
        name="Diego",
        email="diego@test.com",
        password_hash=hash_password("diego123"),
        baseline_activity_level="moderate",
        avatar_color="#6366f1",
        impossible_activities_json=["swimming"],
        onboarding_completed=True,
    )
    db.add(u)
    db.flush()
    return u


@pytest.fixture
def rocio(db: Session, household: Household) -> User:
    u = User(
        household_id=household.id,
        name="Rocío",
        email="rocio@test.com",
        password_hash=hash_password("rocio123"),
        baseline_activity_level="light",
        avatar_color="#ec4899",
        onboarding_completed=True,
    )
    db.add(u)
    db.flush()
    return u


@pytest.fixture
def banana(db: Session) -> FoodItem:
    f = FoodItem(
        canonical_name="banana",
        category="fruit",
        base_unit="unit",
        calories_per_100g=89,
        protein_g=1.1,
        carbs_g=23.0,
        fat_g=0.3,
        perishable=True,
    )
    db.add(f)
    db.flush()
    return f


@pytest.fixture
def authenticated_client(client: TestClient, diego: User, db: Session) -> TestClient:
    """Return a test client with Diego's session cookie set."""
    from app.core.config import get_settings
    from app.core.security import create_session_token

    settings = get_settings()
    token = create_session_token(diego.id)
    client.cookies.set(settings.session_cookie_name, token)
    return client
