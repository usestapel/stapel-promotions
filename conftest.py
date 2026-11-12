def pytest_configure(config):
    from django.conf import settings

    if not settings.configured:
        from stapel_promotions._codegen_settings import settings_kwargs

        settings.configure(**settings_kwargs())
        import django

        django.setup()

        from stapel_core.comm.schemas import autoload_schemas

        autoload_schemas()


import pytest  # noqa: E402
from stapel_core.testing import emit_outside_atomic_gate  # noqa: E402,F401


def _make_user(username, **extra):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user(
        username=username, email=f"{username}@example.com", password="x", **extra
    )


@pytest.fixture
def user(db):
    return _make_user("alice")


@pytest.fixture
def other_user(db):
    return _make_user("bob")


@pytest.fixture
def staff(db):
    return _make_user("staff", is_staff=True)


@pytest.fixture
def client_for():
    def _client(u=None):
        from rest_framework.test import APIClient

        c = APIClient()
        if u is not None:
            c.force_authenticate(user=u)
        return c

    return _client


@pytest.fixture
def anon_client(client_for):
    return client_for()


@pytest.fixture
def staff_client(client_for, staff):
    return client_for(staff)


@pytest.fixture
def auth_client(client_for, user):
    return client_for(user)


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    from django.core.cache import cache

    cache.clear()
    yield


@pytest.fixture
def orders():
    """A fake orders caller that sends exactly the contract payloads."""
    from stapel_promotions.tests.orders import FakeOrders

    return FakeOrders()


@pytest.fixture
def captured_events():
    """Collect promotions emits delivered in-process."""
    from stapel_core.comm import action_registry, subscribe_action

    collected = []

    def _handler(event):
        collected.append(event)

    names = [
        "promotions.redemption.committed",
        "promotions.redemption.released",
        "promotions.promotion.exhausted",
    ]
    for name in names:
        subscribe_action(name, _handler)
    try:
        yield collected
    finally:
        for name in names:
            handlers = action_registry._subscribers.get(name, [])
            if _handler in handlers:
                handlers.remove(_handler)
