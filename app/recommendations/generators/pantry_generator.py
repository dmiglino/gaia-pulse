"""Pantry / shopping suggestion generator.

Generates suggestions based on:
- Items that are low or out of stock
- Frequently purchased items (from PantryMovement history)
- Items commonly used together (simple co-occurrence)
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, desc, func
from sqlalchemy.orm import Session

from app.models.food import FoodItem
from app.models.pantry import PantryMovement, PantryStock
from app.models.user import User

logger = logging.getLogger(__name__)

_PURCHASE_HISTORY_DAYS = 60
_MAX_SUGGESTIONS = 6
_LOW_STOCK_RATIO = 0.25   # threshold / qty ratio


def _get_low_stock_items(db: Session, household_id: int) -> list[PantryStock]:
    return (
        db.query(PantryStock)
        .filter(PantryStock.household_id == household_id)
        .all()
    )


def _get_purchase_history(
    db: Session, household_id: int, days: int = _PURCHASE_HISTORY_DAYS
) -> list[PantryMovement]:
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    return (
        db.query(PantryMovement)
        .filter(
            PantryMovement.household_id == household_id,
            PantryMovement.movement_type == "purchase",
            PantryMovement.timestamp >= cutoff,
        )
        .all()
    )


def _food_name(stock: PantryStock) -> str:
    if stock.food_item:
        return stock.food_item.canonical_name
    return f"item #{stock.food_item_id}"


def generate(
    db: Session,
    household_id: int,
    limit: int = _MAX_SUGGESTIONS,
) -> list[dict[str, Any]]:
    """Generate pantry/shopping suggestions for a household.

    Returns a list of suggestion dicts.
    """
    suggestions: list[dict[str, Any]] = []

    all_stock = _get_low_stock_items(db, household_id)
    purchases = _get_purchase_history(db, household_id)

    # ── 1. Out-of-stock items ──────────────────────────────────────────────
    out_of_stock = [s for s in all_stock if float(s.current_quantity) == 0]
    if out_of_stock:
        names = [_food_name(s) for s in out_of_stock[:5]]
        suggestions.append(
            {
                "category": "shopping",
                "title": "Items are out of stock",
                "text": (
                    f"The following items have run out: {', '.join(names)}. "
                    "Add them to your shopping list."
                ),
                "rationale": "Items at zero stock may block meal preparation.",
                "evidence_summary": (
                    f"{len(out_of_stock)} item(s) at zero stock: {', '.join(names)}."
                ),
                "confidence": 0.95,
                "source_type": "stock",
            }
        )

    # ── 2. Low-stock items ─────────────────────────────────────────────────
    low_stock = [
        s for s in all_stock
        if float(s.current_quantity) > 0
        and s.low_stock_threshold is not None
        and float(s.current_quantity) <= float(s.low_stock_threshold)
    ]
    if low_stock:
        names = [_food_name(s) for s in low_stock[:5]]
        suggestions.append(
            {
                "category": "shopping",
                "title": "Stock up on low items",
                "text": (
                    f"The following items are running low: {', '.join(names)}. "
                    "Consider restocking soon."
                ),
                "rationale": "Low stock items may run out before the next planned shopping trip.",
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

        frequent_missing = [
            (fid, count)
            for fid, count in purchase_counts.most_common(10)
            if fid not in stock_ids
        ]
        if frequent_missing:
            # Resolve names
            names: list[str] = []
            for fid, _ in frequent_missing[:3]:
                food = db.query(FoodItem).filter(FoodItem.id == fid).first()
                if food:
                    names.append(food.canonical_name)
            if names:
                suggestions.append(
                    {
                        "category": "shopping",
                        "title": "Restock your regulars",
                        "text": (
                            f"You regularly buy {', '.join(names)} but they're not in stock. "
                            "Time to restock?"
                        ),
                        "rationale": "Items purchased frequently that are not currently available.",
                        "evidence_summary": (
                            f"Frequent purchases not in stock: {', '.join(names)}."
                        ),
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
            unique_ids = list(set(ids))
            for i in range(len(unique_ids)):
                for j in range(i + 1, len(unique_ids)):
                    pair = (min(unique_ids[i], unique_ids[j]), max(unique_ids[i], unique_ids[j]))
                    pair_counter[pair] += 1

        top_pair = pair_counter.most_common(1)
        if top_pair and top_pair[0][1] >= 2:
            (fid_a, fid_b), count = top_pair[0]
            stock_map = {s.food_item_id: float(s.current_quantity) for s in all_stock}
            # Only suggest if one is missing / low
            a_qty = stock_map.get(fid_a, 0)
            b_qty = stock_map.get(fid_b, 0)
            if a_qty == 0 or b_qty == 0:
                food_a = db.query(FoodItem).filter(FoodItem.id == fid_a).first()
                food_b = db.query(FoodItem).filter(FoodItem.id == fid_b).first()
                if food_a and food_b:
                    missing = food_a.canonical_name if a_qty == 0 else food_b.canonical_name
                    partner = food_b.canonical_name if a_qty == 0 else food_a.canonical_name
                    suggestions.append(
                        {
                            "category": "shopping",
                            "title": f"Buy {missing} — you often use it with {partner}",
                            "text": (
                                f"You've purchased {food_a.canonical_name} and "
                                f"{food_b.canonical_name} together {count} times recently. "
                                f"Currently {missing} is out of stock."
                            ),
                            "rationale": "Items frequently purchased together may be needed together.",
                            "evidence_summary": (
                                f"Co-purchase count: {count} times in last {_PURCHASE_HISTORY_DAYS} days."
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

    return unique[:limit]
