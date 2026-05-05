"""initial schema

Revision ID: 0001
Revises:
Create Date: 2024-01-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # households
    op.create_table(
        "households",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("timezone", sa.String(60), nullable=False),
        sa.Column("settings_json", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # users
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("household_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("sex", sa.String(30), nullable=True),
        sa.Column("height_cm", sa.Numeric(5, 1), nullable=True),
        sa.Column("target_weight_kg", sa.Numeric(6, 2), nullable=True),
        sa.Column("baseline_activity_level", sa.String(30), nullable=False, server_default="moderate"),
        sa.Column("goals_json", postgresql.JSONB(), nullable=True),
        sa.Column("dietary_preferences_json", postgresql.JSONB(), nullable=True),
        sa.Column("dietary_restrictions_json", postgresql.JSONB(), nullable=True),
        sa.Column("disliked_foods_json", postgresql.JSONB(), nullable=True),
        sa.Column("preferred_cuisines_json", postgresql.JSONB(), nullable=True),
        sa.Column("preferred_activities_json", postgresql.JSONB(), nullable=True),
        sa.Column("impossible_activities_json", postgresql.JSONB(), nullable=True),
        sa.Column("disliked_activities_json", postgresql.JSONB(), nullable=True),
        sa.Column("recommendation_context_json", postgresql.JSONB(), nullable=True),
        sa.Column("avatar_color", sa.String(20), nullable=False, server_default="#6366f1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_email", "users", ["email"])

    # body_metric_logs
    op.create_table(
        "body_metric_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("weight_kg", sa.Numeric(6, 2), nullable=True),
        sa.Column("body_fat_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("muscle_mass_kg", sa.Numeric(6, 2), nullable=True),
        sa.Column("waist_cm", sa.Numeric(5, 1), nullable=True),
        sa.Column("sleep_hours", sa.Numeric(4, 1), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_body_metric_logs_user_id", "body_metric_logs", ["user_id"])
    op.create_index("ix_body_metric_logs_timestamp", "body_metric_logs", ["timestamp"])

    # food_items
    op.create_table(
        "food_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("canonical_name", sa.String(200), nullable=False),
        sa.Column("category", sa.String(60), nullable=True),
        sa.Column("aliases_json", postgresql.JSONB(), nullable=True),
        sa.Column("base_unit", sa.String(30), nullable=False, server_default="g"),
        sa.Column("serving_size_g", sa.Numeric(), nullable=True),
        sa.Column("calories_per_100g", sa.Numeric(), nullable=True),
        sa.Column("protein_g", sa.Numeric(), nullable=True),
        sa.Column("carbs_g", sa.Numeric(), nullable=True),
        sa.Column("fat_g", sa.Numeric(), nullable=True),
        sa.Column("fiber_g", sa.Numeric(), nullable=True),
        sa.Column("macro_json", postgresql.JSONB(), nullable=True),
        sa.Column("micro_json", postgresql.JSONB(), nullable=True),
        sa.Column("perishable", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("typical_shelf_days", sa.Integer(), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canonical_name"),
    )
    op.create_index("ix_food_items_canonical_name", "food_items", ["canonical_name"])

    # pantry_stock
    op.create_table(
        "pantry_stock",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("household_id", sa.Integer(), nullable=False),
        sa.Column("food_item_id", sa.Integer(), nullable=False),
        sa.Column("current_quantity", sa.Numeric(10, 3), nullable=False, server_default="0"),
        sa.Column("unit", sa.String(30), nullable=False),
        sa.Column("low_stock_threshold", sa.Numeric(10, 3), nullable=True),
        sa.Column("storage_location", sa.String(100), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["food_item_id"], ["food_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pantry_stock_household_id", "pantry_stock", ["household_id"])

    # pantry_movements
    op.create_table(
        "pantry_movements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("household_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("food_item_id", sa.Integer(), nullable=False),
        sa.Column("movement_type", sa.String(30), nullable=False),
        sa.Column("quantity", sa.Numeric(10, 3), nullable=False),
        sa.Column("unit", sa.String(30), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("related_entity_type", sa.String(60), nullable=True),
        sa.Column("related_entity_id", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["food_item_id"], ["food_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pantry_movements_household_id", "pantry_movements", ["household_id"])
    op.create_index("ix_pantry_movements_timestamp", "pantry_movements", ["timestamp"])

    # meal_events
    op.create_table(
        "meal_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("household_id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meal_type", sa.String(30), nullable=False, server_default="other"),
        sa.Column("context", sa.String(40), nullable=False, server_default="home"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_meal_events_household_id", "meal_events", ["household_id"])
    op.create_index("ix_meal_events_timestamp", "meal_events", ["timestamp"])

    # meal_participants
    op.create_table(
        "meal_participants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("meal_event_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("portion_label", sa.String(60), nullable=True),
        sa.Column("estimated_total_grams", sa.Numeric(7, 1), nullable=True),
        sa.Column("hunger_before", sa.Integer(), nullable=True),
        sa.Column("satiety_after", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["meal_event_id"], ["meal_events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_meal_participants_meal_event_id", "meal_participants", ["meal_event_id"])
    op.create_index("ix_meal_participants_user_id", "meal_participants", ["user_id"])

    # meal_items_consumed
    op.create_table(
        "meal_items_consumed",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("meal_event_id", sa.Integer(), nullable=False),
        sa.Column("meal_participant_id", sa.Integer(), nullable=False),
        sa.Column("food_item_id", sa.Integer(), nullable=True),
        sa.Column("normalized_free_text_name", sa.String(200), nullable=False),
        sa.Column("quantity", sa.Numeric(8, 2), nullable=True),
        sa.Column("unit", sa.String(30), nullable=True),
        sa.Column("estimated_grams", sa.Numeric(8, 1), nullable=True),
        sa.Column("preparation", sa.String(100), nullable=True),
        sa.Column("affects_stock", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["food_item_id"], ["food_items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["meal_event_id"], ["meal_events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["meal_participant_id"], ["meal_participants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_meal_items_meal_event_id", "meal_items_consumed", ["meal_event_id"])
    op.create_index("ix_meal_items_participant_id", "meal_items_consumed", ["meal_participant_id"])

    # recipes
    op.create_table(
        "recipes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("household_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("ingredients_json", postgresql.JSONB(), nullable=True),
        sa.Column("portions", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("steps_json", postgresql.JSONB(), nullable=True),
        sa.Column("tags_json", postgresql.JSONB(), nullable=True),
        sa.Column("estimated_nutrition_json", postgresql.JSONB(), nullable=True),
        sa.Column("pantry_compatible", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("prep_time_minutes", sa.Integer(), nullable=True),
        sa.Column("cook_time_minutes", sa.Integer(), nullable=True),
        sa.Column("cuisine", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # exercise_types
    op.create_table(
        "exercise_types",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("muscle_group", sa.String(80), nullable=True),
        sa.Column("indoor_outdoor", sa.String(20), nullable=False, server_default="both"),
        sa.Column("equipment_required", sa.String(200), nullable=True),
        sa.Column("intensity", sa.String(20), nullable=False, server_default="moderate"),
        sa.Column("tags_json", postgresql.JSONB(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    # workout_sessions
    op.create_table(
        "workout_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("household_id", sa.Integer(), nullable=False),
        sa.Column("timestamp_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timestamp_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("workout_type", sa.String(60), nullable=True),
        sa.Column("location", sa.String(100), nullable=True),
        sa.Column("calories_estimated", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(30), nullable=False, server_default="manual"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workout_sessions_household_id", "workout_sessions", ["household_id"])
    op.create_index("ix_workout_sessions_timestamp_start", "workout_sessions", ["timestamp_start"])

    # workout_participants
    op.create_table(
        "workout_participants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workout_session_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workout_session_id"], ["workout_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workout_participants_session_id", "workout_participants", ["workout_session_id"])
    op.create_index("ix_workout_participants_user_id", "workout_participants", ["user_id"])

    # workout_exercises
    op.create_table(
        "workout_exercises",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workout_session_id", sa.Integer(), nullable=False),
        sa.Column("workout_participant_id", sa.Integer(), nullable=False),
        sa.Column("exercise_type_id", sa.Integer(), nullable=True),
        sa.Column("exercise_name", sa.String(120), nullable=False),
        sa.Column("sets", sa.Integer(), nullable=True),
        sa.Column("reps", sa.Integer(), nullable=True),
        sa.Column("load_kg", sa.Numeric(6, 2), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("distance_km", sa.Numeric(6, 2), nullable=True),
        sa.Column("perceived_effort", sa.Integer(), nullable=True),
        sa.Column("muscle_group", sa.String(80), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["exercise_type_id"], ["exercise_types.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workout_participant_id"], ["workout_participants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workout_session_id"], ["workout_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workout_exercises_session_id", "workout_exercises", ["workout_session_id"])
    op.create_index("ix_workout_exercises_participant_id", "workout_exercises", ["workout_participant_id"])

    # suggestions
    op.create_table(
        "suggestions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scope_type", sa.String(20), nullable=False),
        sa.Column("household_id", sa.Integer(), nullable=True),
        sa.Column("scope_user_id", sa.Integer(), nullable=True),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("evidence_summary", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False, server_default="0.5"),
        sa.Column("source_type", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("feedback_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scope_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_suggestions_household_id", "suggestions", ["household_id"])
    op.create_index("ix_suggestions_scope_user_id", "suggestions", ["scope_user_id"])
    op.create_index("ix_suggestions_status", "suggestions", ["status"])
    op.create_index("ix_suggestions_created_at", "suggestions", ["created_at"])

    # recommendation_preferences
    op.create_table(
        "recommendation_preferences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("item_type", sa.String(40), nullable=False),
        sa.Column("item_name", sa.String(200), nullable=False),
        sa.Column("preference_signal", sa.String(40), nullable=False),
        sa.Column("strength", sa.Numeric(4, 3), nullable=False, server_default="1.0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rec_prefs_user_id", "recommendation_preferences", ["user_id"])

    # nlp_ingestion_events
    op.create_table(
        "nlp_ingestion_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("input_type", sa.String(20), nullable=False),
        sa.Column("original_input", sa.Text(), nullable=False),
        sa.Column("transcription", sa.Text(), nullable=True),
        sa.Column("parsed_intent_json", postgresql.JSONB(), nullable=True),
        sa.Column("parse_confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("parser_layer", sa.String(20), nullable=True),
        sa.Column("status", sa.String(40), nullable=False, server_default="pending_confirmation"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_nlp_events_user_id", "nlp_ingestion_events", ["user_id"])
    op.create_index("ix_nlp_events_status", "nlp_ingestion_events", ["status"])

    # notifications
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("household_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("related_entity_type", sa.String(60), nullable=True),
        sa.Column("related_entity_id", sa.Integer(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("source_type", sa.String(30), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notifications_household_id", "notifications", ["household_id"])
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])

    # behavior_signals
    op.create_table(
        "behavior_signals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("signal_type", sa.String(60), nullable=False),
        sa.Column("entity_type", sa.String(40), nullable=False),
        sa.Column("entity_name", sa.String(200), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("value", sa.Numeric(6, 3), nullable=False, server_default="1.0"),
        sa.Column("context_json", postgresql.JSONB(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("source_type", sa.String(30), nullable=False),
        sa.Column("source_entity_type", sa.String(60), nullable=True),
        sa.Column("source_entity_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_behavior_signals_user_id", "behavior_signals", ["user_id"])
    op.create_index("ix_behavior_signals_signal_type", "behavior_signals", ["signal_type"])
    op.create_index("ix_behavior_signals_entity_name", "behavior_signals", ["entity_name"])
    op.create_index("ix_behavior_signals_created_at", "behavior_signals", ["created_at"])


def downgrade() -> None:
    op.drop_table("behavior_signals")
    op.drop_table("notifications")
    op.drop_table("nlp_ingestion_events")
    op.drop_table("recommendation_preferences")
    op.drop_table("suggestions")
    op.drop_table("workout_exercises")
    op.drop_table("workout_participants")
    op.drop_table("workout_sessions")
    op.drop_table("exercise_types")
    op.drop_table("recipes")
    op.drop_table("meal_items_consumed")
    op.drop_table("meal_participants")
    op.drop_table("meal_events")
    op.drop_table("pantry_movements")
    op.drop_table("pantry_stock")
    op.drop_table("food_items")
    op.drop_table("body_metric_logs")
    op.drop_table("users")
    op.drop_table("households")
