"""Service layer for NLP ingestion: orchestrates parse → persist → confirm → execute."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.nlp import NLPIngestionEvent
from app.repositories.user_repo import UserRepository
from app.schemas.body_metric import BodyMetricCreate
from app.schemas.meal import MealEventCreate, MealItemCreate, MealParticipantCreate
from app.schemas.pantry import PurchaseItem, PurchaseRequest, StockAdjustRequest
from app.schemas.suggestion import RecommendationPreferenceCreate
from app.schemas.workout import WorkoutExerciseCreate, WorkoutParticipantCreate, WorkoutSessionCreate
from app.services.body_metric_service import BodyMetricService
from app.services.meal_service import MealService
from app.services.pantry_service import PantryService
from app.services.suggestion_service import SuggestionService
from app.services.workout_service import WorkoutService


class NLPService:
    def __init__(self, db: Session) -> None:
        self.db = db

    async def parse_and_save(
        self,
        user_id: int,
        text: str,
        input_type: str = "text",
        transcription: str | None = None,
    ) -> NLPIngestionEvent:
        """Parse natural language input and persist as a pending NLPIngestionEvent."""
        from app.nlp.parser import NLPParser

        user_repo = UserRepository(self.db)
        user = user_repo.get(user_id)
        speaking_user = user.name.lower().split()[0] if user else "user"

        parser = NLPParser()
        result = await parser.parse(text, speaking_user=speaking_user)

        event = NLPIngestionEvent(
            user_id=user_id,
            input_type=input_type,
            original_input=text,
            transcription=transcription,
            parsed_intent_json=[i.model_dump() for i in result.intents],
            parse_confidence=result.overall_confidence,
            parser_layer=result.parser_layer,
            status="pending_confirmation",
        )
        self.db.add(event)
        self.db.flush()
        self.db.commit()
        return event

    def get_event(self, event_id: int) -> NLPIngestionEvent | None:
        return self.db.get(NLPIngestionEvent, event_id)

    def confirm_event(
        self,
        event_id: int,
        user_id: int,
        household_id: int,
        edited_intents: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Execute all confirmed intents and mark the event as confirmed."""
        event = self.db.get(NLPIngestionEvent, event_id)
        if not event or event.user_id != user_id:
            return {"error": "Event not found or access denied", "results": []}

        intents = edited_intents or event.parsed_intent_json or []

        if not intents:
            event.status = "discarded"
            event.responded_at = datetime.now(timezone.utc)
            self.db.commit()
            return {"results": [], "notice": "Nothing to save — no intents were parsed.", "event_id": event_id}

        user_repo = UserRepository(self.db)
        household_users = user_repo.get_household_users(household_id)
        user_map = {u.name.lower().split()[0]: u for u in household_users}

        results: list[dict[str, Any]] = []
        for intent in intents:
            try:
                result = self._execute_intent(intent, user_id, household_id, user_map)
                results.append({"status": "ok", "intent": intent.get("intent_type"), "result": result})
            except Exception as e:
                results.append({"status": "error", "intent": intent.get("intent_type"), "error": str(e)})

        event.status = "confirmed" if not edited_intents else "edited_and_confirmed"
        event.responded_at = datetime.now(timezone.utc)
        self.db.commit()

        had_errors = any(r["status"] == "error" for r in results)
        return {
            "results": results,
            "event_id": event_id,
            "success": not had_errors,
        }

    def discard_event(self, event_id: int, user_id: int) -> bool:
        event = self.db.get(NLPIngestionEvent, event_id)
        if not event or event.user_id != user_id:
            return False
        event.status = "discarded"
        event.responded_at = datetime.now(timezone.utc)
        self.db.commit()
        return True

    def _execute_intent(
        self,
        intent: dict[str, Any],
        user_id: int,
        household_id: int,
        user_map: dict[str, Any],
    ) -> Any:
        intent_type = intent.get("intent_type")
        now = datetime.now(timezone.utc)

        if intent_type == "add_stock":
            svc = PantryService(self.db)
            items_raw = intent.get("items", [])
            items = [
                PurchaseItem(
                    food_name=i.get("food_name", ""),
                    quantity=i.get("quantity", 1),
                    unit=i.get("unit", "unit"),
                )
                for i in items_raw
                if i.get("food_name")
            ]
            if items:
                svc.process_purchase(household_id, user_id, PurchaseRequest(items=items))
            return {"added": len(items)}

        elif intent_type == "consume_stock":
            svc = PantryService(self.db)
            items_raw = intent.get("items", [])
            consumed = 0
            for i in items_raw:
                food_name = i.get("food_name", "")
                if not food_name:
                    continue
                svc.adjust_stock(
                    household_id,
                    user_id,
                    StockAdjustRequest(
                        food_name=food_name,
                        quantity=i.get("quantity", 1),
                        unit=i.get("unit", "unit"),
                        movement_type="consumption",
                    ),
                )
                consumed += 1
            return {"consumed": consumed}

        elif intent_type == "log_meal":
            svc = MealService(self.db)
            participants = []
            items_per_user = intent.get("items_per_user", {})
            for user_key, items_list in items_per_user.items():
                target_user = user_map.get(user_key.lower()) or next(iter(user_map.values()), None)
                if not target_user:
                    continue
                items = [
                    MealItemCreate(
                        # FoodItemRef uses food_name; legacy dicts may still have 'name'
                        food_name=i.get("food_name") or i.get("name") or "",
                        quantity=i.get("qty") or i.get("quantity"),
                        unit=i.get("unit"),
                    )
                    for i in items_list
                ]
                participants.append(MealParticipantCreate(user_id=target_user.id, items=items))
            if participants:
                svc.log_meal(
                    household_id,
                    MealEventCreate(
                        timestamp=intent.get("timestamp") or now,
                        meal_type=intent.get("meal_type", "other"),
                        context=intent.get("context", "home"),
                        participants=participants,
                    ),
                )
            return {"participants": len(participants)}

        elif intent_type == "log_workout":
            svc = WorkoutService(self.db)
            participant_keys = intent.get("participants", ["both"])
            target_users = []
            for key in participant_keys:
                if key == "both":
                    target_users = list(user_map.values())
                    break
                u = user_map.get(key.lower())
                if u:
                    target_users.append(u)
            if not target_users and user_map:
                target_users = [next(iter(user_map.values()))]

            exercises_raw = intent.get("exercises", [])
            participants = []
            for u in target_users:
                if not u:
                    continue
                exercises = [
                    WorkoutExerciseCreate(
                        exercise_name=ex.get("name", ex) if isinstance(ex, dict) else str(ex),
                        muscle_group=ex.get("muscle_group") if isinstance(ex, dict) else None,
                    )
                    for ex in exercises_raw
                ]
                participants.append(WorkoutParticipantCreate(user_id=u.id, exercises=exercises))

            if participants:
                svc.log_workout(
                    household_id,
                    WorkoutSessionCreate(
                        timestamp_start=intent.get("timestamp") or now,
                        duration_minutes=intent.get("duration_minutes"),
                        workout_type=intent.get("workout_type"),
                        participants=participants,
                        source="text",
                    ),
                )
            return {"participants": len(participants)}

        elif intent_type == "log_body_metric":
            svc = BodyMetricService(self.db)
            user_key = intent.get("user_key", "")
            target_user = user_map.get(user_key.lower()) if user_key else None
            if not target_user:
                target_user = UserRepository(self.db).get(user_id)
            if target_user:
                svc.log_metric(
                    target_user.id,
                    BodyMetricCreate(
                        timestamp=intent.get("timestamp") or now,
                        weight_kg=intent.get("weight_kg"),
                        body_fat_pct=intent.get("body_fat_pct"),
                        waist_cm=intent.get("waist_cm"),
                        sleep_hours=intent.get("sleep_hours"),
                    ),
                )
            return {"user": target_user.name if target_user else "unknown"}

        elif intent_type == "update_preference":
            svc = SuggestionService(self.db)
            user_key = intent.get("user_key", "")
            target_user = user_map.get(user_key.lower()) if user_key else None
            if not target_user:
                target_user = UserRepository(self.db).get(user_id)
            if target_user:
                item_name = intent.get("item_name", "").strip()
                if item_name:
                    svc.save_preference(
                        target_user.id,
                        RecommendationPreferenceCreate(
                            item_type=intent.get("item_type", "exercise"),
                            item_name=item_name,
                            preference_signal=intent.get("preference_signal", intent.get("signal", "likes")),
                        ),
                    )
            return {"preference_saved": True}

        return {"skipped": intent_type}
