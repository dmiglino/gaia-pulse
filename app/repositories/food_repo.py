from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.food import FoodItem
from app.repositories.base import BaseRepository


class FoodRepository(BaseRepository[FoodItem]):
    def __init__(self, db: Session) -> None:
        super().__init__(FoodItem, db)

    def find_by_name(self, name: str) -> FoodItem | None:
        """Find food item by canonical name or alias (case-insensitive)."""
        name_lower = name.strip().lower()
        stmt = select(FoodItem).where(
            func.lower(FoodItem.canonical_name) == name_lower
        )
        result = self.db.scalar(stmt)
        if result:
            return result
        # Check aliases (JSONB contains)
        stmt2 = select(FoodItem).where(
            FoodItem.aliases_json.contains([name_lower])
        )
        return self.db.scalar(stmt2)

    def name_categories(self) -> list[tuple[str, str, list[str]]]:
        """Cada alimento **categorizado** del catálogo: nombre canónico, categoría y alias.

        Devuelve columnas y no filas `FoodItem` porque el único consumidor es el índice de
        atributos del aprendizaje (`learning.attribute_index`), que necesita esos tres
        campos de todo el catálogo y ninguna relación. Sin `limit`: `get_all` de la base
        recorta en 100 en silencio, y `get_or_create` agrega una fila por cada alimento de
        texto libre que aparece en una compra, así que un catálogo de más de 100 dejaría de
        aprender categorías sin que nada avise. Las filas sin categoría —justamente las que
        crea `get_or_create`— no entran: de esas no sabemos el atributo, y un `None` en el
        índice sería un sujeto llamado "none".
        """
        stmt = select(FoodItem.canonical_name, FoodItem.category, FoodItem.aliases_json).where(
            FoodItem.category.isnot(None)
        )
        return [
            (name, category, list(aliases or []))
            for name, category, aliases in self.db.execute(stmt)
            if category
        ]

    def search(self, query: str, limit: int = 20) -> list[FoodItem]:
        stmt = select(FoodItem).where(
            func.lower(FoodItem.canonical_name).contains(query.lower())
        ).limit(limit)
        return list(self.db.scalars(stmt).all())

    def get_or_create(self, name: str, category: str | None = None) -> FoodItem:
        """Find existing food item or create a new one from the name."""
        existing = self.find_by_name(name)
        if existing:
            return existing
        item = FoodItem(canonical_name=name.strip().lower(), category=category)
        return self.create(item)
