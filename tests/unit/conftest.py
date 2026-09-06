"""Suite-wide guards. A DesktopController built by any test must be silent:
the default narration factory would find real SAPI voices on Windows."""

from __future__ import annotations

import pytest

import src.desktop.narration as narration_module


class SilentFactory:
    available = False
    unavailable_reason = "voice disabled in tests"

    def create(self):
        raise narration_module.NarrationUnavailableError(self.unavailable_reason)


@pytest.fixture(autouse=True)
def _no_real_voice(monkeypatch):
    monkeypatch.setattr(narration_module, "default_engine_factory", SilentFactory)
