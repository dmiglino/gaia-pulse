from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.pantry import PantryMovement, PantryStock
from app.recommendations import learning
from app.repositories.food_repo import FoodRepository
from app.repositories.pantry_repo import PantryMovementRepository, PantryStockRepository
from app.schemas.pantry import PurchaseRequest, StockAdjustRequest

#: Una compra vale la mitad que una comida como evidencia de gusto. Ver
#: `PantryService.process_purchase`.
_PURCHASE_SIGNAL_VALUE = 0.5


class PantryService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.stock_repo = PantryStockRepository(db)
        self.movement_repo = PantryMovementRepository(db)
        self.food_repo = FoodRepository(db)

    def get_stock(
        self,
        household_id: int,
        search: str | None = None,
        category: str | None = None,
        low_only: bool = False,
    ) -> list:
        """Household stock, optionally narrowed by name, category or low-stock state.

        `low_only` se filtra acá y no en el repositorio porque `is_low` es una
        propiedad de Python (compara la cantidad contra `low_stock_threshold` y
        contra cero), no una columna: no hay forma de expresarla en el `WHERE`.
        """
        items = self.stock_repo.get_household_stock(household_id, search=search, category=category)
        if low_only:
            return [item for item in items if item.is_low]
        return items

    def get_low_stock(self, household_id: int) -> list:
        return self.stock_repo.get_low_stock(household_id)

    def get_stock_summary(self, household_id: int) -> dict[str, int]:
        """How many items the household tracks, and how many are running low.

        Una sola consulta para los dos números: la pantalla los muestra juntos y
        pedirlos por separado recorría la despensa completa dos veces.
        """
        items = self.stock_repo.get_household_stock(household_id)
        return {"total": len(items), "low": sum(1 for item in items if item.is_low)}

    def process_purchase(
        self, household_id: int, user_id: int, request: PurchaseRequest
    ) -> list[PantryMovement]:
        """Register a purchase: creates movements and updates stock for each item."""
        movements = []
        now = datetime.now(timezone.utc)
        for item in request.items:
            food = self.food_repo.get_or_create(item.food_name)
            self.stock_repo.upsert_stock(
                household_id=household_id,
                food_item_id=food.id,
                delta=item.quantity,
                unit=item.unit,
            )
            movement = self._make_movement(
                household_id=household_id,
                user_id=user_id,
                food_item_id=food.id,
                movement_type="purchase",
                quantity=item.quantity,
                unit=item.unit,
                timestamp=now,
                notes=request.notes,
            )
            movements.append(movement)
            #: Para que la señal pueda apuntar al movimiento: `_make_movement` solo hace
            #: `add`, así que sin esto `movement.id` es `None` y la fila queda sin rastro
            #: de dónde salió — que es lo que la 4.4.8 necesita para poder explicarla y
            #: para poder olvidarla.
            self.db.flush()

            #: `repeated_purchase` era el tipo de señal que el scorer leía y **nadie
            #: escribía nunca**: el síntoma de que el vocabulario estaba repartido entre
            #: lector y escritores. Acá se cierra el circuito, y con esto una compra
            #: repetida empuja los candidatos de comida y de despensa que la nombran.
            #:
            #: Pesa la mitad que una comida a propósito: comprar algo dice menos que
            #: comerlo —se compra para otro, se compra y se tira—, y además esto se
            #: atribuye a quien registró la compra, que en una casa de dos es quien fue al
            #: súper y no necesariamente quien lo come. Es una pista, no una preferencia.
            learning.record_signal(
                self.db,
                user_id=user_id,
                signal_type="repeated_purchase",
                subject_type="food",
                subject_name=item.food_name,
                value=_PURCHASE_SIGNAL_VALUE,
                source_type="implicit",
                source_entity_type="pantry_movement",
                source_entity_id=movement.id,
            )

        self.db.flush()
        self.db.commit()
        return movements

    def adjust_stock(
        self, household_id: int, user_id: int, request: StockAdjustRequest
    ) -> PantryMovement:
        """Apply a stock adjustment (consumption, manual correction, discard)."""
        food = self.food_repo.get_or_create(request.food_name)
        sign = -1.0 if request.movement_type in ("consumption", "discard") else 1.0
        applied = self._apply_delta(
            household_id=household_id,
            food_item_id=food.id,
            delta=sign * request.quantity,
            unit=request.unit,
        )
        movement = self._make_movement(
            household_id=household_id,
            user_id=user_id,
            food_item_id=food.id,
            movement_type=request.movement_type,
            quantity=abs(applied),
            unit=request.unit,
            notes=request.notes,
        )
        self.db.flush()
        self.db.commit()
        return movement

    def adjust_stock_by_id(
        self,
        household_id: int,
        user_id: int,
        stock_id: int,
        quantity: float,
        movement_type: str,
        notes: str | None = None,
    ) -> PantryStock | None:
        """Adjust an existing stock row identified by its id.

        Unlike :meth:`adjust_stock`, ``adjustment`` means *set the quantity to
        this absolute value* (which is what the pantry grid's "Set" option
        offers); ``purchase`` adds, ``consumption``/``discard`` subtract. The
        recorded movement always carries the magnitude of the actual change.

        Returns ``None`` when the row does not belong to *household_id*.
        """
        stock = self.stock_repo.get_for_household(household_id, stock_id)
        if stock is None:
            return None

        if movement_type == "adjustment":
            delta = quantity - float(stock.current_quantity)
        elif movement_type in ("consumption", "discard"):
            delta = -quantity
        else:
            delta = quantity

        applied = self._apply_delta(
            household_id=household_id,
            food_item_id=stock.food_item_id,
            delta=delta,
            unit=stock.unit,
        )
        self._make_movement(
            household_id=household_id,
            user_id=user_id,
            food_item_id=stock.food_item_id,
            movement_type=movement_type,
            quantity=abs(applied),
            unit=stock.unit,
            notes=notes,
        )
        self.db.flush()
        self.db.commit()
        return stock

    def record_consumption(
        self,
        household_id: int,
        user_id: int,
        food_name: str,
        quantity: float,
        unit: str,
        related_entity_type: str | None = None,
        related_entity_id: int | None = None,
    ) -> PantryMovement | None:
        """Record consumption of a pantry item (typically triggered from meal logging)."""
        food = self.food_repo.find_by_name(food_name)
        if not food:
            return None
        if not self.stock_repo.get_by_food_item(household_id, food.id):
            return None

        applied = self._apply_delta(
            household_id=household_id,
            food_item_id=food.id,
            delta=-quantity,
            unit=unit,
        )
        movement = self._make_movement(
            household_id=household_id,
            user_id=user_id,
            food_item_id=food.id,
            movement_type="consumption",
            quantity=abs(applied),
            unit=unit,
            related_entity_type=related_entity_type,
            related_entity_id=related_entity_id,
        )
        self.db.flush()
        return movement

    def get_movements(
        self, household_id: int, limit: int = 50, offset: int = 0
    ) -> list:
        return self.movement_repo.get_household_movements(household_id, limit=limit, offset=offset)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _apply_delta(
        self, household_id: int, food_item_id: int, delta: float, unit: str
    ) -> float:
        """Apply *delta* to the stock row and return the change that actually landed.

        The repository clamps stock at zero, so asking to consume 5 of an item
        that has 2 only moves 2. The ledger has to record what moved and not what
        was asked for: otherwise `/pantry/movements` claims a consumption of 5
        units of something that never held more than 2, and stock stops being
        reconcilable from its own movements.
        """
        existing = self.stock_repo.get_by_food_item(household_id, food_item_id)
        before = float(existing.current_quantity) if existing else 0.0
        stock = self.stock_repo.upsert_stock(
            household_id=household_id,
            food_item_id=food_item_id,
            delta=delta,
            unit=unit,
        )
        return float(stock.current_quantity) - before

    def _make_movement(
        self,
        household_id: int,
        user_id: int,
        food_item_id: int,
        movement_type: str,
        quantity: float,
        unit: str,
        timestamp: datetime | None = None,
        notes: str | None = None,
        related_entity_type: str | None = None,
        related_entity_id: int | None = None,
    ) -> PantryMovement:
        movement = PantryMovement(
            household_id=household_id,
            user_id=user_id,
            food_item_id=food_item_id,
            movement_type=movement_type,
            quantity=quantity,
            unit=unit,
            timestamp=timestamp or datetime.now(timezone.utc),
            notes=notes,
            related_entity_type=related_entity_type,
            related_entity_id=related_entity_id,
        )
        self.db.add(movement)
        return movement
