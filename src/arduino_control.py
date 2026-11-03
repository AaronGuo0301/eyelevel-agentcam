"""USB control for the UNO LED and bounded 28BYJ-48 bench jogs."""

import argparse
import re
import sys
import time

COMMANDS = {"ping": "PING", "on": "LED ON", "off": "LED OFF", "status": "STATUS",
            "cw": "MOVE 128", "ccw": "MOVE -128", "stop": "STOP", "motor": "MOTOR STATUS"}
REPLIES = {
    "ping": {"PONG AGENTCAM UNO 2"},
    "on": {"OK LED ON"},
    "off": {"OK LED OFF"},
    "status": {"STATUS LED ON", "STATUS LED OFF"},
    "cw": {"OK MOVE 128"},
    "ccw": {"OK MOVE -128"},
    "stop": {"OK STOP"},
}


class CommandRejected(RuntimeError):
    """The board rejected a command without losing protocol synchronization."""


def encode_command(command):
    if command in COMMANDS:
        return COMMANDS[command]
    if re.fullmatch(r"move [+-]?\d{1,3}", command):
        steps = int(command.split()[1])
        if 0 < abs(steps) <= 512:
            return f"MOVE {steps}"
    raise ValueError("使用 cw / ccw 小幅点动，或 move N（N 为 -512～512 的非零整数）。")


def exchange(board, command, timeout=3.0):
    wire_command = encode_command(command)
    board.write((wire_command + "\n").encode("ascii"))
    board.flush()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = board.read_until(b"\n", 128)
        if not raw:
            continue
        reply = raw.decode("ascii", errors="replace").strip()
        if reply == "READY AGENTCAM UNO 2":
            continue
        if reply.startswith("ERR "):
            explanation = {"ERR BUSY": "电机正在转动，请等它停止或先输入 stop。",
                           "ERR STEPS_RANGE": "步数必须为 -512～512 的非零整数。"}
            raise CommandRejected(explanation.get(reply, reply))
        if (reply in REPLIES.get(command, set())
                or (wire_command.startswith("MOVE ") and reply == "OK " + wire_command)
                or (command == "motor" and (reply == "MOTOR IDLE 0" or
                    re.fullmatch(r"MOTOR MOVING (0|[1-9]\d{0,2})", reply)
                    and 0 <= int(reply.split()[-1]) <= 512))):
            return reply
        raise RuntimeError(f"收到不符合协议的回复：{reply!r}。请先上传项目中的 UNO 固件。")
    raise RuntimeError("等待 Arduino 回复超时。请确认已上传本项目固件，串口正确，波特率为 115200。")


def smoke_test(board):
    print(exchange(board, "on"), flush=True)
    try:
        if exchange(board, "status") != "STATUS LED ON":
            raise RuntimeError("开灯后的状态校验失败")
        time.sleep(1)
    finally:
        print(exchange(board, "off"), flush=True)
    if exchange(board, "status") != "STATUS LED OFF":
        raise RuntimeError("关灯后的状态校验失败")
    print("PASS：握手、开灯、状态查询和关灯均收到正确回复。", flush=True)


def interactive(board):
    print("电机：cw / ccw 小幅点动，move N 指定半步数（最多 ±512），stop 停止，motor 查状态。\n"
          "灯光：on / off / status；ping 检查通信；q 停止、释放电机并退出。", flush=True)
    try:
        while True:
            command = input("Arduino> ").strip().lower()
            command = {"1": "on", "0": "off"}.get(command, command)
            if command in ("q", "quit", "exit"):
                break
            try:
                print(exchange(board, command), flush=True)
            except (ValueError, CommandRejected) as exc:
                print(exc, flush=True)
    finally:
        try:
            print(exchange(board, "stop"), flush=True)
        finally:
            print(exchange(board, "off"), flush=True)


def wait_for_motion(board, timeout=4.0):
    """Keep single-command mode connected until the jog completes."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if exchange(board, "motor") == "MOTOR IDLE 0":
            return
        time.sleep(0.1)
    raise RuntimeError("电机动作等待超时。")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="识别 Arduino 串口，控制板载 L 灯与步进电机点动。")
    parser.add_argument("--port", help="从 --list 结果选择端口，例如 COM3")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--list", action="store_true", help="列出串口，不打开设备")
    action.add_argument("--command", help='单条命令，例如 cw、stop 或 "move 128"')
    action.add_argument("--smoke-test", action="store_true", help="自动开灯、检查状态、关灯")
    action.add_argument("--interactive", action="store_true", help="交互控制（默认）")
    args = parser.parse_args(argv)
    if args.command:
        try:
            encode_command(args.command)
        except ValueError as exc:
            parser.error(str(exc))
    if not args.list and not args.port:
        parser.error("请指定 --port COM3，或用 --list 查看设备；程序不会猜测控制哪个串口。")
    try:
        import serial
        from serial.tools import list_ports
    except ImportError:
        print("缺少 pyserial，请先运行 VS Code 的 Arduino: Setup tools 任务。", file=sys.stderr)
        return 1
    if args.list:
        ports = list(list_ports.comports())
        if not ports:
            print("未发现串口。请检查 USB 数据线，并在设备管理器查看是否有未识别设备。")
            return 1
        for port in sorted(ports, key=lambda item: item.device):
            print(f"{port.device}: {port.description}\n  {port.hwid}")
        return 0
    try:
        with serial.Serial(args.port, 115200, timeout=0.5, write_timeout=2) as board:
            # UNO normally resets when the host opens the port.
            time.sleep(2)
            board.reset_input_buffer()
            print(f"{args.port}: {exchange(board, 'ping')}", flush=True)
            if args.smoke_test:
                smoke_test(board)
            elif args.command:
                if args.command != "ping":
                    if encode_command(args.command).startswith("MOVE "):
                        try:
                            print(exchange(board, args.command), flush=True)
                            wait_for_motion(board)
                        finally:
                            print(exchange(board, "stop"), flush=True)
                    else:
                        print(exchange(board, args.command), flush=True)
            else:
                interactive(board)
        return 0
    except (KeyboardInterrupt, EOFError):
        print("\n已退出。")
        return 0
    except (serial.SerialException, OSError, RuntimeError) as exc:
        print(f"错误：{exc}\n请关闭占用串口的监视器，确认设备连接和端口编号。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
