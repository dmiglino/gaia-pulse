"""Envío de mail por SMTP, para la recuperación de contraseña (fase 7.10).

Sin `SMTP_HOST` configurado la app sigue arriba —igual que sin `OPENAI_API_KEY`—:
`send_email` solo loguea el intento y no manda nada. Es lo que corre en dev y en los
tests; el envío real solo ocurre con SMTP puesto en producción.
"""

import logging
import smtplib
from email.message import EmailMessage

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def send_email(to: str, subject: str, body: str) -> None:
    if not settings.email_enabled:
        logger.info("SMTP no configurado — no se envía el mail a %s (%s)", to, subject)
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from_address
    msg["To"] = to
    msg.set_content(body)

    # Sin este `except`, un SMTP caído tumba con un 500 justo el pedido de reset con
    # un email real — y no el de uno inexistente, que nunca llega a este punto. Esa
    # asimetría es un oráculo de qué emails existen, peor que cualquier diferencia de
    # tiempo: por eso el fallo de envío se loguea y se traga, no se propaga.
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
    except Exception:
        logger.exception("No se pudo enviar el mail a %s (%s)", to, subject)
