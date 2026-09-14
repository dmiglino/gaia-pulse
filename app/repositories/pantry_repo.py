from datetime import UTC, datetime

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, joinedload

from app.models.food import FoodItem
from app.models.pantry import PantryMovement, PantryStock
from app.repositories.base import BaseRepository


class PantryStockRepository(BaseRepository[PantryStock]):
    def __init__(self, db: Session) -> None:
        super().__init__(PantryStock, db)

    def get_household_stock(
        self, household_id: int, search: str | None = None, category: str | None = None
    ) -> list[PantryStock]:
        stmt = (
            select(PantryStock)
            .join(PantryStock.food_item)
            .where(PantryStock.household_id == household_id)
            .options(joinedload(PantryStock.food_item))
            .order_by(FoodItem.canonical_name)
        )
        if search:
            stmt = stmt.where(func.lower(FoodItem.canonical_name).contains(search.lower()))
        if category:
            stmt = stmt.where(FoodItem.category == category)
        return list(self.db.scalars(stmt).unique().all())

    def get_for_household(self, household_id: int, stock_id: int) -> PantryStock | None:
        """Return one stock row by id, scoped to the household that owns it."""
        stmt = (
            select(PantryStock)
            .where(
                and_(
                    PantryStock.id == stock_id,
                    PantryStock.household_id == household_id,
                )
            )
            .options(joinedload(PantryStock.food_item))
        )
        return self.db.scalar(stmt)

    def get_by_food_item(self, household_id: int, food_item_id: int) -> PantryStock | None:
        stmt = select(PantryStock).where(
            and_(
                PantryStock.household_id == household_id,
                PantryStock.food_item_id == food_item_id,
            )
        )
        return self.db.scalar(stmt)

    def get_in_stock(self, household_id: int) -> list[PantryStock]:
        """Lo que la casa **tiene** ahora mismo: cantidad mayor que cero.

        `get_household_stock` trae también las filas en cero —son las que la pantalla de
        despensa necesita mostrar para poder reponerlas—, así que no sirve para "¿con qué se
        puede cocinar hoy?". El generador de comidas tenía esta consulta escrita a mano.

        Con `joinedload` del alimento porque el uso siempre le mira el nombre y la categoría:
        sin eso es una consulta por ítem de la despensa.
        """
        stmt = (
            select(PantryStock)
            .join(PantryStock.food_item)
            .where(
                and_(
                    PantryStock.household_id == household_id,
                    PantryStock.current_quantity > 0,
                )
            )
            .options(joinedload(PantryStock.food_item))
            .order_by(FoodItem.canonical_name)
        )
        return list(self.db.scalars(stmt).unique().all())

    def get_low_stock(self, household_id: int) -> list[PantryStock]:
        """Return items at or below their low-stock threshold, or at zero."""
        stmt = (
            select(PantryStock)
            .join(PantryStock.food_item)
            .where(PantryStock.household_id == household_id)
            .options(joinedload(PantryStock.food_item))
        )
        all_stock = list(self.db.scalars(stmt).unique().all())
        return [s for s in all_stock if s.is_low]

    def set_threshold(
        self, household_id: int, stock_id: int, threshold: float | None
    ) -> PantryStock | None:
        """Set (or clear, with ``None``) the low-stock threshold of one row.

        Scoped the same way :meth:`get_for_household` is: returns ``None``
        when *stock_id* does not belong to *household_id*, so the caller
        can't be tricked into editing another household's row by guessing an id.
        """
        stock = self.get_for_household(household_id, stock_id)
        if stock is None:
            return None
        stock.low_stock_threshold = threshold
        stock.updated_at = datetime.now(UTC)
        self.db.flush()
        return stock

    def upsert_stock(
        self,
        household_id: int,
        food_item_id: int,
        delta: float,
        unit: str,
    ) -> PantryStock:
        """Atomically update stock quantity by delta (positive=add, negative=consume)."""
        stock = self.get_by_food_item(household_id, food_item_id)
        if stock is None:
            stock = PantryStock(
                household_id=household_id,
                food_item_id=food_item_id,
                current_quantity=max(0.0, delta),
                unit=unit,
                updated_at=datetime.now(UTC),
            )
            self.db.add(stock)
        else:
            new_qty = float(stock.current_quantity) + delta
            stock.current_quantity = max(0.0, new_qty)
            stock.updated_at = datetime.now(UTC)
        self.db.flush()
        return stock


class PantryMovementRepository(BaseRepository[PantryMovement]):
    def __init__(self, db: Session) -> None:
        super().__init__(PantryMovement, db)

    def get_household_movements(
        self,
        household_id: int,
        limit: int = 50,
        offset: int = 0,
        movement_type: str | None = None,
    ) -> list[PantryMovement]:
        stmt = (
            select(PantryMovement)
            .where(PantryMovement.household_id == household_id)
            .options(joinedload(PantryMovement.food_item))
            .order_by(PantryMovement.timestamp.desc())
            .limit(limit)
            .offset(offset)
        )
        if movement_type:
            stmt = stmt.where(PantryMovement.movement_type == movement_type)
        return list(self.db.scalars(stmt).unique().all())

    def get_purchases_since(self, household_id: int, since: datetime) -> list[PantryMovement]:
        """Las compras de la casa desde *since*, con el alimento ya cargado.

        `get_household_movements` no sirve para esto: pagina (`limit=50`) y ordena de lo más
        nuevo a lo más viejo, y quien la usa acá cuenta compras dentro de una ventana — un
        `LIMIT` invisible convertiría "cuántas veces se compró café" en "cuántas de las
        últimas cincuenta fueron café". Sin tope, entonces, pero acotada por fecha.

        Con `joinedload` porque el consumidor cuenta por ítem y después **nombra** los más
        comprados: sin eso el nombre es una consulta por ítem. Ese nombre se resolvía con un
        `db.query(FoodItem)` escrito dentro de `pantry_generator`, que es justo lo que la
        regla de capas reserva a los repositorios.
        """
        stmt = (
            select(PantryMovement)
            .where(
                and_(
                    PantryMovement.household_id == household_id,
                    PantryMovement.movement_type == "purchase",
                    PantryMovement.timestamp >= since,
                )
            )
            .options(joinedload(PantryMovement.food_item))
            #: `ORDER BY` explícito (regla 5): el agrupado por día del generador de compras
            #: recorre esta lista, y sin orden lo decide el motor.
            .order_by(PantryMovement.timestamp)
        )
        return list(self.db.scalars(stmt).unique().all())
