"""Tests for apps.core.ratelimit and its two call sites: the login view
(apps.accounts.views.RateLimitedLoginView) and the public share-link
view (apps.reports.views.shared_view) — closing the Priority 0
KNOWN_LIMITATIONS gap "rate limiting on login/share-link endpoints is
not implemented."
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse

from apps.accounts.models import Organization, UserProfile
from apps.accounts.views import LOGIN_MAX_ATTEMPTS
from apps.reports.views import SHARE_VIEW_MAX_REQUESTS

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture(autouse=True)
def clear_rate_limit_cache():
    """The default cache backend (in-process LocMemCache) persists for
    the life of the test process, not per-test — clear it around every
    test in this file so tests never see another test's counters."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def rate_limit_user(organization):
    user = User.objects.create_user(username="ratelimit_user", password="correct-password-123")
    UserProfile.objects.create(user=user, organization=organization)
    return user


def test_login_blocked_after_max_failed_attempts(client, rate_limit_user):
    login_url = reverse("login")
    for _ in range(LOGIN_MAX_ATTEMPTS):
        response = client.post(login_url, {"username": "ratelimit_user", "password": "wrong-password"}, REMOTE_ADDR="10.0.0.1")
        assert response.status_code == 200
        assert "Demasiados intentos" not in response.content.decode()

    blocked_response = client.post(
        login_url, {"username": "ratelimit_user", "password": "wrong-password"}, REMOTE_ADDR="10.0.0.1"
    )
    assert "Demasiados intentos" in blocked_response.content.decode()

    # Even the CORRECT password is now rejected — proves the block is
    # enforced pre-emptively, not just re-displaying invalid-credentials.
    still_blocked = client.post(
        login_url, {"username": "ratelimit_user", "password": "correct-password-123"}, REMOTE_ADDR="10.0.0.1"
    )
    assert "Demasiados intentos" in still_blocked.content.decode()
    assert not still_blocked.wsgi_request.user.is_authenticated


def test_login_not_blocked_for_a_different_ip(client, rate_limit_user):
    login_url = reverse("login")
    for _ in range(LOGIN_MAX_ATTEMPTS + 2):
        client.post(login_url, {"username": "ratelimit_user", "password": "wrong-password"}, REMOTE_ADDR="10.0.0.2")

    response = client.post(
        login_url, {"username": "ratelimit_user", "password": "correct-password-123"}, REMOTE_ADDR="10.0.0.3"
    )
    assert response.status_code == 302  # successful login from an unrelated IP, never blocked


def test_share_view_rate_limited_after_max_requests(client):
    share_url = reverse("reports:shared-view", args=["nonexistent-token"])
    for _ in range(SHARE_VIEW_MAX_REQUESTS):
        response = client.get(share_url, REMOTE_ADDR="10.0.0.10")
        assert response.status_code == 404  # token doesn't exist, but request itself isn't blocked yet

    blocked = client.get(share_url, REMOTE_ADDR="10.0.0.10")
    assert blocked.status_code == 429


def test_share_view_not_rate_limited_under_threshold(client):
    share_url = reverse("reports:shared-view", args=["another-nonexistent-token"])
    for _ in range(3):
        response = client.get(share_url, REMOTE_ADDR="10.0.0.20")
        assert response.status_code == 404
