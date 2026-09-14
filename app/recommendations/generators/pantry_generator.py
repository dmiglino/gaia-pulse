"""Qué le falta a la casa: agotados, bajos, regulares que no están y pares de compra.

Es el único generador cuyo sujeto es el hogar y no una persona, y el único que hasta la
4.5.7 **no llegaba a nadie**: `engine.generate_for_household` existía sin llamadores, así
que las cuatro tarjetas de acá se generaban solo en los tests. Desde la 4.5.7 las produce
`suggestion_jobs.run_suggestion_generation`, una vez por hogar y no una vez por persona.

Dos reglas que este archivo aprendió tarde y conviene no volver a romper:

- **Las cuatro razones citan lo que midieron.** Antes eran cuatro frases de catálogo
  ("Items at zero stock may block meal preparation.") idénticas para toda tarjeta de esa
  regla: la app decía por qué y no era la razón. Es la misma regla de la 4.5.4 —
  `tests/test_explain.py::TestNoFixedRationalesLeft` recorre el AST de este archivo desde
  la 4.5.7 y falla ante una constante—.
- **Las consultas viven en los repositorios.** Había cinco `db.query(...)` acá, tres de
  ellas para resolver el nombre de un alimento de a uno. Ahora el stock llega con su
  alimento cargado (`get_household_stock`) y los nombres de las compras salen de las
  propias compras (`get_purchases_since`), que es la única forma de nombrar un ítem sin
  volver a la base por cada uno.

Lo que estas tarjetas **no** son: el aviso por ítem. Ese es la notificación `low_stock` de
la 4.3, que se retira cuando el ítem se repone. Estas nombran hasta cinco cosas juntas y,
con el dedup por sujeto de la 4.4, mientras haya una pendiente no se genera otra.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.pantry import PantryMovement, PantryStock
from app.repositories.pantry_repo import PantryMovementRepository, PantryStockRepository

logger = logging.getLogger(__name__)

_PURCHASE_HISTORY_DAYS = 60
_MAX_SUGGESTIONS = 6

#: Cuántos nombres entran en el texto de una tarjeta de lista. Cinco, y el mismo número en
#: las dos: es lo que se lee de un vistazo en una tarjeta, no una propiedad de la despensa.
#: Escrito una vez porque el día que sean cuatro tienen que ser cuatro en las dos.
_NAMES_IN_TEXT = 5


def _food_name(stock: PantryStock) -> str:
    if stock.food_item:
        return stock.food_item.canonical_name
    return f"item #{stock.food_item_id}"


def _amount(value: Decimal | float | None) -> str:
    """Una cantidad como la escribiría una persona: `1` y no `1.000`, `0.5` y no `0.50`.

    Las columnas de despensa son `Numeric`, así que sin esto la razón de la tarjeta de
    bajos decía "down to 1.000 unit against a threshold of 3.000".
    """
    return f"{float(value or 0):g}"


def _headroom(stock: PantryStock) -> float:
    """Cuánto le queda por encima de su propio umbral. Negativo o cero: ya lo pasó."""
    return float(stock.current_quantity) - float(stock.low_stock_threshold or 0)


def _names_by_food_id(purchases: list[PantryMovement]) -> dict[int, str]:
    """El nombre de cada alimento que aparece en estas compras.

    Sale de la relación que el repositorio ya trajo cargada, y no de una consulta por ítem:
    todo `food_item_id` que se cuenta más abajo vino de una de estas filas, así que el
    nombre está siempre a mano. Una fila sin alimento queda afuera del mapa y, con eso,
    afuera de las tarjetas que nombran cosas.
    """
    return {
        movement.food_item_id: movement.food_item.canonical_name
        for movement in purchases
        if movement.food_item is not None and movement.food_item.canonical_name
    }


def generate(
    db: Session,
    household_id: int,
    limit: int = _MAX_SUGGESTIONS,
) -> list[dict[str, Any]]:
    """Generate pantry/shopping suggestions for a household.

    Returns a list of suggestion dicts.
    """
    suggestions: list[dict[str, Any]] = []

    #: Todo el stock, incluidas las filas en cero: son justamente las de la primera
    #: tarjeta. `get_low_stock` no alcanzaría —la segunda tarjeta necesita distinguir
    #: "en cero" de "bajo pero hay"— y `get_in_stock` deja afuera lo que se acabó.
    all_stock = PantryStockRepository(db).get_household_stock(household_id)
    since = datetime.now(tz=UTC) - timedelta(days=_PURCHASE_HISTORY_DAYS)
    purchases = PantryMovementRepository(db).get_purchases_since(household_id, since)
    names_by_id = _names_by_food_id(purchases)

    # ── 1. Out-of-stock items ──────────────────────────────────────────────
    out_of_stock = [s for s in all_stock if float(s.current_quantity) == 0]
    if out_of_stock:
        names = [_food_name(s) for s in out_of_stock[:_NAMES_IN_TEXT]]
        suggestions.append(
            {
                "category": "shopping",
                #: Un hábito y no un alimento, aunque la tarjeta nombre cinco: lo que la
                #: persona acepta o rechaza acá es el aviso de reposición, no la leche.
                #: Anclarlo al primer ítem de la lista habría enseñado sobre la leche cada
                #: vez que la lista arrancara con ella.
                #:
                #: El costo, anotado: con el dedup por sujeto de la 4.4, mientras haya una
                #: de estas pendiente no se genera otra, así que si además se acaba algo
                #: nuevo la lista pendiente queda vieja. Se acepta porque el aviso por ítem
                #: —el que sí se retira cuando el ítem se repone— es la notificación de
                #: `low_stock` de la 4.3, no esta tarjeta.
                "subject_type": "habit",
                "subject_name": "out of stock alert",
                "title": "Items are out of stock",
                "text": (
                    f"The following items have run out: {', '.join(names)}. "
                    "Add them to your shopping list."
                ),
                #: Por qué esta tarjeta y no la de bajos: un cero no necesita umbral para
                #: leerse como faltante, y es lo que la separa de la de abajo — que sí
                #: depende de un umbral que alguien tuvo que cargar.
                "rationale": (
                    f"{len(out_of_stock)} of the {len(all_stock)} items the pantry tracks "
                    "read zero right now, and a zero needs no threshold to be read as "
                    "missing."
                ),
                "evidence_summary": (
                    f"{len(out_of_stock)} item(s) at zero stock: {', '.join(names)}."
                ),
                "confidence": 0.95,
                "source_type": "stock",
            }
        )

    # ── 2. Low-stock items ─────────────────────────────────────────────────
    #: `PantryStock.is_low` con la cantidad en cero ya cubierta arriba. La comparación con
    #: el umbral estaba escrita otra vez acá, y el modelo era el que la tenía: dos copias
    #: de "qué es poco" es cómo la grilla de despensa y esta tarjeta empiezan a discrepar.
    low_stock = [s for s in all_stock if s.is_low and float(s.current_quantity) > 0]
    if low_stock:
        names = [_food_name(s) for s in low_stock[:_NAMES_IN_TEXT]]
        #: El que menos margen tiene sobre su umbral es el que da la razón: es un número
        #: medido de esta corrida, y el desempate por `food_item_id` lo hace estable entre
        #: corridas (regla 5) en vez de dejarlo al orden en que volvió la consulta.
        tightest = min(low_stock, key=lambda s: (_headroom(s), s.food_item_id))
        suggestions.append(
            {
                "category": "shopping",
                "subject_type": "habit",
                "subject_name": "low stock alert",
                "title": "Stock up on low items",
                "text": (
                    f"The following items are running low: {', '.join(names)}. "
                    "Consider restocking soon."
                ),
                "rationale": (
                    f"{_food_name(tightest)} is down to "
                    f"{_amount(tightest.current_quantity)} {tightest.unit} against a "
                    f"threshold of {_amount(tightest.low_stock_threshold)}, the tightest "
                    f"of the {len(low_stock)} items below their mark."
                ),
                "evidence_summary": (
                    f"{len(low_stock)} item(s) below threshold: {', '.join(names)}."
                ),
                "confidence": 0.85,
                "source_type": "stock",
            }
        )

    # ── 3. Frequently purchased items not currently in stock ──────────────
    if purchases:
        purchase_counts: Counter[int] = Counter(p.food_item_id for p in purchases)
        stock_ids = {s.food_item_id for s in all_stock if float(s.current_quantity) > 0}

        #: Solo los que se pueden nombrar: un ítem sin nombre resoluble no puede entrar ni
        #: al texto ni a la razón, así que tampoco cuenta para elegir el más comprado.
        frequent_missing = [
            (fid, count)
            for fid, count in purchase_counts.most_common(10)
            if fid not in stock_ids and fid in names_by_id
        ]
        if frequent_missing:
            named = frequent_missing[:3]
            names = [names_by_id[fid] for fid, _ in named]
            top_id, top_count = named[0]
            suggestions.append(
                {
                    "category": "shopping",
                    "subject_type": "habit",
                    "subject_name": "restock regulars",
                    "title": "Restock your regulars",
                    "text": (
                        f"You regularly buy {', '.join(names)} but they're not in stock. "
                        "Time to restock?"
                    ),
                    "rationale": (
                        f"{names_by_id[top_id]} was bought {top_count} times in the last "
                        f"{_PURCHASE_HISTORY_DAYS} days and is not in stock today."
                    ),
                    "evidence_summary": (f"Frequent purchases not in stock: {', '.join(names)}."),
                    "confidence": 0.75,
                    "source_type": "rule",
                }
            )

    # ── 4. Co-purchase patterns (simple co-occurrence) ─────────────────────
    if len(purchases) >= 4:
        # Group purchases by day (same-day purchases likely bought together)
        day_buckets: dict[str, list[int]] = defaultdict(list)
        for mov in purchases:
            day_key = mov.timestamp.strftime("%Y-%m-%d")
            day_buckets[day_key].append(mov.food_item_id)

        pair_counter: Counter[tuple[int, int]] = Counter()
        for ids in day_buckets.values():
            unique_ids = sorted(set(ids))
            for i in range(len(unique_ids)):
                for j in range(i + 1, len(unique_ids)):
                    pair_counter[(unique_ids[i], unique_ids[j])] += 1

        top_pair = pair_counter.most_common(1)
        if top_pair and top_pair[0][1] >= 2:
            (fid_a, fid_b), count = top_pair[0]
            stock_map = {s.food_item_id: float(s.current_quantity) for s in all_stock}
            # Only suggest if one is missing / low
            a_qty = stock_map.get(fid_a, 0)
            b_qty = stock_map.get(fid_b, 0)
            name_a = names_by_id.get(fid_a)
            name_b = names_by_id.get(fid_b)
            if (a_qty == 0 or b_qty == 0) and name_a and name_b:
                missing = name_a if a_qty == 0 else name_b
                partner = name_b if a_qty == 0 else name_a
                suggestions.append(
                    {
                        "category": "shopping",
                        #: Esta sí nombra un alimento y habla de ese alimento, así que
                        #: su sujeto es el que falta —no el par—: es lo que hay que
                        #: comprar, y es sobre lo que un rechazo enseña.
                        "subject_type": "food",
                        "subject_name": missing,
                        "title": f"Buy {missing} — you often use it with {partner}",
                        "text": (
                            f"You've bought {name_a} and {name_b} on the same day "
                            f"{count} times recently. Currently {missing} is out of stock."
                        ),
                        #: "El mismo día" y no "juntos" porque es lo que se midió: el
                        #: agrupador es un `strftime("%Y-%m-%d")`, no un ticket de compra.
                        "rationale": (
                            f"{name_a} and {name_b} were bought on the same day {count} "
                            f"times in the last {_PURCHASE_HISTORY_DAYS} days, and "
                            f"{missing} is the one at zero."
                        ),
                        "evidence_summary": (
                            f"Co-purchase count: {count} times in last "
                            f"{_PURCHASE_HISTORY_DAYS} days."
                        ),
                        "confidence": 0.65,
                        "source_type": "rule",
                    }
                )

    # De-duplicate and limit
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for s in suggestions:
        if s["title"] not in seen:
            seen.add(s["title"])
            unique.append(s)

    logger.debug(
        "Pantry generator produced %d candidate(s) for household_id=%d "
        "(%d stock row(s), %d purchase(s) in the last %d days)",
        len(unique[:limit]),
        household_id,
        len(all_stock),
        len(purchases),
        _PURCHASE_HISTORY_DAYS,
    )
    return unique[:limit]
