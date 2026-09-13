from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.core.clock import local_today
from app.core.dependencies import DB, CurrentUser
from app.services.body_metric_service import BodyMetricService
from app.services.meal_service import MealService
from app.services.notification_service import NotificationService
from app.services.pantry_service import PantryService
from app.services.suggestion_service import SuggestionService
from app.services.workout_service import WorkoutService
from app.web.helpers import get_template_context, templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def home(request: Request, current_user: CurrentUser, db: DB) -> HTMLResponse:
    ctx = get_template_context(request, db, current_user)

    meal_svc = MealService(db)
    workout_svc = WorkoutService(db)
    metric_svc = BodyMetricService(db)
    suggestion_svc = SuggestionService(db)
    notif_svc = NotificationService(db)
    pantry_svc = PantryService(db)

    ctx["today_meals"] = meal_svc.get_today_meals(current_user.household_id)
    ctx["today_workouts"] = workout_svc.get_today_sessions(current_user.household_id)
    # `get_latest` no filtra por fecha: devuelve la última medición, sea de hoy o de
    # hace tres semanas. La clave se llamaba `body_metric_today` y la tarjeta decía
    # "Today's Weight", así que un pesaje viejo se presentaba como el de hoy. El dato
    # es el mismo; lo que cambia es que ahora la pantalla dice de cuándo es.
    ctx["latest_body_metric"] = metric_svc.get_latest(current_user.id)
    ctx["pending_suggestions"] = suggestion_svc.get_pending(
        current_user.id, current_user.household_id
    )[:3]
    ctx["unread_notifications_count"] = notif_svc.get_unread_count(
        current_user.id, current_user.household_id
    )
    ctx["low_stock_items"] = pantry_svc.get_low_stock(current_user.household_id)[:5]
    # `date.today()` usa la timezone del proceso (UTC en el contenedor), así que
    # entre las 21:00 y la medianoche local el "hoy" del Home era el día siguiente.
    ctx["today"] = local_today()

    return templates.TemplateResponse("home.html", ctx)
