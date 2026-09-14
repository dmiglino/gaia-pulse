"""Main recommendation engine for GaiaPulse.

Orchestrates generators, filters, and scoring to produce Suggestion records.

Usage::

    from app.recommendations.engine import RecommendationEngine

    engine = RecommendationEngine()
    suggestions = engine.generate_for_user(db, user, limit=10)
    household_suggestions = engine.generate_for_household(db, household, limit=5)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.household import Household
from app.models.signal import BehaviorSignal
from app.models.suggestion import RecommendationPreference, Suggestion
from app.models.user import User
from app.recommendations import filters, learning, scorer
from app.recommendations.context import build_user_context
from app.recommendations.generators import (
    activity_generator,
    blood_generator,
    meal_generator,
    pantry_generator,
)
from app.repositories.suggestion_repo import BehaviorSignalRepository, SuggestionRepository
from app.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)

_RECENT_SUGGESTION_DAYS = 7

#: Cuánto historial de señales se trae. Lo declara `learning` porque se deriva de las
#: semividas con las que después se pondera (4.4.2): acá era un `30` escrito a mano, y el
#: scorer tenía su propia copia del mismo número. Con decaimiento el corte solo acota la
#: consulta — lo viejo entra y pesa poco, en vez de no entrar.
_RECENT_SIGNAL_DAYS = learning.SIGNAL_HORIZON_DAYS

#: `record_feedback` vivía acá: una segunda implementación completa del camino de
#: escritura de feedback, con `db.get(Suggestion, id)` sin chequeo de pertenencia y la
#: señal de aprendizaje grabada contra quien apretaba el botón. Cero llamadores — el
#: único camino real es `SuggestionService.respond_to_suggestion`, que ahora sí filtra.
#: Se borró en vez de arreglarse: dos implementaciones de la misma escritura es cómo se
#: filtró la primera vez, y esta además tocaba modelos desde fuera de `repositories/`.


class RecommendationEngine:
    """Generate and manage recommendations for users and households."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_for_user(
        self,
        db: Session,
        user: User,
        limit: int = 10,
    ) -> list[Suggestion]:
        """Generate personalised suggestions for a single user.

        Runs meal and activity generators, applies hard constraints,
        scores against behaviour signals, and persists new Suggestion rows.

        Args:
            db: Active SQLAlchemy session.
            user: The target User.
            limit: Maximum number of Suggestion records to create/return.

        Returns:
            List of freshly created (or existing pending) Suggestion records.
        """
        preferences = self._get_preferences(db, user.id)
        signals = self._get_signals(db, user.id)
        recent_suggestions = self._get_recent_suggestions_for_user(db, user.id)

        #: Una sola lectura de todo lo que la app sabe de esta persona, y de acá en
        #: adelante los generadores razonan sobre eso en vez de abrir cada uno sus
        #: propias consultas. No es solo cuántas consultas: eran respuestas que se
        #: contradecían —"qué comió esta semana" tenía dos implementaciones distintas—
        #: y nada las obligaba a coincidir.
        context = build_user_context(db, user)

        # ── Gather candidates ──────────────────────────────────────────
        candidates: list[dict[str, Any]] = []
        candidates += meal_generator.generate(db, user, preferences, context)
        candidates += activity_generator.generate(user, preferences, context)

        # Blood-analysis-driven suggestions (if analysis data exists)
        #: El `try` queda: ya no protege una lectura de base —el panel viene en el
        #: contexto— pero sí el recorrido de `values_json`, que es un blob que escribió
        #: un parser y del que ninguna capa garantiza la forma.
        try:
            candidates += blood_generator.generate(user, context.blood_panel)
        except Exception:
            logger.exception("Blood generator failed for user_id=%d — skipping", user.id)

        # ── Hard constraint filter (explicit blocks) ───────────────────
        candidates = filters.apply_hard_constraints(candidates, user, preferences)

        # ── Signal constraint filter (rejected items from history) ─────
        candidates = filters.apply_signal_constraints(candidates, signals)

        # ── Score and rank ─────────────────────────────────────────────
        #: El índice de atributos se arma una vez por corrida y se pasa al scorer, que no
        #: toca la base. Es lo que permite que una espinaca herede lo que la app aprendió
        #: de las verduras.
        ranked = scorer.score_candidates(
            candidates,
            user,
            signals,
            recent_suggestions,
            subject_attributes=learning.attribute_index(db),
        )

        # ── Persist top-N as Suggestion records ────────────────────────
        suppressed = self._suppressed_subjects_for_user(db, user.id)
        created: list[Suggestion] = []
        for item in self._without_duplicate_subjects(ranked, suppressed, limit):
            suggestion = self._make_user_suggestion(user, item)
            db.add(suggestion)
            created.append(suggestion)

        try:
            db.commit()
            for s in created:
                db.refresh(s)
        except Exception:
            db.rollback()
            logger.exception("Failed to commit user suggestions for user_id=%d", user.id)
            raise

        logger.info(
            "Generated %d suggestion(s) for user_id=%d.", len(created), user.id
        )
        return created

    def generate_for_household(
        self,
        db: Session,
        household: Household,
        limit: int = 5,
    ) -> list[Suggestion]:
        """Generate household-scoped suggestions (primarily pantry/shopping).

        Args:
            db: Active SQLAlchemy session.
            household: The target Household.
            limit: Maximum number of Suggestion records to create/return.

        Returns:
            List of freshly created Suggestion records scoped to the household.
        """
        candidates = pantry_generator.generate(db, household.id, limit=limit * 2)

        #: Una sugerencia de la casa no es una sugerencia sin dueño: la comen las dos
        #: personas. Acá se filtra contra las dos, con las dos reglas asimétricas que
        #: documenta `filters.apply_household_constraints` —los bloqueos se unen, los "no"
        #: aprendidos se intersectan—. Antes de la 4.4.9 esta línea era un comentario que
        #: decía que el filtrado por persona se saltea.
        candidates = filters.apply_household_constraints(
            candidates, self._household_members(db, household.id)
        )

        suppressed = self._suppressed_subjects_for_household(db, household.id)
        created: list[Suggestion] = []
        for item in self._without_duplicate_subjects(candidates, suppressed, limit):
            suggestion = self._make_household_suggestion(household, item)
            db.add(suggestion)
            created.append(suggestion)

        try:
            db.commit()
            for s in created:
                db.refresh(s)
        except Exception:
            db.rollback()
            logger.exception(
                "Failed to commit household suggestions for household_id=%d", household.id
            )
            raise

        logger.info(
            "Generated %d household suggestion(s) for household_id=%d.",
            len(created),
            household.id,
        )
        return created

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_preferences(
        self, db: Session, user_id: int
    ) -> list[RecommendationPreference]:
        """Las preferencias explícitas de esta persona.

        `SuggestionRepository.get_user_preferences` ya existía con esta misma consulta: el
        motor tenía la copia. Queda el método acá —de una línea— porque `_household_members`
        lo llama una vez por integrante y el nombre dice qué se está leyendo.
        """
        return SuggestionRepository(db).get_user_preferences(user_id)

    def _get_signals(self, db: Session, user_id: int) -> list[BehaviorSignal]:
        """El historial de señales dentro del horizonte, de la más nueva a la más vieja.

        `limit=None` a propósito: el default de 200 filas del repositorio es para el panel
        paginado, y acá recortar por cantidad haría que el peso de una señal dependa de
        cuántas otras se anotaron después — que es exactamente lo que el decaimiento por
        fecha vino a reemplazar.
        """
        cutoff = datetime.now(UTC) - timedelta(days=_RECENT_SIGNAL_DAYS)
        return BehaviorSignalRepository(db).get_user_signals(user_id, since=cutoff, limit=None)

    def _household_members(self, db: Session, household_id: int) -> list[filters.HouseholdMember]:
        """Las personas de la casa, cada una con sus preferencias y sus señales.

        Tres consultas por persona en lugar de una por casa, y a propósito: las dos
        lecturas personales reusan los mismos helpers que `generate_for_user`, que filtran
        por `user_id`. Es la regla 4 de `AGENTS.md` —cada consulta de datos personales
        filtra por la persona, aunque quien pregunte viva en la misma casa— y además es lo
        que hace posible la intersección: un `WHERE household_id = ?` traería las señales
        de los dos revueltas, y de ahí no se puede volver.

        Quiénes son las personas lo contesta `UserRepository.get_household_users`, que ya
        existía con el mismo `ORDER BY id` **y** el `is_active` que acá se había escrito de
        nuevo sin él. Esa copia no era solo duplicación: un miembro desactivado entraba a la
        lista sin señales, y como los "no" aprendidos se **intersectan**, un conjunto vacío
        apagaba en silencio esa mitad del filtro —ningún rechazo volvía a sacar nada—.
        """
        users = UserRepository(db).get_household_users(household_id)
        return [
            filters.HouseholdMember(
                user=user,
                preferences=self._get_preferences(db, user.id),
                signals=self._get_signals(db, user.id),
            )
            for user in users
        ]

    def _get_recent_suggestions_for_user(
        self, db: Session, user_id: int
    ) -> list[Suggestion]:
        cutoff = datetime.now(UTC) - timedelta(days=_RECENT_SUGGESTION_DAYS)
        return SuggestionRepository(db).get_created_since(user_id, cutoff)

    #: La cláusula `WHERE` de "este sujeto sigue suprimido" se fue a
    #: `suggestion_repo._still_suppressed`, donde puede vivir al lado de las dos consultas
    #: que la usan. Lo que queda acá es la traducción a claves de sujeto, que es del
    #: vocabulario de `learning` y no de la base: el repositorio devuelve los pares crudos
    #: porque no puede importar `learning` sin cerrar el círculo.

    def _suppressed_subjects_for_user(self, db: Session, user_id: int) -> set[tuple[str, str]]:
        """Subjects this user should not be offered right now."""
        rows = SuggestionRepository(db).get_suppressed_subjects_for_user(user_id)
        return {learning.subject_key(t, n) for t, n in rows}

    def _suppressed_subjects_for_household(
        self, db: Session, household_id: int
    ) -> set[tuple[str, str]]:
        """Lo mismo para las sugerencias de scope household."""
        rows = SuggestionRepository(db).get_suppressed_subjects_for_household(household_id)
        return {learning.subject_key(t, n) for t, n in rows}

    @staticmethod
    def _without_duplicate_subjects(
        ranked: list[dict[str, Any]],
        already_suppressed: set[tuple[str, str]],
        limit: int,
    ) -> list[dict[str, Any]]:
        """Take up to *limit* candidates, at most one per subject.

        Este es el dedup **antes** de persistir que pide el plan. Hasta acá el único
        control de repetición era la penalización de diversidad del scorer, que es un
        ajuste de score y no un filtro: un candidato de confianza 0.95 bajaba a 0.75 y
        seguía saliendo primero, así que cada corrida del job escribía otra vez la misma
        sugerencia. Con esto, mientras el sujeto siga suprimido —pendiente de respuesta, o
        pospuesto— no se genera otra del mismo sujeto, y la penalización de diversidad queda
        para cuando la supresión se vence: entonces sí puede volver a aparecer, pero más
        abajo.

        El corte por `limit` se aplica **después** del dedup, no antes: recortar primero
        habría devuelto menos de `limit` sugerencias cada vez que el tope se llenaba de
        duplicados, que es justamente el caso frecuente.
        """
        seen = set(already_suppressed)
        kept: list[dict[str, Any]] = []
        for item in ranked:
            if len(kept) >= limit:
                break
            subject = learning.candidate_subject(item)
            #: Un candidato sin sujeto pasa: no hay con qué deduplicarlo, y descartarlo
            #: sería peor —una sugerencia menos por un dato que le falta a la app, no a
            #: la persona.
            if subject is not None:
                if subject in seen:
                    logger.debug(
                        "Skipping candidate %r: subject %s still suppressed.",
                        item.get("title"),
                        subject,
                    )
                    continue
                seen.add(subject)
            kept.append(item)
        return kept

    def _make_user_suggestion(
        self, user: User, item: dict[str, Any]
    ) -> Suggestion:
        score = float(item.get("_score", item.get("confidence", 0.5)))
        return Suggestion(
            scope_type="user",
            household_id=user.household_id,
            scope_user_id=user.id,
            category=item.get("category", "meal"),
            subject_type=item.get("subject_type"),
            subject_name=item.get("subject_name"),
            title=item["title"],
            text=item["text"],
            rationale=item.get("rationale", ""),
            evidence_summary=item.get("evidence_summary"),
            confidence=round(min(max(score, 0.0), 1.0), 3),
            priority=self._confidence_to_priority(score),
            source_type=item.get("source_type", "rule"),
            status="pending",
        )

    def _make_household_suggestion(
        self, household: Household, item: dict[str, Any]
    ) -> Suggestion:
        score = float(item.get("_score", item.get("confidence", 0.5)))
        return Suggestion(
            scope_type="household",
            household_id=household.id,
            scope_user_id=None,
            category=item.get("category", "shopping"),
            subject_type=item.get("subject_type"),
            subject_name=item.get("subject_name"),
            title=item["title"],
            text=item["text"],
            rationale=item.get("rationale", ""),
            evidence_summary=item.get("evidence_summary"),
            confidence=round(min(max(score, 0.0), 1.0), 3),
            priority=self._confidence_to_priority(score),
            source_type=item.get("source_type", "rule"),
            status="pending",
        )

    @staticmethod
    def _confidence_to_priority(confidence: float) -> int:
        """Map confidence 0–1 to priority 1–10."""
        return max(1, min(10, round(confidence * 10)))
