from fastapi import APIRouter, Query

from app.core.dependencies import DB, CurrentUser
from app.schemas.pantry import (
    PantryMovementRead,
    PantryStockRead,
    PurchaseRequest,
    StockAdjustRequest,
)
from app.services.pantry_service import PantryService

router = APIRouter()


@router.get("/stock", response_model=list[PantryStockRead])
def get_stock(
    current_user: CurrentUser,
    db: DB,
    search: str | None = Query(None),
    category: str | None = Query(None),
) -> list[PantryStockRead]:
    svc = PantryService(db)
    stock = svc.get_stock(current_user.household_id, search=search, category=category)
    return [PantryStockRead.model_validate(s) for s in stock]


@router.get("/stock/low", response_model=list[PantryStockRead])
def get_low_stock(current_user: CurrentUser, db: DB) -> list[PantryStockRead]:
    svc = PantryService(db)
    return [PantryStockRead.model_validate(s) for s in svc.get_low_stock(current_user.household_id)]


@router.post("/purchase")
def register_purchase(
    data: PurchaseRequest, current_user: CurrentUser, db: DB
) -> dict:
    svc = PantryService(db)
    movements = svc.process_purchase(current_user.household_id, current_user.id, data)
    return {"registered": len(movements), "items": [m.food_item.canonical_name for m in movements]}


@router.post("/adjust")
def adjust_stock(
    data: StockAdjustRequest, current_user: CurrentUser, db: DB
) -> dict:
    svc = PantryService(db)
    svc.adjust_stock(current_user.household_id, current_user.id, data)
    return {"status": "ok"}


@router.get("/movements", response_model=list[PantryMovementRead])
def get_movements(
    current_user: CurrentUser,
    db: DB,
    limit: int = Query(50, le=200),
    offset: int = Query(0),
) -> list[PantryMovementRead]:
    svc = PantryService(db)
    movements = svc.get_movements(current_user.household_id, limit=limit, offset=offset)
    return [PantryMovementRead.model_validate(m) for m in movements]
