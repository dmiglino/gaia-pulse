from fastapi import APIRouter, HTTPException, Query

from app.core.dependencies import DB, CurrentUser
from app.schemas.meal import MealEventCreate, MealEventRead
from app.services.meal_service import MealService

router = APIRouter()


@router.get("/", response_model=list[MealEventRead])
def get_meals(
    current_user: CurrentUser,
    db: DB,
    limit: int = Query(20, le=100),
    offset: int = Query(0),
    user_id: int | None = Query(None),
) -> list[MealEventRead]:
    svc = MealService(db)
    meals = svc.get_meals(current_user.household_id, limit=limit, offset=offset, user_id=user_id)
    return [MealEventRead.model_validate(m) for m in meals]


@router.post("/", response_model=MealEventRead, status_code=201)
def log_meal(data: MealEventCreate, current_user: CurrentUser, db: DB) -> MealEventRead:
    svc = MealService(db)
    event = svc.log_meal(current_user.household_id, data)
    return MealEventRead.model_validate(svc.get_meal(event.id))


@router.get("/{meal_id}", response_model=MealEventRead)
def get_meal(meal_id: int, current_user: CurrentUser, db: DB) -> MealEventRead:
    svc = MealService(db)
    meal = svc.get_meal(meal_id)
    if not meal or meal.household_id != current_user.household_id:
        raise HTTPException(status_code=404, detail="Meal not found")
    return MealEventRead.model_validate(meal)


@router.delete("/{meal_id}", status_code=204)
def delete_meal(meal_id: int, current_user: CurrentUser, db: DB) -> None:
    svc = MealService(db)
    meal = svc.get_meal(meal_id)
    if not meal or meal.household_id != current_user.household_id:
        raise HTTPException(status_code=404, detail="Meal not found")
    svc.delete_meal(meal_id)
