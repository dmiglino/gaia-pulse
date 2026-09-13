from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.household import Household
from app.repositories.base import BaseRepository


class HouseholdRepository(BaseRepository[Household]):
    """El hogar como fila.

    Existe porque los jobs de fondo necesitan recorrer los hogares y lo hacían con
    un `select(Household)` propio: `app/jobs/` armando consultas sobre un modelo es
    justo lo que la regla de capas de `AGENTS.md` no permite — solo los repositorios
    tocan modelos. Es un repositorio chico a propósito; el resto de la app llega al
    hogar por la persona autenticada, no por listado.
    """

    def __init__(self, db: Session) -> None:
        super().__init__(Household, db)

    def list_all(self) -> list[Household]:
        """Todos los hogares, en un orden estable. Es la que tienen que usar los jobs.

        Convive con la `get_all()` heredada, que no ordena y corta en 100: para una
        pasada de fondo sobre la tabla entera eso es un `LIMIT` invisible y un orden
        que decide el motor, así que las dos no son intercambiables.
        """
        #: `ORDER BY` explícito (regla 5): los jobs recorren esta lista y algunos
        #: tienen un tope de avisos por corrida, así que el orden decide a quién le
        #: toca hoy. Sin él lo decide el motor, y puede cambiar entre corridas.
        return list(self.db.scalars(select(Household).order_by(Household.id)).all())
