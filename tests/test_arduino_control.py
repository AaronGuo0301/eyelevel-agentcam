import contextlib
import io
import unittest
from unittest.mock import Mock, patch

from src.arduino_control import (CommandRejected, encode_command, exchange,
                                 interactive, smoke_test, wait_for_motion)


class SerialProtocolTests(unittest.TestCase):
    def test_handshake_ignores_startup_banner(self):
        board = Mock()
        board.read_until.side_effect = [b"READY AGENTCAM UNO 2\r\n", b"PONG AGENTCAM UNO 2\r\n"]
        self.assertEqual(exchange(board, "ping"), "PONG AGENTCAM UNO 2")
        board.write.assert_called_once_with(b"PING\n")

    def test_wrong_firmware_is_rejected(self):
        board = Mock()
        board.read_until.return_value = b"HELLO OTHER DEVICE\n"
        with self.assertRaisesRegex(RuntimeError, "不符合协议"):
            exchange(board, "ping")

    def test_timeout_is_reported(self):
        board = Mock()
        with patch("src.arduino_control.time.monotonic", side_effect=[0, 4]):
            with self.assertRaisesRegex(RuntimeError, "超时"):
                exchange(board, "ping")

    def test_led_command_requires_matching_ack(self):
        board = Mock()
        board.read_until.return_value = b"OK LED OFF\n"
        with self.assertRaisesRegex(RuntimeError, "不符合协议"):
            exchange(board, "on")

    def test_smoke_checks_state_and_switches_off(self):
        board = Mock()
        board.read_until.side_effect = [b"OK LED ON\n", b"STATUS LED ON\n",
                                       b"OK LED OFF\n", b"STATUS LED OFF\n"]
        with patch("src.arduino_control.time.sleep"), contextlib.redirect_stdout(io.StringIO()):
            smoke_test(board)
        self.assertEqual([call.args[0] for call in board.write.call_args_list],
                         [b"LED ON\n", b"STATUS\n", b"LED OFF\n", b"STATUS\n"])

    def test_failed_state_check_still_switches_off(self):
        board = Mock()
        board.read_until.side_effect = [b"OK LED ON\n", b"STATUS LED OFF\n", b"OK LED OFF\n"]
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "状态校验失败"):
                smoke_test(board)
        self.assertEqual(board.write.call_args.args, (b"LED OFF\n",))

    def test_move_bounds_and_command_injection(self):
        self.assertEqual(encode_command("move -512"), "MOVE -512")
        self.assertEqual(encode_command("move +128"), "MOVE 128")
        for value in ("move 0", "move 513", "move -513", "move 1.5", "move 1\nLED ON"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                encode_command(value)

    def test_busy_is_reported_as_rejection(self):
        board = Mock()
        board.read_until.return_value = b"ERR BUSY\n"
        with self.assertRaises(CommandRejected):
            exchange(board, "cw")

    def test_motion_status_limits(self):
        board = Mock()
        for reply in (b"MOTOR MOVING 512\n", b"MOTOR MOVING 0\n", b"MOTOR IDLE 0\n"):
            board.read_until.return_value = reply
            self.assertEqual(exchange(board, "motor"), reply.decode().strip())
        board.read_until.return_value = b"MOTOR MOVING 999\n"
        with self.assertRaises(RuntimeError):
            exchange(board, "motor")

    def test_move_ack_must_match_requested_steps(self):
        board = Mock()
        board.read_until.return_value = b"OK MOVE 256\n"
        with self.assertRaises(RuntimeError):
            exchange(board, "move 128")

    def test_wait_does_not_finish_during_final_dwell(self):
        board = Mock()
        board.read_until.side_effect = [b"MOTOR MOVING 0\n", b"MOTOR IDLE 0\n"]
        with patch("src.arduino_control.time.sleep"):
            wait_for_motion(board)
        self.assertEqual(board.write.call_count, 2)

    def test_interactive_exit_sends_stop(self):
        board = Mock()
        board.read_until.side_effect = [b"OK STOP\n", b"OK LED OFF\n"]
        with patch("builtins.input", return_value="q"), contextlib.redirect_stdout(io.StringIO()):
            interactive(board)
        self.assertEqual([call.args[0] for call in board.write.call_args_list],
                         [b"STOP\n", b"LED OFF\n"])


if __name__ == "__main__":
    unittest.main()
