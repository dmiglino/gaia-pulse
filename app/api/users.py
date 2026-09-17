from fastapi import APIRouter

from app.core.dependencies import DB, CurrentUser
from app.repositories.user_repo import UserRepository
from app.schemas.user import UserRead, UserUpdate

router = APIRouter()


@router.get("/me", response_model=UserRead)
def get_me(current_user: CurrentUser) -> UserRead:
    return UserRead.model_validate(current_user)


@router.patch("/me", response_model=UserRead)
def update_me(data: UserUpdate, current_user: CurrentUser, db: DB) -> UserRead:
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(current_user, field, value)
    db.flush()
    db.commit()
    db.refresh(current_user)
    return UserRead.model_validate(current_user)


@router.get("/household", response_model=list[UserRead])
def get_household_users(current_user: CurrentUser, db: DB) -> list[UserRead]:
    repo = UserRepository(db)
    users = repo.get_household_users(current_user.household_id)
    return [UserRead.model_validate(u) for u in users]
