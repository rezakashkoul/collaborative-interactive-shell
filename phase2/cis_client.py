#!/usr/bin/env python3

import os
import sys
import socket
import select
import signal
import tty
import termios
import fcntl
import struct


ESCAPE_KEY = b"\x14"   # Ctrl-T
CTRL_C = b"\x03"       # Ctrl-C


class CISClient:
    def __init__(self, sock_path: str):
        self.sock_path = sock_path
        self.sock = None
        self.running = True

        self.stdin_fd = sys.stdin.fileno()
        self.stdout_fd = sys.stdout.fileno()
        self.old_termios = None

        self.command_mode = False
        self.command_buffer = ""

        self.role = "observer"
        self.resize_pending = False

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.sock_path)
        self.sock.setblocking(False)

    def enable_raw_mode(self) -> None:
        self.old_termios = termios.tcgetattr(self.stdin_fd)
        tty.setraw(self.stdin_fd)

    def restore_terminal(self) -> None:
        if self.old_termios is not None:
            termios.tcsetattr(self.stdin_fd, termios.TCSADRAIN, self.old_termios)
            self.old_termios = None

    def write_stdout(self, data: bytes) -> None:
        os.write(self.stdout_fd, data)

    def write_text(self, text: str) -> None:
        self.write_stdout(text.encode())

    def send_control(self, command: str) -> None:
        msg = f"CONTROL {command}\n"
        self.sock.sendall(msg.encode())

    def send_input_bytes(self, raw: bytes) -> None:
        if not raw:
            return
        msg = f"INPUT {raw.hex()}\n"
        self.sock.sendall(msg.encode())

    def get_terminal_size(self) -> tuple[int, int]:
        try:
            packed = fcntl.ioctl(self.stdin_fd, termios.TIOCGWINSZ, b"\x00" * 8)
            rows, cols, _, _ = struct.unpack("HHHH", packed)
            return rows, cols
        except Exception:
            return 0, 0

    def send_resize_if_possible(self) -> None:
        rows, cols = self.get_terminal_size()
        if rows > 0 and cols > 0:
            try:
                self.send_control(f"resize {rows} {cols}")
            except Exception:
                self.running = False

    def enter_command_mode(self) -> None:
        self.command_mode = True
        self.command_buffer = ""
        self.write_text("\r\n[CIS-CLIENT] command mode: ")

    def leave_command_mode(self) -> None:
        self.command_mode = False
        self.command_buffer = ""

    def handle_command_char(self, ch: bytes) -> None:
        if ch in (b"\r", b"\n"):
            cmd = self.command_buffer.strip()
            self.write_text("\r\n")

            if cmd == "":
                self.leave_command_mode()
                return

            if cmd == "quit":
                self.running = False
                self.leave_command_mode()
                return

            try:
                self.send_control(cmd)
            except Exception:
                self.running = False
                return

            self.leave_command_mode()
            return

        if ch in (b"\x7f", b"\x08"):
            if self.command_buffer:
                self.command_buffer = self.command_buffer[:-1]
                self.write_stdout(b"\b \b")
            return

        if ch in (ESCAPE_KEY, CTRL_C):
            self.write_text("\r\n[CIS-CLIENT] command canceled\r\n")
            self.leave_command_mode()
            return

        try:
            s = ch.decode("utf-8", errors="ignore")
        except Exception:
            return

        if s and s.isprintable():
            self.command_buffer += s
            self.write_stdout(ch)

    def handle_stdin(self) -> None:
        try:
            data = os.read(self.stdin_fd, 1024)
        except Exception:
            self.running = False
            return

        if not data:
            self.running = False
            return

        for ch_int in data:
            one = bytes([ch_int])

            if self.command_mode:
                self.handle_command_char(one)
                continue

            if one == ESCAPE_KEY:
                self.enter_command_mode()
                continue

            if one == CTRL_C:
                if self.role == "controller":
                    try:
                        self.send_input_bytes(one)
                    except Exception:
                        self.running = False
                    continue
                else:
                    self.write_text("\r\n[CIS-CLIENT] observer exiting on Ctrl-C\r\n")
                    self.running = False
                    continue

            try:
                self.send_input_bytes(one)
            except Exception:
                self.running = False
                return

    def update_role_from_text(self, text: str) -> None:
        previous_role = self.role

        if "[CIS] role=controller" in text:
            self.role = "controller"
        elif "[CIS] role=observer" in text:
            self.role = "observer"

        if self.role == "controller" and previous_role != "controller":
            self.resize_pending = True

    def handle_socket(self) -> None:
        try:
            data = self.sock.recv(4096)
        except BlockingIOError:
            return
        except Exception:
            self.running = False
            return

        if not data:
            self.running = False
            return

        try:
            decoded = data.decode("utf-8", errors="replace")
        except Exception:
            decoded = ""

        if decoded:
            self.update_role_from_text(decoded)

        normalized = data.replace(b"\r\n", b"\n")
        normalized = normalized.replace(b"\n", b"\r\n")
        self.write_stdout(normalized)

    def handle_pending_resize(self) -> None:
        if not self.resize_pending:
            return

        self.resize_pending = False

        if self.role != "controller":
            return

        self.send_resize_if_possible()

    def cleanup(self) -> None:
        self.restore_terminal()
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass

    def run(self) -> None:
        self.connect()
        self.enable_raw_mode()

        try:
            while self.running:
                self.handle_pending_resize()

                read_list = [self.sock, self.stdin_fd]
                readable, _, _ = select.select(read_list, [], [], 0.2)

                for obj in readable:
                    if obj == self.sock:
                        self.handle_socket()
                    elif obj == self.stdin_fd:
                        self.handle_stdin()
        finally:
            self.cleanup()


def main():
    if len(sys.argv) < 2:
        print(f"usage: {sys.argv[0]} ./cis.sock", file=sys.stderr)
        sys.exit(1)

    client = CISClient(sys.argv[1])

    def term_handler(signum, frame):
        client.running = False

    def winch_handler(signum, frame):
        client.resize_pending = True

    signal.signal(signal.SIGTERM, term_handler)
    signal.signal(signal.SIGWINCH, winch_handler)

    client.run()


if __name__ == "__main__":
    main()
