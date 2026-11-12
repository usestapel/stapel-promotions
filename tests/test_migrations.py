"""Migrations are complete (no pending model change) and apply from zero."""
import io

import pytest
from django.core.management import call_command


@pytest.mark.django_db(transaction=True)
def test_no_missing_migrations():
    out = io.StringIO()
    call_command("makemigrations", "promotions", "--check", "--dry-run", stdout=out)
    assert "No changes detected" in out.getvalue()


@pytest.mark.django_db(transaction=True)
def test_migrate_from_zero_and_back():
    call_command("migrate", "promotions", "zero", verbosity=0)
    call_command("migrate", "promotions", verbosity=0)
