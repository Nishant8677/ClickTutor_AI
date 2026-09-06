"""ScreenCapture region safeguards, with mss and the WSL bridge mocked out.

No screen is grabbed and no subprocess runs: the native path uses a fake mss
whose grab returns a fixed-size buffer, and the WSL path is asserted to be
refused before its PowerShell bridge could start.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.capture import screen_capture
from src.capture.screen_capture import (
    CaptureError,
    CaptureSizeMismatchError,
    RegionUnsupportedError,
    ScreenCapture,
)

REGION = {"left": 100, "top": 80, "width": 400, "height": 200}


class _FakeMss:
    """Stands in for mss.mss(): records grabs and returns an image of a chosen size."""

    def __init__(self, grab_size=None):
        self.grab_size = grab_size
        self.grabs: list[dict] = []
        self.monitors = [
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
        ]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def grab(self, monitor):
        self.grabs.append(dict(monitor))
        width, height = self.grab_size or (monitor["width"], monitor["height"])
        return SimpleNamespace(size=(width, height), bgra=bytes(width * height * 4))


@pytest.fixture
def native(monkeypatch):
    """A ScreenCapture that believes it is native and never runs a subprocess."""
    monkeypatch.setattr(ScreenCapture, "_is_wsl", lambda self: False)
    forbidden = MagicMock(side_effect=AssertionError("subprocess must not run"))
    monkeypatch.setattr(screen_capture.subprocess, "run", forbidden)
    monkeypatch.setattr(screen_capture.subprocess, "check_output", forbidden)
    return ScreenCapture()


def _install_mss(monkeypatch, fake):
    monkeypatch.setattr(screen_capture.mss, "mss", lambda: fake)
    return fake


class TestNativeRegionCapture:
    def test_supports_regions(self, native):
        assert native.supports_regions() is True

    def test_explicit_region_is_grabbed_and_sized_exactly(self, native, monkeypatch):
        fake = _install_mss(monkeypatch, _FakeMss())
        image = native.capture(region=dict(REGION))
        assert fake.grabs == [REGION]
        assert image.size == (400, 200)

    def test_region_none_grabs_the_indexed_monitor(self, native, monkeypatch):
        fake = _install_mss(monkeypatch, _FakeMss())
        image = native.capture(monitor_index=1, region=None)
        assert fake.grabs == [fake.monitors[1]]
        assert image.size == (1920, 1080)

    @pytest.mark.parametrize(
        "region",
        [
            {},
            {"left": 0, "top": 0, "width": 400},
            {"left": 0, "top": 0, "width": 0, "height": 200},
            {"left": 0, "top": 0, "width": 400, "height": -5},
            {"left": 0.5, "top": 0, "width": 400, "height": 200},
        ],
    )
    def test_malformed_region_fails_before_any_grab(self, native, monkeypatch, region):
        fake = _install_mss(monkeypatch, _FakeMss())
        with pytest.raises(ValueError):
            native.capture(region=region)
        assert fake.grabs == []

    def test_size_mismatch_is_an_error_not_a_fallback(self, native, monkeypatch):
        fake = _install_mss(monkeypatch, _FakeMss(grab_size=(1920, 1080)))
        with pytest.raises(CaptureSizeMismatchError, match="400x200"):
            native.capture(region=dict(REGION))
        assert len(fake.grabs) == 1

    def test_mismatch_error_is_a_capture_error(self):
        assert issubclass(CaptureSizeMismatchError, CaptureError)
        assert issubclass(RegionUnsupportedError, CaptureError)

    def test_caller_dict_is_not_mutated(self, native, monkeypatch):
        _install_mss(monkeypatch, _FakeMss())
        region = {"left": 100.0, "top": 80, "width": 400, "height": 200}
        native.capture(region=region)
        assert region["left"] == 100.0


class TestWslFallback:
    @pytest.fixture
    def wsl(self, monkeypatch):
        monkeypatch.setattr(ScreenCapture, "_is_wsl", lambda self: True)
        forbidden = MagicMock(side_effect=AssertionError("bridge must not run"))
        monkeypatch.setattr(screen_capture.subprocess, "run", forbidden)
        monkeypatch.setattr(screen_capture.subprocess, "check_output", forbidden)
        monkeypatch.setattr(screen_capture.mss, "mss", forbidden)
        return ScreenCapture(), forbidden

    def test_does_not_support_regions(self, wsl):
        capture, _ = wsl
        assert capture.supports_regions() is False

    def test_explicit_region_is_refused_before_the_bridge_runs(self, wsl):
        capture, forbidden = wsl
        with pytest.raises(RegionUnsupportedError):
            capture.capture(region=dict(REGION))
        forbidden.assert_not_called()

    def test_malformed_region_is_a_value_error_even_on_wsl(self, wsl):
        capture, forbidden = wsl
        with pytest.raises(ValueError):
            capture.capture(region={})
        forbidden.assert_not_called()

    def test_region_none_still_takes_the_bridge_path(self, wsl, monkeypatch):
        capture, _ = wsl
        calls = []

        def fake_check_output(args, timeout=None):
            calls.append(args[0])
            return b"C:\\fake\\path"

        def fake_run(args, check, timeout):
            calls.append(args[0])
            raise RuntimeError("bridge reached")

        monkeypatch.setattr(screen_capture.subprocess, "check_output", fake_check_output)
        monkeypatch.setattr(screen_capture.subprocess, "run", fake_run)

        with pytest.raises(RuntimeError, match="bridge reached"):
            capture.capture(region=None)
        assert calls == ["wslpath", "wslpath", "powershell.exe"]
