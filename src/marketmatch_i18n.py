"""Canonical MarketMatch locale and display-timezone policy.

Locale controls presentation only.  Timezone resolution remains independent and
canonical timestamps are never rewritten here.
"""

from __future__ import annotations

import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SUPPORTED_LOCALES = ("es", "en", "zh-Hans")
DEFAULT_LOCALE = "es"
DEFAULT_TIMEZONE = "America/Santo_Domingo"
UNDETERMINED_LANGUAGE = "und"

_LOCALE_ALIASES = {
    "es": "es",
    "es-es": "es",
    "es-do": "es",
    "en": "en",
    "en-us": "en",
    "en-gb": "en",
    "zh": "zh-Hans",
    "zh-cn": "zh-Hans",
    "zh-sg": "zh-Hans",
    "zh-hans": "zh-Hans",
    "zh-hans-cn": "zh-Hans",
}


def normalize_locale(value: object) -> str | None:
    """Return a supported canonical locale, or ``None`` at strict boundaries."""

    if type(value) is not str:
        return None
    return _LOCALE_ALIASES.get(value.strip().lower())


def normalize_transcript_language(value: object) -> str:
    """Normalize genuine STT metadata without guessing from transcript text."""

    if type(value) is not str:
        return UNDETERMINED_LANGUAGE
    normalized = normalize_locale(value)
    return normalized or UNDETERMINED_LANGUAGE


def configured_default_locale() -> tuple[str, str]:
    """Resolve deployment locale, safely falling back to Spanish."""

    raw = os.getenv("MARKETMATCH_DEFAULT_LOCALE", DEFAULT_LOCALE)
    normalized = normalize_locale(raw)
    if normalized is None:
        return DEFAULT_LOCALE, "fallback"
    return normalized, "deployment"


def resolve_interface_locale(
    persisted_locale: object = None,
    *,
    project_locale: object = None,
    organization_locale: object = None,
) -> tuple[str, str]:
    """Resolve available server-side hierarchy layers.

    Project and organization locale layers are intentionally extension points:
    the repository currently has no proven settings for either layer.
    """

    persisted = normalize_locale(persisted_locale)
    if persisted is not None:
        return persisted, "user"
    project = normalize_locale(project_locale)
    if project is not None:
        return project, "project"
    organization = normalize_locale(organization_locale)
    if organization is not None:
        return organization, "organization"
    return configured_default_locale()


def configured_default_timezone() -> tuple[str, str]:
    """Resolve and validate the independent deployment display timezone."""

    raw = os.getenv("MARKETMATCH_DEFAULT_TIMEZONE", DEFAULT_TIMEZONE).strip()
    try:
        ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return "UTC", "fallback"
    return raw, "deployment"


__all__ = (
    "DEFAULT_LOCALE",
    "DEFAULT_TIMEZONE",
    "SUPPORTED_LOCALES",
    "UNDETERMINED_LANGUAGE",
    "configured_default_locale",
    "configured_default_timezone",
    "normalize_locale",
    "normalize_transcript_language",
    "resolve_interface_locale",
)
