from typing import Any, Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    def __init__(self, model: type[ModelT], db: Session) -> None:
        self.model = model
        self.db = db

    def get(self, id: int) -> ModelT | None:
        return self.db.get(self.model, id)

    def get_all(self, limit: int = 100, offset: int = 0) -> list[ModelT]:
        stmt = select(self.model).limit(limit).offset(offset)
        return list(self.db.scalars(stmt).all())

    def create(self, obj: ModelT) -> ModelT:
        self.db.add(obj)
        self.db.flush()
        self.db.refresh(obj)
        return obj

    def delete(self, obj: ModelT) -> None:
        self.db.delete(obj)
        self.db.flush()

    def save(self) -> None:
        self.db.commit()
