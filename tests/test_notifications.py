"""Tests for notification creation and management."""
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.models.household import Household
from app.models.user import User
from app.repositories.notification_repo import NotificationRepository
from app.schemas.notification import NotificationCreate
from app.services.notification_service import NotificationService


class TestNotificationService:
    def test_create_household_notification(
        self, db: Session, household: Household
    ) -> None:
        svc = NotificationService(db)
        n = svc.create(NotificationCreate(
            household_id=household.id,
            category="low_stock",
            title="Low stock alert",
            body="You're running low on eggs.",
            priority=7,
        ))
        assert n.id is not None
        assert n.category == "low_stock"
        assert not n.is_read
        assert not n.is_dismissed

    def test_create_user_notification(
        self, db: Session, household: Household, diego: User
    ) -> None:
        svc = NotificationService(db)
        n = svc.create(NotificationCreate(
            user_id=diego.id,
            household_id=household.id,
            category="inactivity",
            title="No workouts in 4 days",
            body="Hey Diego, time to move!",
        ))
        assert n.user_id == diego.id

    def test_mark_read(
        self, db: Session, household: Household, diego: User
    ) -> None:
        svc = NotificationService(db)
        n = svc.create(NotificationCreate(
            user_id=diego.id, household_id=household.id,
            category="info", title="Test", body="Body",
        ))
        assert not n.is_read
        svc.mark_read(n.id, diego.id, household.id)
        db.refresh(n)
        assert n.is_read

    def test_dismiss(
        self, db: Session, household: Household, diego: User
    ) -> None:
        svc = NotificationService(db)
        n = svc.create(NotificationCreate(
            user_id=diego.id, household_id=household.id,
            category="info", title="Test", body="Body",
        ))
        svc.dismiss(n.id, diego.id, household.id)
        db.refresh(n)
        assert n.is_dismissed

    def test_unread_count(
        self, db: Session, household: Household, diego: User, rocio: User
    ) -> None:
        svc = NotificationService(db)
        # Create 2 for diego
        svc.create(NotificationCreate(user_id=diego.id, household_id=household.id, category="info", title="A", body="B"))
        svc.create(NotificationCreate(user_id=diego.id, household_id=household.id, category="info", title="C", body="D"))
        # Household-level (should be visible to both)
        svc.create(NotificationCreate(household_id=household.id, category="low_stock", title="Low", body="Low on eggs"))

        count = svc.get_unread_count(diego.id, household.id)
        assert count >= 2

    def test_mark_all_read(
        self, db: Session, household: Household, diego: User
    ) -> None:
        svc = NotificationService(db)
        for i in range(3):
            svc.create(NotificationCreate(
                user_id=diego.id, household_id=household.id,
                category="info", title=f"Notif {i}", body="body",
            ))
        marked = svc.mark_all_read(diego.id, household.id)
        assert marked >= 3
        assert svc.get_unread_count(diego.id, household.id) == 0

    def test_notifications_scoped_to_household(
        self, db: Session, household: Household, diego: User
    ) -> None:
        """Household-level notifications should appear for all household members."""
        svc = NotificationService(db)
        svc.create(NotificationCreate(
            household_id=household.id,
            category="low_stock",
            title="Shared alert",
            body="Pantry needs attention",
        ))
        # Should appear for Diego (member of household)
        notifs = svc.get_for_user(diego.id, household.id)
        assert any(n.title == "Shared alert" for n in notifs)


class TestCooldownScope:
    """`has_recent_by_category` decides whether a job notification is suppressed."""

    def test_one_members_reminder_does_not_suppress_the_other(
        self, db: Session, household: Household, diego: User, rocio: User
    ) -> None:
        """Per-user cooldowns are per user.

        Per-user reminders also carry ``household_id``, so an ``or_``-ed
        household clause made Diego's reminder count as Rocío's.
        """
        NotificationService(db).create(NotificationCreate(
            user_id=diego.id,
            household_id=household.id,
            category="inactivity",
            title="No workouts in 4 days",
            body="Hey Diego, time to move!",
        ))
        repo = NotificationRepository(db)
        assert repo.has_recent_by_category(
            household.id, "inactivity", hours=48, user_id=diego.id
        )
        assert not repo.has_recent_by_category(
            household.id, "inactivity", hours=48, user_id=rocio.id
        )

    def test_household_cooldown_ignores_per_user_rows(
        self, db: Session, household: Household, diego: User
    ) -> None:
        """A reminder addressed to one member is not a household-wide alert."""
        NotificationService(db).create(NotificationCreate(
            user_id=diego.id,
            household_id=household.id,
            category="low_stock",
            title="Diego, buy milk",
            body="body",
        ))
        repo = NotificationRepository(db)
        assert not repo.has_recent_by_category(household.id, "low_stock", hours=6)

    def test_household_cooldown_sees_household_rows(
        self, db: Session, household: Household
    ) -> None:
        NotificationService(db).create(NotificationCreate(
            household_id=household.id,
            category="low_stock",
            title="Low pantry stock",
            body="body",
        ))
        repo = NotificationRepository(db)
        assert repo.has_recent_by_category(household.id, "low_stock", hours=6)
