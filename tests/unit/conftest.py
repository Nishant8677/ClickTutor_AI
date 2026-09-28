"""Suite-wide guards. A DesktopController built by any test must be silent:
the default narration factory would find real SAPI voices on Windows."""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication

import src.desktop.narration as narration_module


@pytest.fixture(scope="session", autouse=True)
def _keep_qt_application_alive():
    """Keep Qt's application wrapper alive for every QObject test."""
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


class SilentFactory:
    available = False
    unavailable_reason = "voice disabled in tests"

    def create(self):
        raise narration_module.NarrationUnavailableError(self.unavailable_reason)


@pytest.fixture(autouse=True)
def _no_real_voice(monkeypatch):
    monkeypatch.setattr(narration_module, "default_engine_factory", SilentFactory)
