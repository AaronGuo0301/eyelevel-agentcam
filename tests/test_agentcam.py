import contextlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.agentcam import (Advice, Detector, Face, format_report, get_advice, parse_args,
                          run, select_target)


class ModelLocationTests(unittest.TestCase):
    def test_pip_and_conda_model_directories(self):
        for distribution in ("pip", "conda"):
            with self.subTest(distribution=distribution), tempfile.TemporaryDirectory() as root:
                directory = Path(root) / ("pip-data" if distribution == "pip"
                                          else "Library/etc/haarcascades")
                directory.mkdir(parents=True)
                names = ("haarcascade_frontalface_default.xml",
                         "haarcascade_eye_tree_eyeglasses.xml")
                for name in names:
                    (directory / name).touch()
                model = Mock()
                model.empty.return_value = False
                cv2 = SimpleNamespace(CascadeClassifier=Mock(return_value=model))
                if distribution == "pip":
                    cv2.data = SimpleNamespace(haarcascades=str(directory))
                with patch("src.agentcam.sys.prefix", root):
                    Detector(cv2)
                self.assertEqual([call.args[0] for call in cv2.CascadeClassifier.call_args_list],
                                 [str(directory / name) for name in names])


def face_at(x, y):
    return Face((int(x - 40), int(y - 30), 80, 100), (x, y), True)


class GuidanceTests(unittest.TestCase):
    def test_directions_and_both_axes(self):
        for x, y, pan, lift in [(100, 100, "left", "up"),
                                (550, 400, "right", "down"),
                                (320, 100, "hold", "up"),
                                (100, 240, "left", "hold"),
                                (320, 240, "hold", "hold")]:
            with self.subTest(x=x, y=y):
                result = get_advice(face_at(x, y), 640, 480)
                self.assertEqual((result.pan, result.lift), (pan, lift))

    def test_deadzone_and_resolution_independence(self):
        self.assertEqual(get_advice(face_at(350, 260), 640, 480).pan, "hold")
        self.assertEqual(get_advice(face_at(350, 260), 640, 480).lift, "hold")
        self.assertEqual(get_advice(face_at(100, 100), 640, 480),
                         get_advice(face_at(200, 200), 1280, 960))

    def test_loss_does_not_reuse_previous_target(self):
        self.assertIsNone(select_target([], face_at(100, 100)))
        self.assertEqual(get_advice(None, 640, 480), Advice("hold", "hold"))

    def test_multiple_faces_prefer_overlap_then_largest(self):
        small = face_at(100, 100)
        large = Face((300, 200, 200, 200), (400, 280))
        self.assertIs(select_target([small, large], None), large)
        self.assertIs(select_target([small, large], face_at(105, 100)), small)

    def test_reports_distinguish_missing_frame_and_missing_face(self):
        advice = Advice("hold", "hold")
        self.assertIn("未知", format_report([], None, advice, False))
        self.assertIn("未检测到人脸", format_report([], None, advice))
        estimated = Face((10, 10, 100, 100), (60, 50))
        self.assertIn("眼高估计", format_report([estimated], estimated, advice))

    def test_reject_invalid_options(self):
        for argv in [["--interval", "0"], ["--duration", "nan"],
                     ["--deadzone-x", "0.5"], ["--camera", "-1"]]:
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    parse_args(argv)


class FakeFrame:
    size = 640 * 480 * 3
    shape = (480, 640, 3)


class FakeCapture:
    def __init__(self, available=True):
        self.available = available
        self.released = False

    def set(self, *args):
        pass

    def read(self):
        return self.available, FakeFrame() if self.available else None

    def release(self):
        self.released = True


class FakeCV:
    CAP_PROP_FRAME_WIDTH = 3
    CAP_PROP_FRAME_HEIGHT = 4
    CAP_PROP_FPS = 5


class CaptureLoopTests(unittest.TestCase):
    def test_reports_every_three_seconds_and_releases_camera(self):
        capture = FakeCapture()
        clock = iter([0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7])
        output = io.StringIO()
        with patch("src.agentcam.open_camera", return_value=capture), \
                patch("src.agentcam.Detector") as detector, \
                patch("src.agentcam.time.monotonic", side_effect=lambda: next(clock)), \
                contextlib.redirect_stdout(output):
            detector.return_value.detect.return_value = []
            run(parse_args(["--duration", "7"]), FakeCV())
        self.assertEqual(output.getvalue().count("未检测到人脸"), 3)
        self.assertTrue(capture.released)

    def test_camera_failure_stops_and_releases(self):
        capture = FakeCapture(False)
        with patch("src.agentcam.open_camera", return_value=capture), \
                patch("src.agentcam.Detector"), patch("src.agentcam.time.sleep"), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "连续 10 次"):
                run(parse_args([]), FakeCV())
        self.assertTrue(capture.released)

    def test_detector_exception_releases(self):
        capture = FakeCapture()
        with patch("src.agentcam.open_camera", return_value=capture), \
                patch("src.agentcam.Detector") as detector, \
                contextlib.redirect_stdout(io.StringIO()):
            detector.return_value.detect.side_effect = RuntimeError("detection failed")
            with self.assertRaisesRegex(RuntimeError, "detection failed"):
                run(parse_args([]), FakeCV())
        self.assertTrue(capture.released)


if __name__ == "__main__":
    unittest.main()
