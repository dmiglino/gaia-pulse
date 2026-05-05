from fastapi import APIRouter, HTTPException, Query

from app.core.dependencies import DB, CurrentUser
from app.schemas.workout import WorkoutSessionCreate, WorkoutSessionRead
from app.services.workout_service import WorkoutService

router = APIRouter()


@router.get("/", response_model=list[WorkoutSessionRead])
def get_workouts(
    current_user: CurrentUser,
    db: DB,
    limit: int = Query(20, le=100),
    offset: int = Query(0),
    user_id: int | None = Query(None),
) -> list[WorkoutSessionRead]:
    svc = WorkoutService(db)
    sessions = svc.get_sessions(
        current_user.household_id, limit=limit, offset=offset, user_id=user_id
    )
    return [WorkoutSessionRead.model_validate(s) for s in sessions]


@router.post("/", response_model=WorkoutSessionRead, status_code=201)
def log_workout(data: WorkoutSessionCreate, current_user: CurrentUser, db: DB) -> WorkoutSessionRead:
    svc = WorkoutService(db)
    session = svc.log_workout(current_user.household_id, data)
    return WorkoutSessionRead.model_validate(svc.get_session(session.id))


@router.get("/{session_id}", response_model=WorkoutSessionRead)
def get_workout(session_id: int, current_user: CurrentUser, db: DB) -> WorkoutSessionRead:
    svc = WorkoutService(db)
    session = svc.get_session(session_id)
    if not session or session.household_id != current_user.household_id:
        raise HTTPException(status_code=404, detail="Workout not found")
    return WorkoutSessionRead.model_validate(session)


@router.delete("/{session_id}", status_code=204)
def delete_workout(session_id: int, current_user: CurrentUser, db: DB) -> None:
    svc = WorkoutService(db)
    session = svc.get_session(session_id)
    if not session or session.household_id != current_user.household_id:
        raise HTTPException(status_code=404, detail="Workout not found")
    svc.delete_session(session_id)
