"""Unit tests for DemoManager discovery and screenshot resolution.

Regression coverage for the empty Windows demo dropdown: discovery and
screenshot paths must not depend on the process CWD. Runs offscreen; OCR is
mocked so no Tesseract is required.
"""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from PyQt6.QtWidgets import QApplication

from src.desktop import demo_manager as dm
from src.desktop.demo_manager import DEMOS_DIR, PROJECT_ROOT, DemoManager

PACKAGED_DEMOS = {"kth_missing", "rotate_image"}
FAKE_OCR = {"text": [], "left": [], "top": [], "width": [], "height": []}
LESSON_TEXT = (
    "STEP 1\nTITLE: T\nANCHOR: a\nCONTEXT: a\nATTENTION: rectangle\nEMPHASIS: high\nEXPLANATION: e"
)


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def foreign_cwd(tmp_path, monkeypatch):
    """Run the test from an unrelated directory, the way run.ps1 launches."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def make_manager(qt_app):
    managers = []

    def _make(demos_dir=DEMOS_DIR):
        manager = DemoManager(None, demos_dir=demos_dir)
        managers.append(manager)
        return manager

    yield _make
    for manager in managers:
        manager.step_timer.stop()


def _write_demo(root: Path, demo_id: str, manifest: dict) -> Path:
    package = root / demo_id
    package.mkdir(parents=True)
    (package / "lesson.json").write_text(json.dumps(manifest), encoding="utf-8")
    return package


class TestDiscovery:
    def test_default_dir_is_absolute_project_demo_folder(self):
        assert DEMOS_DIR.is_absolute()
        assert DEMOS_DIR == PROJECT_ROOT / "demo"
        assert (PROJECT_ROOT / "src" / "desktop" / "demo_manager.py").is_file()

    def test_packaged_demos_found_from_foreign_cwd(self, foreign_cwd, make_manager):
        manager = make_manager()

        assert set(manager.get_available_demos()) >= PACKAGED_DEMOS
        assert manager.get_available_demos()["kth_missing"]["title"]
        assert Path.cwd() == foreign_cwd

    def test_missing_custom_dir_returns_empty(self, tmp_path, make_manager):
        manager = make_manager(demos_dir=tmp_path / "does_not_exist")

        assert manager.get_available_demos() == {}

    def test_empty_custom_dir_returns_empty(self, tmp_path, make_manager):
        manager = make_manager(demos_dir=tmp_path)

        assert manager.get_available_demos() == {}

    def test_dir_without_lesson_json_is_skipped(self, tmp_path, make_manager):
        (tmp_path / "stray").mkdir()
        (tmp_path / "loose.txt").write_text("x", encoding="utf-8")
        manager = make_manager(demos_dir=tmp_path)

        assert manager.get_available_demos() == {}


class TestStartDemoPaths:
    @pytest.mark.parametrize("demo_id", sorted(PACKAGED_DEMOS))
    def test_packaged_screenshot_is_absolute_existing_file(
        self, foreign_cwd, make_manager, demo_id
    ):
        manager = make_manager()
        emitted = []
        manager.demo_started.connect(emitted.append)

        with patch.object(dm, "extract_ocr_data", return_value=FAKE_OCR) as ocr:
            manager.start_demo(demo_id)

        ocr_path = Path(ocr.call_args.args[0])
        assert ocr_path.is_absolute()
        assert ocr_path.is_file()
        assert ocr_path == (PROJECT_ROOT / "demo" / demo_id / "screenshot.png").resolve()
        assert emitted == [str(ocr_path)]
        assert manager.is_running
        assert Path.cwd() == foreign_cwd

    def test_omitted_screenshot_resolves_in_package_dir(self, tmp_path, foreign_cwd, make_manager):
        root = tmp_path / "custom"
        package = _write_demo(root, "mine", {"lesson_text": LESSON_TEXT})
        (package / "screenshot.png").write_bytes(b"png")
        manager = make_manager(demos_dir=root)
        emitted = []
        manager.demo_started.connect(emitted.append)

        with patch.object(dm, "extract_ocr_data", return_value=FAKE_OCR) as ocr:
            manager.start_demo("mine")

        expected = str((package / "screenshot.png").resolve())
        assert ocr.call_args.args[0] == expected
        assert emitted == [expected]

    def test_explicit_absolute_screenshot_is_preserved(self, tmp_path, make_manager):
        image = tmp_path / "elsewhere" / "shot.png"
        image.parent.mkdir()
        image.write_bytes(b"png")
        root = tmp_path / "custom"
        _write_demo(root, "abs", {"screenshot": str(image), "lesson_text": LESSON_TEXT})
        manager = make_manager(demos_dir=root)
        emitted = []
        manager.demo_started.connect(emitted.append)

        with patch.object(dm, "extract_ocr_data", return_value=FAKE_OCR) as ocr:
            manager.start_demo("abs")

        assert ocr.call_args.args[0] == str(image)
        assert emitted == [str(image)]

    def test_package_relative_screenshot_falls_back_to_package_dir(self, tmp_path, make_manager):
        root = tmp_path / "custom"
        package = _write_demo(
            root, "rel", {"screenshot": "img/shot.png", "lesson_text": LESSON_TEXT}
        )
        (package / "img").mkdir()
        (package / "img" / "shot.png").write_bytes(b"png")
        manager = make_manager(demos_dir=root)

        assert manager.resolve_screenshot_path("rel") == str(
            (package / "img" / "shot.png").resolve()
        )

    def test_unknown_demo_does_not_run_ocr(self, make_manager):
        manager = make_manager()

        with patch.object(dm, "extract_ocr_data") as ocr:
            manager.start_demo("nope")

        ocr.assert_not_called()
        assert not manager.is_running

    def test_ocr_failure_leaves_demo_stopped(self, make_manager):
        manager = make_manager()
        emitted = []
        manager.demo_started.connect(emitted.append)

        with patch.object(dm, "extract_ocr_data", side_effect=OSError("no file")):
            manager.start_demo("kth_missing")

        assert emitted == []
        assert not manager.is_running
        assert not manager.step_timer.isActive()


def test_module_does_not_touch_cwd(foreign_cwd):
    assert os.getcwd() == str(foreign_cwd)
