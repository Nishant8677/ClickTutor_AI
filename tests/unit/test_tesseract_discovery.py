"""Unit tests for locating the Tesseract binary.

The failure this guards against is Windows-specific: the UB Mannheim installer
puts tesseract.exe under Program Files without reliably adding it to PATH, and
a process that is already running would not see a PATH change anyway. These
tests fake the filesystem so they mean the same thing on any platform.
"""

import src.ocr_locator as ocr
from src.ocr_locator import configure_tesseract, find_tesseract


class TestFindTesseract:
    def test_prefers_path_when_available(self, monkeypatch):
        monkeypatch.setattr(ocr.shutil, "which", lambda _: "/usr/bin/tesseract")

        assert find_tesseract() == "/usr/bin/tesseract"

    def test_falls_back_to_a_known_windows_location(self, monkeypatch):
        installed = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        monkeypatch.setattr(ocr.shutil, "which", lambda _: None)
        monkeypatch.setattr(ocr, "_WINDOWS_TESSERACT_CANDIDATES", (installed,))
        monkeypatch.setattr(ocr.Path, "is_file", lambda self: str(self) == installed)

        assert find_tesseract() == installed

    def test_returns_none_when_nothing_is_installed(self, monkeypatch):
        monkeypatch.setattr(ocr.shutil, "which", lambda _: None)
        monkeypatch.setattr(ocr, "_WINDOWS_TESSERACT_CANDIDATES", ())

        assert find_tesseract() is None

    def test_skips_candidates_that_do_not_exist(self, monkeypatch):
        real = r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"
        monkeypatch.setattr(ocr.shutil, "which", lambda _: None)
        monkeypatch.setattr(
            ocr,
            "_WINDOWS_TESSERACT_CANDIDATES",
            (r"C:\nope\tesseract.exe", real),
        )
        monkeypatch.setattr(ocr.Path, "is_file", lambda self: str(self) == real)

        assert find_tesseract() == real


class TestConfigureTesseract:
    def test_points_pytesseract_at_the_located_binary(self, monkeypatch):
        located = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        monkeypatch.setattr(ocr, "find_tesseract", lambda: located)

        assert configure_tesseract() == located
        assert ocr.pytesseract.pytesseract.tesseract_cmd == located

    def test_leaves_configuration_alone_when_nothing_is_found(self, monkeypatch):
        # Better to let pytesseract raise its own TesseractNotFoundError than
        # to overwrite a command the user configured by hand.
        monkeypatch.setattr(ocr, "find_tesseract", lambda: None)
        before = ocr.pytesseract.pytesseract.tesseract_cmd

        assert configure_tesseract() is None
        assert ocr.pytesseract.pytesseract.tesseract_cmd == before
