"""Local webcam detection and advisory-only pan/lift guidance."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
import math
from pathlib import Path
import sys
import time


@dataclass(frozen=True)
class Face:
    box: tuple[int, int, int, int]
    anchor: tuple[float, float]
    eyes_detected: bool = False


@dataclass(frozen=True)
class Advice:
    pan: str
    lift: str
    dx: float = 0.0
    dy: float = 0.0


def get_advice(face: Face | None, width: int, height: int,
               deadzone_x: float = 0.08, deadzone_y: float = 0.06) -> Advice:
    """Errors are fractions of the full frame; input must be unmirrored."""
    if face is None:
        return Advice("hold", "hold")
    dx = (face.anchor[0] - width / 2) / width
    dy = (face.anchor[1] - height / 2) / height
    return Advice(
        "left" if dx < -deadzone_x else "right" if dx > deadzone_x else "hold",
        "up" if dy < -deadzone_y else "down" if dy > deadzone_y else "hold",
        dx, dy,
    )


def overlap(a: Face, b: Face) -> float:
    ax, ay, aw, ah = a.box
    bx, by, bw, bh = b.box
    intersection = max(0, min(ax + aw, bx + bw) - max(ax, bx)) * max(
        0, min(ay + ah, by + bh) - max(ay, by))
    return intersection / (aw * ah + bw * bh - intersection)


def select_target(faces: list[Face], previous: Face | None) -> Face | None:
    if not faces:
        return None
    if previous is not None:
        candidate = max(faces, key=lambda face: overlap(face, previous))
        if overlap(candidate, previous) >= 0.2:
            return candidate
    return max(faces, key=lambda face: face.box[2] * face.box[3])


class Detector:
    def __init__(self, cv2):
        if not hasattr(cv2, "CascadeClassifier"):
            raise RuntimeError("当前 OpenCV 缺少 cv2.CascadeClassifier 检测接口，"
                               "请在当前 Python 环境安装 requirements.txt 中指定的 OpenCV 4.x。")
        model_names = ("haarcascade_frontalface_default.xml",
                       "haarcascade_eye_tree_eyeglasses.xml")
        pip_models = getattr(getattr(cv2, "data", None), "haarcascades", None)
        candidates = ([Path(pip_models)] if pip_models else []) + [
            Path(sys.prefix) / "Library" / "etc" / "haarcascades",
            Path(sys.prefix) / "share" / "opencv4" / "haarcascades",
            Path(sys.prefix) / "share" / "OpenCV" / "haarcascades",
        ]
        model_dir = next((path for path in candidates
                          if all((path / name).is_file() for name in model_names)), None)
        if model_dir is None:
            raise RuntimeError("未找到人脸和眼部 Haar 模型文件。已检查："
                               + ", ".join(str(path) for path in candidates))
        self.cv2 = cv2
        self.face_model = cv2.CascadeClassifier(str(model_dir / model_names[0]))
        self.eye_model = cv2.CascadeClassifier(str(model_dir / model_names[1]))
        if self.face_model.empty() or self.eye_model.empty():
            raise RuntimeError("无法加载 OpenCV 自带的检测模型，请重新安装 requirements.txt。")

    def detect(self, frame) -> list[Face]:
        cv2 = self.cv2
        # Bound detection cost, retaining coordinates in the original frame.
        scale = min(1.0, 640 / frame.shape[1])
        small = cv2.resize(frame, None, fx=scale, fy=scale) if scale < 1 else frame
        gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
        boxes = self.face_model.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
        faces = []
        for x, y, w, h in boxes:
            # Restrict eyes to the upper face to avoid mouth false positives.
            roi = gray[y:y + int(h * 0.65), x:x + w]
            eyes = self.eye_model.detectMultiScale(
                roi, scaleFactor=1.1, minNeighbors=5,
                minSize=(max(8, int(w * 0.1)), max(8, int(h * 0.1))))
            centers = [(ex + ew / 2, ey + eh / 2) for ex, ey, ew, eh in eyes]
            pairs = [(a, b) for a, b in combinations(centers, 2)
                     if 0.2 * w <= abs(a[0] - b[0]) <= 0.7 * w
                     and abs(a[1] - b[1]) <= 0.15 * h
                     and min(a[0], b[0]) < w / 2 < max(a[0], b[0])]
            if pairs:
                a, b = min(pairs, key=lambda pair: abs(pair[0][1] - pair[1][1]))
                anchor = (x + (a[0] + b[0]) / 2, y + (a[1] + b[1]) / 2)
            else:
                # Approximate eye height; explicitly labelled in terminal output.
                anchor = (x + w / 2, y + h * 0.4)
            faces.append(Face(
                tuple(int(round(value / scale)) for value in (x, y, w, h)),
                (anchor[0] / scale, anchor[1] / scale), bool(pairs)))
        return faces


def format_report(faces: list[Face], target: Face | None, advice: Advice,
                  available: bool = True) -> str:
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    if not available:
        return f"[{timestamp}] 画面读取失败 | 是否有人脸：未知 | 整体高度：保持 | 云台：保持"
    if target is None:
        return (f"[{timestamp}] 未检测到人脸 | 整体高度：保持 | 云台：保持 | "
                "无法判断目标方向，不建议盲目左右摇摆")
    lift = {"up": "向上升高", "down": "向下降低", "hold": "保持"}[advice.lift]
    pan = {"left": "向左转", "right": "向右转", "hold": "保持"}[advice.pan]
    reference = "双眼中点" if target.eyes_detected else "眼高估计（未检测到双眼）"
    return (f"[{timestamp}] 检测到人脸：{len(faces)} 张 | 整体高度：{lift} | 云台：{pan} | "
            f"参考：{reference} | 偏差：水平 {advice.dx:+.1%}，垂直 {advice.dy:+.1%}")


def draw_preview(cv2, frame, faces, target, advice, args):
    height, width = frame.shape[:2]
    cx, cy = width // 2, height // 2
    cv2.rectangle(frame,
                  (int(cx - width * args.deadzone_x), int(cy - height * args.deadzone_y)),
                  (int(cx + width * args.deadzone_x), int(cy + height * args.deadzone_y)),
                  (0, 180, 180), 1)
    cv2.drawMarker(frame, (cx, cy), (0, 180, 180), cv2.MARKER_CROSS, 16, 1)
    for face in faces:
        x, y, w, h = face.box
        color = (0, 255, 0) if face is target else (160, 160, 160)
        cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
        cv2.circle(frame, tuple(round(value) for value in face.anchor), 4, color, -1)
    # OpenCV's built-in font does not support Chinese; terminal output does.
    status = f"Faces: {len(faces)} | Lift: {advice.lift} | Pan: {advice.pan}"
    cv2.putText(frame, status, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    cv2.imshow("EyeLevel AgentCam - Q / Esc to quit", frame)


def open_camera(cv2, index: int, backend: str):
    backends = {"dshow": cv2.CAP_DSHOW, "msmf": cv2.CAP_MSMF, "auto": cv2.CAP_ANY}
    capture = cv2.VideoCapture(index, backends[backend])
    if not capture.isOpened():
        capture.release()
        raise RuntimeError(
            f"无法打开摄像头 {index}。请关闭占用相机的程序，检查 Windows 相机权限，"
            "或尝试 --camera 1 / --backend msmf / --backend auto。")
    return capture


def run(args, cv2) -> int:
    detector = Detector(cv2)
    capture = open_camera(cv2, args.camera, args.backend)
    try:
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        capture.set(cv2.CAP_PROP_FPS, 30)
        print(f"已打开摄像头 {args.camera}，每 {args.interval:g} 秒输出一次。"
              "左右以未镜像的相机画面为准；仅提供建议。Ctrl+C 退出。", flush=True)
        started = time.monotonic()
        next_report = started
        previous = None
        failures = 0
        while not args.duration or time.monotonic() - started < args.duration:
            ok, frame = capture.read()
            available = ok and frame is not None and frame.size > 0
            if available:
                failures = 0
                faces = detector.detect(frame)
                target = select_target(faces, previous)
                previous = target
                advice = get_advice(target, frame.shape[1], frame.shape[0],
                                    args.deadzone_x, args.deadzone_y)
            else:
                failures += 1
                faces, target, previous = [], None, None
                advice = Advice("hold", "hold")
            now = time.monotonic()
            if now >= next_report:
                print(format_report(faces, target, advice, available), flush=True)
                # Skip missed slots instead of emitting a burst of stale reports.
                next_report += (int((now - next_report) / args.interval) + 1) * args.interval
            if failures >= 10:
                raise RuntimeError("连续 10 次无法读取相机画面，已停止。请检查连接或其他程序占用。")
            if args.preview:
                if available:
                    draw_preview(cv2, frame, faces, target, advice, args)
                else:
                    # Never leave a stale frame showing a previous movement suggestion.
                    cv2.destroyAllWindows()
                if cv2.waitKey(1) & 0xFF in (27, ord("q"), ord("Q")):
                    break
                if available and cv2.getWindowProperty(
                        "EyeLevel AgentCam - Q / Esc to quit", cv2.WND_PROP_VISIBLE) < 1:
                    break
            if not available:
                time.sleep(0.1)
        return 0
    finally:
        capture.release()
        if args.preview:
            cv2.destroyAllWindows()


def positive_float(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("必须是大于 0 的有限数字")
    return number


def deadzone(value):
    number = float(value)
    if not math.isfinite(number) or not 0 <= number < 0.5:
        raise argparse.ArgumentTypeError("必须在 [0, 0.5) 范围内")
    return number


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="实时检测本地摄像头人脸，每 3 秒输出升降和云台建议。")
    parser.add_argument("--camera", type=int, default=0, help="摄像头编号，默认 0")
    parser.add_argument("--backend", choices=("auto", "dshow", "msmf"),
                        default="dshow" if sys.platform == "win32" else "auto")
    parser.add_argument("--interval", type=positive_float, default=3.0, help="输出间隔（秒）")
    parser.add_argument("--duration", type=positive_float, default=None, help="运行秒数，默认持续运行")
    parser.add_argument("--preview", action="store_true", help="显示检测预览窗口")
    parser.add_argument("--deadzone-x", type=deadzone, default=0.08, help="水平容差，占画面宽度的比例")
    parser.add_argument("--deadzone-y", type=deadzone, default=0.06, help="垂直容差，占画面高度的比例")
    args = parser.parse_args(argv)
    if args.camera < 0:
        parser.error("--camera 必须为非负整数")
    return args


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    try:
        import cv2
    except ImportError:
        print("缺少 OpenCV，请运行 python -m pip install -r requirements.txt", file=sys.stderr)
        return 1
    try:
        return run(args, cv2)
    except KeyboardInterrupt:
        print("\n已停止，相机已释放。", flush=True)
        return 0
    except (RuntimeError, cv2.error) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        if args.preview:
            print("预览不可用时可去掉 --preview；显示窗口需要 opencv-python 非 headless 版。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
