"""Singleton accessor for :class:`~memory_layer.config.settings.Settings`.

All application code should obtain settings via :func:`get_settings` rather
than constructing ``Settings()`` directly. This ensures a single validated
instance is shared across the process and makes test overrides straightforward.
"""

from __future__ import annotations

from typing import Optional

from memory_layer.config.settings import Settings

_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Return the singleton :class:`Settings`, loading it on first call.

    Subsequent calls return the same instance without re-reading the
    environment, ensuring consistent configuration across the process lifetime.
    """
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def override_settings(settings: Settings) -> None:
    """Replace the singleton with *settings*.

    Intended for use in tests and CLI bootstrapping where a pre-constructed
    :class:`Settings` instance should be used instead of the auto-loaded one.
    """
    global _settings
    _settings = settings


def reset_settings() -> None:
    """Clear the singleton so the next :func:`get_settings` call reloads.

    Use in test teardown to guarantee isolation between test cases.
    """
    global _settings
    _settings = None
