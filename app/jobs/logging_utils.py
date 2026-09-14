"""Log a job's `except Exception` without leaking bound SQL parameters.

`sqlalchemy.exc.StatementError` (the base of `IntegrityError`) builds its own `__str__()`
from the failed statement **and its bound parameters** — a food name, a person's
`display_name`, whatever the INSERT in that loop iteration was carrying. `logger.exception`
puts that string in the log twice over: once directly, and once more inside the traceback
that `exc_info=True` formats (Python's `traceback.format_exception` ends every frame with
`str(exc)`), so passing `exc_info=True` at all is unsafe for this family — there is no way
to keep the traceback and drop the message.

`log_job_error` is the one call every job's `except Exception` routes through instead of
`logger.exception` directly, so that choice is made once.
"""

import logging

from sqlalchemy.exc import StatementError


def log_job_error(logger: logging.Logger, message: str, exc: Exception) -> None:
    """Log *message* for a failed job iteration, without echoing *exc*'s own text.

    `IntegrityError`/`StatementError` (and only that family — a `StatementError` check
    also catches `IntegrityError`, since it subclasses it) are logged as their type only,
    with no traceback and no `str(exc)`: both would carry the bound parameters. Every
    other exception is logged exactly as `logger.exception(message)` already did — that
    leak is specific to statements carrying bound params, not to exceptions in general.
    """
    if isinstance(exc, StatementError):
        logger.error(
            "%s: %s (bound parameters omitted from this log — see the database's own "
            "error log if you need the failed values)",
            message,
            type(exc).__name__,
        )
        return
    logger.exception(message)
