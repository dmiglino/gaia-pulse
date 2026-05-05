#!/usr/bin/env python3
"""Seed database with demo data for Diego and Rocío's household."""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime, timedelta, timezone
from random import choice, randint, uniform

from sqlalchemy import text

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.body_metric import BodyMetricLog
from app.models.food import FoodItem
from app.models.household import Household
from app.models.meal import MealEvent, MealItemConsumed, MealParticipant
from app.models.notification import Notification
from app.models.pantry import PantryMovement, PantryStock
from app.models.recipe import Recipe
from app.models.signal import BehaviorSignal
from app.models.suggestion import RecommendationPreference, Suggestion
from app.models.user import User
from app.models.workout import ExerciseType, WorkoutExercise, WorkoutParticipant, WorkoutSession


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def days_ago(n: int) -> datetime:
    return utcnow() - timedelta(days=n)


def seed() -> None:
    db = SessionLocal()

    # ── Clear existing data ───────────────────────────────────────────────────
    print("Clearing existing data…")
    for table in [
        "behavior_signals", "notifications", "suggestions", "recommendation_preferences",
        "nlp_ingestion_events", "workout_exercises", "workout_participants", "workout_sessions",
        "meal_items_consumed", "meal_participants", "meal_events", "pantry_movements",
        "pantry_stock", "body_metric_logs", "recipes", "users", "households", "food_items",
        "exercise_types",
    ]:
        db.execute(text(f"DELETE FROM {table}"))
    db.commit()

    # ── Household ─────────────────────────────────────────────────────────────
    print("Creating household…")
    household = Household(
        name="Diego & Rocío's Home",
        timezone="America/Argentina/Buenos_Aires",
        settings_json={"language": "es", "currency": "ARS"},
    )
    db.add(household)
    db.flush()

    # ── Users ─────────────────────────────────────────────────────────────────
    print("Creating users…")
    diego = User(
        household_id=household.id,
        name="Diego",
        email="diego@gaiapulse.app",
        password_hash=hash_password("diego123"),
        height_cm=175.0,
        target_weight_kg=78.0,
        birth_date=None,
        sex="male",
        baseline_activity_level="moderate",
        avatar_color="#6366f1",
        goals_json=["lose_weight", "build_muscle"],
        dietary_preferences_json=["omnivore"],
        preferred_activities_json=["gym", "biking", "walking"],
        impossible_activities_json=["swimming"],
        disliked_activities_json=[],
        preferred_cuisines_json=["italian", "argentinian"],
        is_admin=True,
    )
    rocio = User(
        household_id=household.id,
        name="Rocío",
        email="rocio@gaiapulse.app",
        password_hash=hash_password("rocio123"),
        height_cm=162.0,
        target_weight_kg=58.0,
        birth_date=None,
        sex="female",
        baseline_activity_level="light",
        avatar_color="#ec4899",
        goals_json=["tone", "flexibility", "wellness"],
        dietary_preferences_json=["omnivore"],
        preferred_activities_json=["yoga", "walking", "gym"],
        impossible_activities_json=[],
        disliked_activities_json=["boxing"],
        preferred_cuisines_json=["mediterranean", "argentinian"],
    )
    db.add_all([diego, rocio])
    db.flush()

    # ── Food Items ────────────────────────────────────────────────────────────
    print("Creating food items…")
    foods_data = [
        ("banana", "fruit", "unit", 89, 1.1, 23.0, 0.3, 2.6, True),
        ("apple", "fruit", "unit", 52, 0.3, 14.0, 0.2, 2.4, True),
        ("tomato", "vegetable", "unit", 18, 0.9, 3.9, 0.2, 1.2, True),
        ("bell pepper", "vegetable", "unit", 31, 1.0, 6.0, 0.3, 2.1, True),
        ("onion", "vegetable", "unit", 40, 1.1, 9.3, 0.1, 1.7, True),
        ("garlic", "vegetable", "g", 149, 6.4, 33.1, 0.5, 2.1, True),
        ("egg", "protein", "unit", 155, 12.6, 1.1, 11.0, 0.0, True),
        ("chicken breast", "protein", "g", 165, 31.0, 0.0, 3.6, 0.0, True),
        ("ground beef", "protein", "g", 250, 20.0, 0.0, 18.0, 0.0, True),
        ("milanesa", "protein", "unit", 280, 22.0, 18.0, 12.0, 0.5, True),
        ("pasta", "grain", "g", 371, 13.0, 74.0, 1.5, 2.7, False),
        ("rice", "grain", "g", 365, 7.1, 80.0, 0.7, 1.3, False),
        ("bread", "grain", "unit", 265, 9.0, 49.0, 3.2, 2.7, False),
        ("olive oil", "fat", "ml", 884, 0.0, 0.0, 100.0, 0.0, False),
        ("butter", "fat", "g", 717, 0.9, 0.1, 81.0, 0.0, True),
        ("milk", "dairy", "ml", 61, 3.2, 4.8, 3.3, 0.0, True),
        ("yogurt", "dairy", "g", 100, 5.7, 7.7, 3.3, 0.0, True),
        ("cheese", "dairy", "g", 402, 25.0, 1.3, 33.0, 0.0, True),
        ("orange juice", "beverage", "ml", 45, 0.7, 10.4, 0.2, 0.2, True),
        ("coffee", "beverage", "ml", 2, 0.3, 0.0, 0.0, 0.0, False),
        ("mashed potatoes", "grain", "g", 83, 1.9, 17.0, 0.6, 1.8, True),
        ("ravioli", "grain", "g", 190, 8.0, 28.0, 5.0, 1.5, True),
        ("tomato sauce", "vegetable", "g", 50, 1.5, 9.0, 1.0, 1.8, True),
        ("ice cream", "dairy", "g", 207, 3.5, 24.0, 11.0, 0.0, True),
        ("oats", "grain", "g", 389, 17.0, 66.0, 7.0, 10.6, False),
        ("spinach", "vegetable", "g", 23, 2.9, 3.6, 0.4, 2.2, True),
        ("broccoli", "vegetable", "g", 34, 2.8, 7.0, 0.4, 2.6, True),
        ("salmon", "protein", "g", 208, 20.0, 0.0, 13.0, 0.0, True),
        ("lemon", "fruit", "unit", 29, 1.1, 9.3, 0.3, 2.8, True),
        ("potato", "vegetable", "unit", 77, 2.0, 17.0, 0.1, 2.2, True),
    ]
    foods: dict[str, FoodItem] = {}
    for row in foods_data:
        name, cat, unit, cal, prot, carbs, fat, fiber, perishable = row
        food = FoodItem(
            canonical_name=name,
            category=cat,
            base_unit=unit,
            calories_per_100g=cal,
            protein_g=prot,
            carbs_g=carbs,
            fat_g=fat,
            fiber_g=fiber,
            perishable=perishable,
            aliases_json=[name],
        )
        db.add(food)
        foods[name] = food
    db.flush()

    # ── Exercise Types ────────────────────────────────────────────────────────
    print("Creating exercise types…")
    exercises_data = [
        ("Bench Press", "strength", "chest", "indoor", "barbell, bench", "high"),
        ("Squat", "strength", "legs", "indoor", "barbell", "high"),
        ("Deadlift", "strength", "back", "indoor", "barbell", "high"),
        ("Overhead Press", "strength", "shoulders", "indoor", "barbell", "high"),
        ("Barbell Row", "strength", "back", "indoor", "barbell", "high"),
        ("Dumbbell Curl", "strength", "arms", "indoor", "dumbbells", "moderate"),
        ("Tricep Pushdown", "strength", "arms", "indoor", "cable machine", "moderate"),
        ("Lat Pulldown", "strength", "back", "indoor", "cable machine", "moderate"),
        ("Leg Press", "strength", "legs", "indoor", "leg press machine", "high"),
        ("Plank", "strength", "core", "both", "none", "moderate"),
        ("Push-ups", "strength", "chest", "both", "none", "moderate"),
        ("Running", "cardio", "full_body", "outdoor", "none", "high"),
        ("Cycling", "cardio", "legs", "outdoor", "bicycle", "moderate"),
        ("Walking", "cardio", "full_body", "outdoor", "none", "low"),
        ("Jump Rope", "cardio", "full_body", "indoor", "jump rope", "high"),
        ("Elliptical", "cardio", "full_body", "indoor", "elliptical machine", "moderate"),
        ("Yoga", "flexibility", "full_body", "both", "yoga mat", "low"),
        ("Stretching", "flexibility", "full_body", "both", "none", "low"),
        ("Pilates", "flexibility", "core", "indoor", "mat", "moderate"),
        ("HIIT", "cardio", "full_body", "both", "none", "high"),
    ]
    exercise_types: dict[str, ExerciseType] = {}
    for name, cat, muscle, loc, equip, intensity in exercises_data:
        et = ExerciseType(
            name=name, category=cat, muscle_group=muscle,
            indoor_outdoor=loc, equipment_required=equip, intensity=intensity,
            tags_json=[cat, muscle],
        )
        db.add(et)
        exercise_types[name.lower()] = et
    db.flush()

    # ── Pantry Stock ──────────────────────────────────────────────────────────
    print("Creating pantry stock…")
    pantry_items = [
        ("banana", 6, "unit", 3),
        ("egg", 12, "unit", 6),
        ("milk", 1000, "ml", 500),
        ("olive oil", 500, "ml", 100),
        ("pasta", 500, "g", 100),
        ("rice", 1000, "g", 200),
        ("tomato", 4, "unit", 2),
        ("onion", 3, "unit", 2),
        ("garlic", 100, "g", 20),
        ("bread", 1, "unit", 0.5),
        ("coffee", 200, "g", 50),
        ("chicken breast", 600, "g", 200),
        ("yogurt", 400, "g", 100),
        ("cheese", 200, "g", 50),
        ("potato", 2, "unit", 1),
        ("oats", 500, "g", 100),
        ("spinach", 0, "g", 50),  # empty — low stock alert
        ("butter", 200, "g", 50),
    ]
    for food_name, qty, unit, threshold in pantry_items:
        if food_name not in foods:
            continue
        stock = PantryStock(
            household_id=household.id,
            food_item_id=foods[food_name].id,
            current_quantity=qty,
            unit=unit,
            low_stock_threshold=threshold,
            updated_at=utcnow(),
        )
        db.add(stock)
    db.flush()

    # Add purchase movements for pantry history
    for food_name, qty, unit, _ in pantry_items[:10]:
        if food_name not in foods:
            continue
        db.add(PantryMovement(
            household_id=household.id,
            user_id=diego.id,
            food_item_id=foods[food_name].id,
            movement_type="purchase",
            quantity=qty,
            unit=unit,
            timestamp=days_ago(7),
            notes="Weekly grocery run",
        ))
    db.flush()

    # ── Recipes ───────────────────────────────────────────────────────────────
    print("Creating recipes…")
    recipes = [
        Recipe(
            household_id=household.id,
            name="Milanesa con puré",
            description="Classic Argentine breaded beef with mashed potatoes",
            ingredients_json=[
                {"name": "milanesa", "qty": 200, "unit": "g"},
                {"name": "mashed potatoes", "qty": 300, "unit": "g"},
                {"name": "lemon", "qty": 0.5, "unit": "unit"},
            ],
            portions=2,
            cuisine="argentinian",
            prep_time_minutes=15,
            cook_time_minutes=20,
            tags_json=["dinner", "classic", "beef"],
        ),
        Recipe(
            household_id=household.id,
            name="Ravioli con salsa de tomate",
            description="Ravioli with homemade tomato sauce",
            ingredients_json=[
                {"name": "ravioli", "qty": 300, "unit": "g"},
                {"name": "tomato sauce", "qty": 200, "unit": "g"},
                {"name": "cheese", "qty": 30, "unit": "g"},
            ],
            portions=2,
            cuisine="italian",
            prep_time_minutes=5,
            cook_time_minutes=15,
            tags_json=["dinner", "pasta", "quick"],
        ),
        Recipe(
            household_id=household.id,
            name="Huevos revueltos",
            description="Scrambled eggs with toast",
            ingredients_json=[
                {"name": "egg", "qty": 3, "unit": "unit"},
                {"name": "butter", "qty": 10, "unit": "g"},
                {"name": "bread", "qty": 2, "unit": "unit"},
            ],
            portions=1,
            cuisine="home",
            prep_time_minutes=5,
            cook_time_minutes=5,
            tags_json=["breakfast", "quick", "protein"],
            pantry_compatible=True,
        ),
        Recipe(
            name="Ensalada de pollo",
            description="Grilled chicken breast with spinach and tomato",
            ingredients_json=[
                {"name": "chicken breast", "qty": 200, "unit": "g"},
                {"name": "spinach", "qty": 100, "unit": "g"},
                {"name": "tomato", "qty": 1, "unit": "unit"},
                {"name": "olive oil", "qty": 20, "unit": "ml"},
            ],
            portions=2,
            cuisine="home",
            prep_time_minutes=10,
            cook_time_minutes=15,
            tags_json=["lunch", "healthy", "protein", "light"],
        ),
    ]
    db.add_all(recipes)
    db.flush()

    # ── Body Metrics ──────────────────────────────────────────────────────────
    print("Creating body metrics…")
    diego_base_weight = 85.0
    rocio_base_weight = 63.0
    for i in range(30, 0, -3):
        # Diego's metrics (slight downward trend)
        db.add(BodyMetricLog(
            user_id=diego.id,
            timestamp=days_ago(i),
            weight_kg=round(diego_base_weight - (30 - i) * 0.08 + uniform(-0.3, 0.3), 1),
            body_fat_pct=round(18.5 - (30 - i) * 0.05, 1) if i % 9 == 0 else None,
        ))
        # Rocío's metrics (stable)
        db.add(BodyMetricLog(
            user_id=rocio.id,
            timestamp=days_ago(i),
            weight_kg=round(rocio_base_weight + uniform(-0.5, 0.5), 1),
        ))
    db.flush()

    # ── Meal Events ───────────────────────────────────────────────────────────
    print("Creating meal history…")
    meal_templates = [
        # (meal_type, diego_foods, rocio_foods)
        ("breakfast", ["oats", "banana", "coffee"], ["yogurt", "banana", "coffee"]),
        ("lunch", ["chicken breast", "rice", "tomato"], ["spinach", "tomato", "olive oil"]),
        ("dinner", ["milanesa", "mashed potatoes"], ["ravioli", "tomato sauce"]),
        ("snack", ["apple"], ["yogurt"]),
    ]
    for day in range(14, 0, -1):
        for i, (meal_type, diego_foods, rocio_foods) in enumerate(meal_templates[:3]):
            meal_hour = [8, 13, 20][i]
            meal = MealEvent(
                household_id=household.id,
                timestamp=days_ago(day).replace(hour=meal_hour, minute=0),
                meal_type=meal_type,
                context="home",
            )
            db.add(meal)
            db.flush()

            for user, food_list in [(diego, diego_foods), (rocio, rocio_foods)]:
                participant = MealParticipant(
                    meal_event_id=meal.id,
                    user_id=user.id,
                )
                db.add(participant)
                db.flush()
                for food_name in food_list:
                    item = MealItemConsumed(
                        meal_event_id=meal.id,
                        meal_participant_id=participant.id,
                        food_item_id=foods.get(food_name, None) and foods[food_name].id,
                        normalized_free_text_name=food_name,
                        quantity=1,
                        unit="serving",
                    )
                    db.add(item)
    db.flush()

    # ── Workout Sessions ──────────────────────────────────────────────────────
    print("Creating workout history…")
    workout_templates = [
        # (workout_type, duration, exercises_diego, exercises_rocio)
        ("gym", 60, [("Bench Press", "chest"), ("Overhead Press", "shoulders"), ("Tricep Pushdown", "arms")], [("Lat Pulldown", "back"), ("Dumbbell Curl", "arms"), ("Plank", "core")]),
        ("gym", 50, [("Squat", "legs"), ("Leg Press", "legs"), ("Deadlift", "back")], [("Yoga", "full_body"), ("Stretching", "full_body")]),
        ("cycling", 40, [("Cycling", "legs")], None),
        ("yoga", 45, None, [("Yoga", "full_body"), ("Stretching", "full_body")]),
        ("walking", 30, [("Walking", "full_body")], [("Walking", "full_body")]),
    ]
    for day in range(28, 0, -3):
        template = workout_templates[day % len(workout_templates)]
        wtype, duration, diego_ex, rocio_ex = template

        # Some workouts only one person participates
        participants_data = []
        if diego_ex:
            participants_data.append((diego, diego_ex))
        if rocio_ex:
            participants_data.append((rocio, rocio_ex))

        if not participants_data:
            continue

        session = WorkoutSession(
            household_id=household.id,
            timestamp_start=days_ago(day).replace(hour=18, minute=0),
            duration_minutes=duration,
            workout_type=wtype,
            source="manual",
        )
        db.add(session)
        db.flush()

        for user, exercises in participants_data:
            wp = WorkoutParticipant(
                workout_session_id=session.id,
                user_id=user.id,
            )
            db.add(wp)
            db.flush()
            for ex_name, muscle in exercises:
                db.add(WorkoutExercise(
                    workout_session_id=session.id,
                    workout_participant_id=wp.id,
                    exercise_name=ex_name,
                    muscle_group=muscle,
                    sets=3,
                    reps=10 if muscle != "full_body" else None,
                    duration_minutes=duration // len(exercises),
                ))
    db.flush()

    # ── Recommendation Preferences ────────────────────────────────────────────
    print("Creating preferences…")
    prefs = [
        (diego.id, "exercise", "swimming", "impossible", 1.0, "No pool access in Gaia"),
        (diego.id, "food", "milanesa", "likes", 0.9, None),
        (diego.id, "exercise", "biking", "preferred", 0.9, None),
        (rocio.id, "exercise", "boxing", "dislikes", 0.8, None),
        (rocio.id, "food", "spinach", "likes", 0.8, None),
        (rocio.id, "exercise", "yoga", "preferred", 1.0, None),
    ]
    for user_id, item_type, item_name, signal, strength, notes in prefs:
        db.add(RecommendationPreference(
            user_id=user_id, item_type=item_type, item_name=item_name,
            preference_signal=signal, strength=strength, notes=notes,
        ))
    db.flush()

    # ── Behavior Signals ──────────────────────────────────────────────────────
    print("Creating behavior signals…")
    for _ in range(8):
        db.add(BehaviorSignal(
            user_id=diego.id, signal_type="repeated_meal_choice",
            entity_type="food", entity_name="milanesa", value=1.0, source_type="implicit",
            created_at=days_ago(randint(1, 20)),
        ))
        db.add(BehaviorSignal(
            user_id=rocio.id, signal_type="repeated_activity",
            entity_type="exercise", entity_name="yoga", value=1.0, source_type="implicit",
            created_at=days_ago(randint(1, 20)),
        ))
    db.flush()

    # ── Suggestions ───────────────────────────────────────────────────────────
    print("Creating sample suggestions…")
    suggestions = [
        Suggestion(
            scope_type="user", household_id=household.id, scope_user_id=diego.id,
            category="meal", title="Try pasta with tomato sauce tonight",
            text="You have pasta and tomato sauce in your pantry. A simple, satisfying dinner option.",
            rationale="Pantry stock analysis: both pasta and tomato sauce are available. You've enjoyed this combination before.",
            evidence_summary="pasta: 500g, tomato sauce: 200g in stock",
            priority=7, confidence=0.82, source_type="stock", status="pending",
        ),
        Suggestion(
            scope_type="user", household_id=household.id, scope_user_id=rocio.id,
            category="activity", title="Yoga session today",
            text="A 30-45 minute yoga session would complement your recent gym workouts and aid recovery.",
            rationale="You've done strength training 3 times this week. Yoga is in your preferred activities list.",
            evidence_summary="Recent workouts: 3 gym sessions; yoga listed as preferred",
            priority=6, confidence=0.78, source_type="rule", status="pending",
        ),
        Suggestion(
            scope_type="household", household_id=household.id, scope_user_id=None,
            category="shopping", title="Restock spinach and eggs",
            text="Spinach is out of stock. Eggs are running low (fewer than 6 remaining).",
            rationale="Low-stock detection: spinach at 0g, eggs below threshold.",
            evidence_summary="spinach: 0g (threshold: 50g), eggs: below 6",
            priority=8, confidence=0.95, source_type="stock", status="pending",
        ),
        Suggestion(
            scope_type="user", household_id=household.id, scope_user_id=diego.id,
            category="activity", title="Bike ride this weekend",
            text="You haven't had an outdoor biking session recently. A 30-40 min ride would be great.",
            rationale="Biking is in your preferred activities. Last outdoor ride was 9 days ago.",
            priority=5, confidence=0.72, source_type="preference", status="pending",
        ),
    ]
    db.add_all(suggestions)
    db.flush()

    # ── Notifications ─────────────────────────────────────────────────────────
    print("Creating sample notifications…")
    notifications = [
        Notification(
            household_id=household.id,
            category="low_stock",
            title="Low pantry stock (2 items)",
            body="Running low on: spinach, eggs",
            priority=8, source_type="job",
        ),
        Notification(
            user_id=diego.id, household_id=household.id,
            category="suggestion",
            title="New meal suggestion ready",
            body="We have a meal idea for tonight based on what's in your pantry.",
            priority=5, source_type="system",
        ),
        Notification(
            user_id=rocio.id, household_id=household.id,
            category="metric_reminder",
            title="Time to log your weight",
            body="Rocío, you haven't logged your weight in a while. Tracking trends helps us give you better suggestions.",
            priority=4, source_type="job",
        ),
    ]
    db.add_all(notifications)

    db.commit()
    print("\n✓ Seed complete!")
    print(f"  Household: {household.name}")
    print(f"  Users: Diego (diego@gaiapulse.app / diego123) · Rocío (rocio@gaiapulse.app / rocio123)")
    print(f"  Food items: {len(foods)}")
    print(f"  Pantry items: {len(pantry_items)}")
    print(f"  Recipes: {len(recipes)}")
    print(f"  Meal events: 14 days × 3 meals = 42")
    print(f"  Workout sessions: ~9 sessions")
    print(f"  Suggestions: {len(suggestions)}")
    print(f"  Notifications: {len(notifications)}")


if __name__ == "__main__":
    seed()
