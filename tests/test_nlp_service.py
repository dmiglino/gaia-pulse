"""Tests for NLPService: confirm/discard flow, edge cases, intent execution."""

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import local_today, to_local
from app.models.body_metric import BodyMetricLog
from app.models.household import Household
from app.models.meal import MealEvent, MealParticipant
from app.models.nlp import NLPIngestionEvent
from app.models.user import User
from app.services.nlp_service import NLPService


class TestConfirmEvent:
    def test_confirm_unknown_event_returns_error(
        self, db: Session, diego: User, household: Household
    ) -> None:
        svc = NLPService(db)
        result = svc.confirm_event(event_id=999999, user_id=diego.id, household_id=household.id)
        assert "error" in result
        assert result["results"] == []

    def test_confirm_wrong_user_returns_error(
        self, db: Session, diego: User, rocio: User, household: Household
    ) -> None:
        # Create an event owned by diego
        event = NLPIngestionEvent(
            user_id=diego.id,
            input_type="text",
            original_input="test",
            parsed_intent_json=[],
            status="pending_confirmation",
        )
        db.add(event)
        db.flush()

        svc = NLPService(db)
        # Rocío tries to confirm Diego's event
        result = svc.confirm_event(event_id=event.id, user_id=rocio.id, household_id=household.id)
        assert "error" in result

    def test_confirm_empty_intents_discards_event(
        self, db: Session, diego: User, household: Household
    ) -> None:
        event = NLPIngestionEvent(
            user_id=diego.id,
            input_type="text",
            original_input="asdfghjkl random noise",
            parsed_intent_json=[],
            parse_confidence=0.0,
            status="pending_confirmation",
        )
        db.add(event)
        db.flush()

        svc = NLPService(db)
        result = svc.confirm_event(event_id=event.id, user_id=diego.id, household_id=household.id)

        # Should not be treated as success — notice key explains it
        assert result.get("notice") is not None
        assert result["results"] == []
        # Event should be marked discarded, not confirmed
        db.refresh(event)
        assert event.status == "discarded"

    def test_confirm_add_stock_intent_creates_pantry_entry(
        self, db: Session, diego: User, household: Household
    ) -> None:
        event = NLPIngestionEvent(
            user_id=diego.id,
            input_type="text",
            original_input="We bought 6 bananas",
            parsed_intent_json=[
                {
                    "intent_type": "add_stock",
                    "items": [{"food_name": "banana", "quantity": 6, "unit": "unit"}],
                    "participants": ["both"],
                    "confidence": 0.85,
                }
            ],
            parse_confidence=0.85,
            status="pending_confirmation",
        )
        db.add(event)
        db.flush()

        svc = NLPService(db)
        result = svc.confirm_event(event_id=event.id, user_id=diego.id, household_id=household.id)

        assert result.get("success") is True
        assert len(result["results"]) == 1
        assert result["results"][0]["status"] == "ok"
        assert result["results"][0]["result"]["added"] == 1

        db.refresh(event)
        assert event.status == "confirmed"

    def test_confirm_marks_event_confirmed(
        self, db: Session, diego: User, household: Household
    ) -> None:
        event = NLPIngestionEvent(
            user_id=diego.id,
            input_type="text",
            original_input="Today I weigh 82 kg",
            parsed_intent_json=[
                {
                    "intent_type": "log_body_metric",
                    "user_key": "diego",
                    "weight_kg": 82.0,
                    "participants": ["diego"],
                    "confidence": 0.9,
                }
            ],
            parse_confidence=0.9,
            status="pending_confirmation",
        )
        db.add(event)
        db.flush()

        svc = NLPService(db)
        result = svc.confirm_event(event_id=event.id, user_id=diego.id, household_id=household.id)

        assert result.get("success") is True
        db.refresh(event)
        assert event.status == "confirmed"
        assert event.responded_at is not None

        #: Y la fila, que es el punto: el test miraba solo `status`, así que pasaba
        #: igual si el pesaje no se escribía nunca o se le escribía a otra persona.
        log = db.scalars(select(BodyMetricLog)).one()
        assert (log.user_id, log.weight_kg) == (diego.id, 82.0)

    def test_confirm_with_partial_errors_reports_them(
        self, db: Session, diego: User, household: Household
    ) -> None:
        """An intent that raises should appear as status=error but not crash everything."""
        event = NLPIngestionEvent(
            user_id=diego.id,
            input_type="text",
            original_input="mixed input",
            parsed_intent_json=[
                {
                    "intent_type": "log_body_metric",
                    "user_key": "diego",
                    "weight_kg": 82.0,
                    "participants": ["diego"],
                    "confidence": 0.9,
                },
                {
                    # Intentionally broken: unknown intent type
                    "intent_type": "unknown_intent",
                    "confidence": 0.5,
                },
            ],
            parse_confidence=0.7,
            status="pending_confirmation",
        )
        db.add(event)
        db.flush()

        svc = NLPService(db)
        result = svc.confirm_event(event_id=event.id, user_id=diego.id, household_id=household.id)

        # Both intents processed; first succeeds, second is "skipped" (not error)
        assert len(result["results"]) == 2
        statuses = {r["status"] for r in result["results"]}
        assert "ok" in statuses


class TestDiscardEvent:
    def test_discard_marks_event_discarded(self, db: Session, diego: User) -> None:
        event = NLPIngestionEvent(
            user_id=diego.id,
            input_type="text",
            original_input="something",
            parsed_intent_json=[],
            status="pending_confirmation",
        )
        db.add(event)
        db.flush()

        svc = NLPService(db)
        ok = svc.discard_event(event.id, diego.id)
        assert ok is True

        db.refresh(event)
        assert event.status == "discarded"
        assert event.responded_at is not None

    def test_discard_wrong_user_returns_false(self, db: Session, diego: User, rocio: User) -> None:
        event = NLPIngestionEvent(
            user_id=diego.id,
            input_type="text",
            original_input="something",
            parsed_intent_json=[],
            status="pending_confirmation",
        )
        db.add(event)
        db.flush()

        svc = NLPService(db)
        ok = svc.discard_event(event.id, rocio.id)
        assert ok is False

        db.refresh(event)
        assert event.status == "pending_confirmation"  # unchanged

    def test_discard_nonexistent_event_returns_false(self, db: Session, diego: User) -> None:
        svc = NLPService(db)
        ok = svc.discard_event(event_id=999999, user_id=diego.id)
        assert ok is False


class TestLogMealIntent:
    def test_meal_with_items_per_user(
        self, db: Session, diego: User, rocio: User, household: Household
    ) -> None:
        event = NLPIngestionEvent(
            user_id=diego.id,
            input_type="text",
            original_input="Diego ate pasta, Rocío ate salad",
            parsed_intent_json=[
                {
                    "intent_type": "log_meal",
                    "meal_type": "dinner",
                    "items_per_user": {
                        "diego": [{"food_name": "pasta", "qty": None, "unit": None}],
                        "rocio": [{"food_name": "salad", "qty": None, "unit": None}],
                    },
                    "participants": ["both"],
                    "confidence": 0.85,
                }
            ],
            parse_confidence=0.85,
            status="pending_confirmation",
        )
        db.add(event)
        db.flush()

        svc = NLPService(db)
        result = svc.confirm_event(event_id=event.id, user_id=diego.id, household_id=household.id)

        assert result.get("success") is True
        intent_result = result["results"][0]["result"]
        assert intent_result["participants"] == 2

    def test_meal_empty_items_per_user_skips_gracefully(
        self, db: Session, diego: User, household: Household
    ) -> None:
        """Empty items_per_user should not crash, and must not report a save.

        Devolvía ``{"participants": 0}``, que la pantalla de resultado leía como un
        intent que escribió: tilde verde y "1 cosa registrada" sobre una comida que
        no existe. Un intent que no tiene qué escribir se declara salteado.
        """
        event = NLPIngestionEvent(
            user_id=diego.id,
            input_type="text",
            original_input="we had dinner",
            parsed_intent_json=[
                {
                    "intent_type": "log_meal",
                    "meal_type": "dinner",
                    "items_per_user": {},
                    "participants": ["both"],
                    "confidence": 0.4,
                }
            ],
            parse_confidence=0.4,
            status="pending_confirmation",
        )
        db.add(event)
        db.flush()

        svc = NLPService(db)
        result = svc.confirm_event(event_id=event.id, user_id=diego.id, household_id=household.id)

        # Event processed but nothing logged — no crash, and no claim of a save
        assert result["results"][0]["status"] == "ok"
        assert result["results"][0]["result"] == {"skipped": "log_meal"}
        assert result["saved_count"] == 0
        assert list(db.scalars(select(MealParticipant)).all()) == []


class TestTimestampResolution:
    """`time_reference` ('ayer'/'yesterday') y `override_date` deciden el día que se
    guarda — antes, los tres siempre caían en `datetime.now()` sin mirar ninguno de
    los dos (`app/services/nlp_service.py::_resolve_timestamp`).
    """

    def _meal_event(self, diego: User, time_reference: str | None) -> NLPIngestionEvent:
        intent = {
            "intent_type": "log_meal",
            "meal_type": "dinner",
            "items_per_user": {"diego": [{"food_name": "pasta", "qty": None, "unit": None}]},
            "participants": ["diego"],
            "confidence": 0.85,
        }
        if time_reference is not None:
            intent["time_reference"] = time_reference
        event = NLPIngestionEvent(
            user_id=diego.id,
            input_type="text",
            original_input="test",
            parsed_intent_json=[intent],
            parse_confidence=0.85,
            status="pending_confirmation",
        )
        return event

    def test_no_time_reference_lands_on_today(
        self, db: Session, diego: User, household: Household
    ) -> None:
        event = self._meal_event(diego, time_reference=None)
        db.add(event)
        db.flush()

        svc = NLPService(db)
        result = svc.confirm_event(event_id=event.id, user_id=diego.id, household_id=household.id)
        assert result.get("success") is True

        meal = db.scalars(select(MealEvent)).one()
        assert to_local(meal.timestamp).date() == local_today()

    @pytest.mark.parametrize("time_reference", ["yesterday", "ayer", "Yesterday"])
    def test_yesterday_reference_lands_on_the_local_day_before(
        self, db: Session, diego: User, household: Household, time_reference: str
    ) -> None:
        event = self._meal_event(diego, time_reference=time_reference)
        db.add(event)
        db.flush()

        svc = NLPService(db)
        result = svc.confirm_event(event_id=event.id, user_id=diego.id, household_id=household.id)
        assert result.get("success") is True

        meal = db.scalars(select(MealEvent)).one()
        assert to_local(meal.timestamp).date() == local_today() - timedelta(days=1)

    def test_a_same_day_reference_does_not_shift_the_date(
        self, db: Session, diego: User, household: Household
    ) -> None:
        """'Tonight'/'esta noche' still means today — only yesterday/ayer shifts."""
        event = self._meal_event(diego, time_reference="tonight")
        db.add(event)
        db.flush()

        svc = NLPService(db)
        svc.confirm_event(event_id=event.id, user_id=diego.id, household_id=household.id)

        meal = db.scalars(select(MealEvent)).one()
        assert to_local(meal.timestamp).date() == local_today()

    def test_override_date_wins_over_a_yesterday_reference(
        self, db: Session, diego: User, household: Household
    ) -> None:
        """The confirmation screen's manual correction outranks what the parser heard."""
        event = self._meal_event(diego, time_reference="yesterday")
        db.add(event)
        db.flush()
        chosen_day = local_today() - timedelta(days=3)

        svc = NLPService(db)
        result = svc.confirm_event(
            event_id=event.id,
            user_id=diego.id,
            household_id=household.id,
            override_date=chosen_day,
        )
        assert result.get("success") is True

        meal = db.scalars(select(MealEvent)).one()
        assert to_local(meal.timestamp).date() == chosen_day
