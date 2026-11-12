"""The housekeeping schedule — shipped by the library.

Wire it in the settings module itself::

    from stapel_promotions.beat import get_promotions_beat_schedule

    CELERY_BEAT_SCHEDULE = {**get_promotions_beat_schedule(), ...}

Two passes: expire lapsed holds (every 5 minutes) and purge old released
or expired attempts (daily). Imports nothing at module level beyond the
optional Celery, so it is safe from a settings module. Without Celery,
``manage.py promotions_housekeeping`` runs the same passes.
"""
from __future__ import annotations

EXPIRE_TASK_NAME = "stapel_promotions.beat.expire_reservations"
PURGE_TASK_NAME = "stapel_promotions.beat.purge_attempts"
EXPIRE_BEAT_KEY = "promotions-expire-reservations"
PURGE_BEAT_KEY = "promotions-purge-attempts"
EXPIRE_INTERVAL_SECONDS = 5 * 60
PURGE_INTERVAL_SECONDS = 24 * 60 * 60


def expire_reservations() -> int:
    """Reserved attempts past RESERVATION_TTL_MINUTES become expired."""
    from .services.redemption import expire_reservations as _expire

    return _expire()


def purge_attempts() -> int:
    """Released/expired attempts older than ATTEMPT_TTL_DAYS are deleted."""
    from .services.redemption import purge_attempts as _purge

    return _purge()


def get_promotions_beat_schedule() -> dict:
    return {
        EXPIRE_BEAT_KEY: {"task": EXPIRE_TASK_NAME, "schedule": float(EXPIRE_INTERVAL_SECONDS)},
        PURGE_BEAT_KEY: {"task": PURGE_TASK_NAME, "schedule": float(PURGE_INTERVAL_SECONDS)},
    }


try:  # pragma: no cover - exercised by whichever profile the host installs
    from celery import shared_task
except ImportError:
    pass
else:
    expire_reservations = shared_task(name=EXPIRE_TASK_NAME)(expire_reservations)
    purge_attempts = shared_task(name=PURGE_TASK_NAME)(purge_attempts)


__all__ = [
    "EXPIRE_BEAT_KEY", "EXPIRE_TASK_NAME", "PURGE_BEAT_KEY", "PURGE_TASK_NAME",
    "expire_reservations", "get_promotions_beat_schedule", "purge_attempts",
]
