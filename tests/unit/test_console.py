"""Unit tests for Unicode-safe stdio configuration.

The bug these guard against is Windows-only -- Python selects the legacy ANSI
codepage for redirected streams there, and printing anything outside it raises
UnicodeEncodeError. These tests therefore exercise the behaviour against fake
streams rather than the real console, so they mean the same thing on any
platform and in CI.
"""

import io
import sys

from src.console import configure_stdio


class FakeStream:
    """Minimal stand-in recording how it was reconfigured."""

    def __init__(self, encoding="cp1252", raises=None):
        self.encoding = encoding
        self.errors = "strict"
        self.raises = raises
        self.calls = []

    def reconfigure(self, encoding=None, errors=None):
        self.calls.append((encoding, errors))
        if self.raises:
            raise self.raises
        self.encoding = encoding
        self.errors = errors


class TestConfigureStdio:
    def test_switches_both_streams_to_utf8(self, monkeypatch):
        out, err = FakeStream(), FakeStream()
        monkeypatch.setattr(sys, "stdout", out)
        monkeypatch.setattr(sys, "stderr", err)

        configure_stdio()

        assert out.encoding == "utf-8"
        assert err.encoding == "utf-8"

    def test_uses_replace_so_a_bad_character_cannot_kill_the_process(self, monkeypatch):
        # A diagnostic that cannot be encoded should degrade, never raise --
        # logging carries OCR'd screen text, which is arbitrary Unicode.
        out = FakeStream()
        monkeypatch.setattr(sys, "stdout", out)

        configure_stdio()

        assert out.errors == "replace"

    def test_is_idempotent(self, monkeypatch):
        out = FakeStream()
        monkeypatch.setattr(sys, "stdout", out)

        configure_stdio()
        configure_stdio()

        assert out.encoding == "utf-8"
        assert len(out.calls) == 2


class TestHostileStreams:
    def test_tolerates_a_stream_without_reconfigure(self, monkeypatch):
        # pythonw.exe and some test harnesses substitute objects that do not
        # support reconfiguration.
        monkeypatch.setattr(sys, "stdout", io.StringIO())
        monkeypatch.setattr(sys, "stderr", io.StringIO())

        configure_stdio()  # must not raise

    def test_tolerates_a_missing_stream(self, monkeypatch):
        monkeypatch.setattr(sys, "stdout", None)
        monkeypatch.setattr(sys, "stderr", None)

        configure_stdio()  # must not raise

    def test_swallows_a_refusing_stream(self, monkeypatch):
        monkeypatch.setattr(sys, "stdout", FakeStream(raises=ValueError("detached")))

        configure_stdio()  # must not raise

    def test_a_failure_on_stdout_does_not_skip_stderr(self, monkeypatch):
        out = FakeStream(raises=OSError("closed"))
        err = FakeStream()
        monkeypatch.setattr(sys, "stdout", out)
        monkeypatch.setattr(sys, "stderr", err)

        configure_stdio()

        assert err.encoding == "utf-8"
