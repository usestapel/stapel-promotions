"""The beat schedule names real callables, and the passes run without Celery."""
import importlib

import pytest

from stapel_promotions import beat


def test_schedule_names_the_two_passes():
    schedule = beat.get_promotions_beat_schedule()
    assert {entry["task"] for entry in schedule.values()} == {beat.EXPIRE_TASK_NAME, beat.PURGE_TASK_NAME}
    for entry in schedule.values():
        module, _, name = entry["task"].rpartition(".")
        assert callable(getattr(importlib.import_module(module), name))


@pytest.mark.django_db
def test_passes_run():
    assert beat.expire_reservations() == 0
    assert beat.purge_attempts() == 0
