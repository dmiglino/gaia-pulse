from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from app.core.clock import as_utc, local_today
from app.models.meal import MealEvent, MealItemConsumed, MealParticipant
from app.recommendations import learning
from app.repositories.food_repo import FoodRepository
from app.repositories.meal_repo import MealRepository
from app.schemas.meal import MealEventCreate


class MealService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.meal_repo = MealRepository(db)
        self.food_repo = FoodRepository(db)

    def log_meal(self, household_id: int, data: MealEventCreate) -> MealEvent:
        """Create a full meal event with per-user participants and consumed items."""
        event = MealEvent(
            household_id=household_id,
            #: Todo lo que se guarda es UTC — de eso dependen ahora los límites del
            #: día local. El schema acepta un `datetime` cualquiera, así que un POST a
            #: `/api/v1/meals` con offset propio entraba tal cual y en SQLite quedaba
            #: guardado con la hora de pared de *su* zona, no de la nuestra.
            timestamp=as_utc(data.timestamp),
            meal_type=data.meal_type,
            context=data.context,
            notes=data.notes,
        )
        self.db.add(event)
        self.db.flush()

        for p_data in data.participants:
            participant = MealParticipant(
                meal_event_id=event.id,
                user_id=p_data.user_id,
                portion_label=p_data.portion_label,
                estimated_total_grams=p_data.estimated_total_grams,
                hunger_before=p_data.hunger_before,
                satiety_after=p_data.satiety_after,
                notes=p_data.notes,
            )
            self.db.add(participant)
            self.db.flush()

            for item_data in p_data.items:
                # Attempt to link to known FoodItem
                food_item = self.food_repo.find_by_name(item_data.food_name)
                item = MealItemConsumed(
                    meal_event_id=event.id,
                    meal_participant_id=participant.id,
                    food_item_id=food_item.id if food_item else None,
                    normalized_free_text_name=item_data.food_name.strip().lower(),
                    quantity=item_data.quantity,
                    unit=item_data.unit,
                    estimated_grams=item_data.estimated_grams,
                    preparation=item_data.preparation,
                    affects_stock=item_data.affects_stock,
                    notes=None,
                )
                self.db.add(item)

            # Record implicit behavior signals for each food eaten
            #: Vía `learning.record_signal` y no `signal_repo.record` directo: es el único
            #: lugar que normaliza el nombre antes de guardarlo, y sin eso el "Brócoli" de
            #: una captura y el "brocoli" de otra quedaban como dos sujetos distintos que
            #: nunca se sumaban entre sí ni matcheaban con el catálogo.
            for item_data in p_data.items:
                learning.record_signal(
                    self.db,
                    user_id=p_data.user_id,
                    signal_type="repeated_meal_choice",
                    subject_type="food",
                    subject_name=item_data.food_name,
                    value=1.0,
                    source_type="implicit",
                    source_entity_type="meal_event",
                    source_entity_id=event.id,
                    #: A qué hora del día le gusta. `MealEvent.meal_type` ya se guardaba y
                    #: nadie lo leía para aprender: sin esto, "café" es un gusto y no un
                    #: gusto *del desayuno*, y la 4.4.5 tendría que volver a buscar la
                    #: comida para averiguarlo.
                    context={"meal_type": data.meal_type} if data.meal_type else None,
                )

        self.db.flush()
        self.db.commit()
        self.db.refresh(event)
        return event

    def get_meals(
        self,
        household_id: int,
        limit: int = 20,
        offset: int = 0,
        user_id: int | None = None,
        on_date: date | None = None,
    ) -> list[MealEvent]:
        """Las comidas del hogar, de la más nueva a la más vieja.

        `on_date` filtra un solo día: el repositorio ya sabía filtrar por rango, pero
        la pantalla de comidas no tenía forma de pedirlo — su filtro de fecha mandaba
        un parámetro que la ruta no leía.
        """
        return self.meal_repo.get_household_meals(
            household_id,
            limit=limit,
            offset=offset,
            user_id=user_id,
            start_date=on_date,
            end_date=on_date,
        )

    def get_today_meals(self, household_id: int) -> list[MealEvent]:
        #: `date.today()` es el día del reloj del proceso — UTC en el contenedor —,
        #: así que entre las 21:00 y la medianoche local "hoy" era mañana.
        return self.meal_repo.get_today_meals(household_id, local_today())

    def get_meal(self, meal_id: int) -> MealEvent | None:
        return self.meal_repo.get_with_participants(meal_id)

    def delete_meal(self, meal_id: int) -> bool:
        meal = self.meal_repo.get(meal_id)
        if not meal:
            return False
        self.meal_repo.delete(meal)
        self.db.commit()
        return True

    def repeat_meal(self, meal_id: int, for_user_id: int | None = None) -> MealEvent:
        """Duplicate a past meal event with the current timestamp."""
        orig = self.meal_repo.get_with_participants(meal_id)
        if not orig:
            raise ValueError(f"Meal {meal_id} not found")

        event = MealEvent(
            household_id=orig.household_id,
            timestamp=as_utc(datetime.now(UTC)),
            meal_type=orig.meal_type,
            context=orig.context,
            notes=orig.notes,
        )
        self.db.add(event)
        self.db.flush()

        # If for_user_id is specified and was in participants, we only repeat for that user
        # otherwise repeat for all original participants
        participants_to_copy = orig.participants
        if for_user_id is not None:
            user_participants = [p for p in orig.participants if p.user_id == for_user_id]
            if user_participants:
                participants_to_copy = user_participants

        for orig_p in participants_to_copy:
            participant = MealParticipant(
                meal_event_id=event.id,
                user_id=orig_p.user_id,
                portion_label=orig_p.portion_label,
                estimated_total_grams=orig_p.estimated_total_grams,
                hunger_before=orig_p.hunger_before,
                satiety_after=orig_p.satiety_after,
                notes=orig_p.notes,
            )
            self.db.add(participant)
            self.db.flush()

            for item in orig_p.items_consumed:
                new_item = MealItemConsumed(
                    meal_event_id=event.id,
                    meal_participant_id=participant.id,
                    food_item_id=item.food_item_id,
                    normalized_free_text_name=item.normalized_free_text_name,
                    quantity=item.quantity,
                    unit=item.unit,
                    estimated_grams=item.estimated_grams,
                    preparation=item.preparation,
                    affects_stock=item.affects_stock,
                    notes=item.notes,
                )
                self.db.add(new_item)

                learning.record_signal(
                    self.db,
                    user_id=orig_p.user_id,
                    signal_type="repeated_meal_choice",
                    subject_type="food",
                    subject_name=item.normalized_free_text_name,
                    value=1.0,
                    source_type="implicit",
                    source_entity_type="meal_event",
                    source_entity_id=event.id,
                    context={"meal_type": orig.meal_type} if orig.meal_type else None,
                )

        self.db.flush()
        self.db.commit()
        self.db.refresh(event)
        return event
