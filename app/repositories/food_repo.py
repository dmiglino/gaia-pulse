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

    def known_names(self) -> dict[str, str]:
        """Todo nombre de alimento que el catálogo conoce → el canónico al que pertenece.

        Es el vocabulario contra el que se busca un alimento **dentro de una frase** —el
        motivo de texto libre de un rechazo, en `learning.subjects_in_text`—, y por eso, a
        diferencia de `name_categories`, acá los alimentos sin categoría **sí** entran:
        aquel índice necesita la categoría para poder generalizar y sin ella la entrada no
        significa nada, pero para reconocer "brócoli" en *"no me gusta el brócoli"* la
        categoría no hace falta. Y justamente los que no la tienen son los que creó
        `get_or_create` a partir de lo que la casa escribió en una captura: dejarlos afuera
        haría que la app no reconociera los nombres que la propia persona usa.

        **Un mapa y no una lista, y el motivo es todo el punto.** El catálogo se escribe con
        el canónico en inglés y el castellano como alias —`["tomato", "tomate"]`, ver
        `FoodItem.aliases_json`—, y los candidatos declaran su sujeto con
        `food.canonical_name`. Con una lista plana, *"no nos gusta la palta"* grababa una
        señal sobre `palta` que jamás iba a encontrarse con un candidato llamado `avocado`:
        se aprendía y no se leía. O sea, el caso típico de una casa que escribe en
        castellano, no un borde.

        Devuelve las claves sin normalizar y sin orden garantizado: quien la consume
        normaliza y ordena por longitud, así que hacerlo acá sería trabajo tirado. Un nombre
        que es el canónico de un alimento **y** el alias de otro se resuelve a sí mismo:
        los canónicos se escriben después, así que ganan.
        """
        stmt = select(FoodItem.canonical_name, FoodItem.aliases_json)
        rows = list(self.db.execute(stmt))
        canonical_by_name: dict[str, str] = {}
        for canonical_name, aliases in rows:
            if not canonical_name:
                continue
            for alias in aliases or []:
                if alias:
                    canonical_by_name[alias] = canonical_name
        for canonical_name, _aliases in rows:
            if canonical_name:
                canonical_by_name[canonical_name] = canonical_name
        return canonical_by_name

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
