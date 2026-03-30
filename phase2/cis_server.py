#!/usr/bin/env python3

import os
import sys
import pty
import fcntl
import signal
import socket
import select
import termios
import struct
from dataclasses import dataclass, field


@dataclass
class Client:
    sock: socket.socket
    cid: int
    name: str
    role: str
    pending_request: bool = False
    recv_buffer: bytes = field(default_factory=bytes)


class CISServer:
    def __init__(self, sock_path: str, shell: str = "/bin/bash"):
        self.sock_path = sock_path
        self.shell = shell

        self.server_sock = None
        self.pty_master = None
        self.child_pid = None

        self.clients: dict[int, Client] = {}
        self.next_client_id = 1
        self.controller_id = None
        self.request_queue: list[int] = []

        self.running = True

    def log(self, msg: str) -> None:
        print(f"[CIS] {msg}", flush=True)

    def start_shell(self) -> None:
        pid, fd = pty.fork()

        if pid == 0:
            os.execvp(self.shell, [self.shell])

        self.child_pid = pid
        self.pty_master = fd

        flags = fcntl.fcntl(self.pty_master, fcntl.F_GETFL)
        fcntl.fcntl(self.pty_master, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        self.log(f"shell started: pid={self.child_pid}, shell={self.shell}")

    def start_socket(self) -> None:
        if os.path.exists(self.sock_path):
            os.unlink(self.sock_path)

        self.server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server_sock.bind(self.sock_path)
        self.server_sock.listen()
        self.server_sock.setblocking(False)

        self.log(f"listening on socket: {self.sock_path}")

    def send_text(self, sock: socket.socket, text: str) -> None:
        try:
            sock.sendall(text.encode())
        except Exception:
            pass

    def send_client(self, cid: int, text: str) -> None:
        client = self.clients.get(cid)
        if client is not None:
            self.send_text(client.sock, text)

    def broadcast_bytes(self, data: bytes) -> None:
        dead = []

        for cid, client in self.clients.items():
            try:
                client.sock.sendall(data)
            except Exception:
                dead.append(cid)

        for cid in dead:
            self.remove_client(cid)

    def broadcast_text(self, text: str) -> None:
        self.broadcast_bytes(text.encode())

    def update_roles(self) -> None:
        for cid, client in self.clients.items():
            if cid == self.controller_id:
                client.role = "controller"
                self.send_text(
                    client.sock,
                    f"\n[CIS] role=controller id={client.cid} name={client.name}\n"
                )
            else:
                client.role = "observer"
                self.send_text(
                    client.sock,
                    f"\n[CIS] role=observer id={client.cid} name={client.name}\n"
                )

    def queue_snapshot(self) -> str:
        if not self.request_queue:
            return "[]"
        return "[" + ", ".join(str(cid) for cid in self.request_queue) + "]"

    def pop_next_controller(self):
        while self.request_queue:
            cid = self.request_queue.pop(0)
            client = self.clients.get(cid)
            if client is None:
                continue
            client.pending_request = False
            return cid
        return None

    def assign_controller(self, cid):
        self.controller_id = cid
        self.update_roles()

        if cid is None:
            self.broadcast_text("\n[CIS] no controller currently assigned\n")
            self.log("no controller currently assigned")
            return

        client = self.clients.get(cid)
        if client is not None:
            self.broadcast_text(
                f"\n[CIS] controller granted to id={client.cid} name={client.name}\n"
            )
            self.log(f"controller granted: id={client.cid} name={client.name}")

    def apply_resize(self, cid: int, rows: int, cols: int) -> None:
        if cid != self.controller_id:
            self.send_client(cid, "[CIS] only controller may resize the shared PTY\n")
            return

        if rows <= 0 or cols <= 0:
            self.send_client(cid, "[CIS] invalid resize dimensions\n")
            return

        try:
            winsz = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.pty_master, termios.TIOCSWINSZ, winsz)
        except Exception:
            self.send_client(cid, "[CIS] failed to apply resize\n")
            self.log(f"failed resize from id={cid} rows={rows} cols={cols}")
            return

        self.log(f"resize applied by id={cid}: rows={rows} cols={cols}")

    def maybe_grant_next_controller(self) -> None:
        if self.controller_id is not None and self.controller_id in self.clients:
            return

        next_cid = self.pop_next_controller()
        self.assign_controller(next_cid)

    def accept_client(self) -> None:
        sock, _ = self.server_sock.accept()
        sock.setblocking(False)

        cid = self.next_client_id
        self.next_client_id += 1

        client = Client(
            sock=sock,
            cid=cid,
            name=f"user{cid}",
            role="observer",
        )
        self.clients[cid] = client

        self.send_text(sock, "[CIS] connected\n")
        self.send_text(sock, f"[CIS] your id={cid}, name={client.name}\n")
        self.send_text(sock, "[CIS] shell input works only for controller\n")
        self.send_text(
            sock,
            "[CIS] control commands: request release cancel who name NEWNAME kick ID resize ROWS COLS\n"
        )

        if self.controller_id is None:
            self.assign_controller(cid)
        else:
            self.update_roles()

        self.broadcast_text(f"[CIS] client joined: id={cid} name={client.name}\n")
        self.log(f"client joined: id={cid} name={client.name}")

    def handle_control_command(self, cid: int, command_line: str) -> None:
        client = self.clients.get(cid)
        if client is None:
            return

        parts = command_line.strip().split(maxsplit=1)
        if not parts:
            return

        cmd = parts[0].lower()

        if cmd == "request":
            if cid == self.controller_id:
                self.send_client(cid, "[CIS] you already have control\n")
                return

            if client.pending_request:
                self.send_client(cid, "[CIS] request already pending\n")
                return

            client.pending_request = True
            self.request_queue.append(cid)

            self.broadcast_text(
                f"[CIS] request queued: id={cid} name={client.name} queue={self.queue_snapshot()}\n"
            )
            self.log(
                f"request queued: id={cid} name={client.name} queue={self.queue_snapshot()}"
            )

            if self.controller_id is None:
                self.maybe_grant_next_controller()

        elif cmd == "release":
            if cid != self.controller_id:
                self.send_client(cid, "[CIS] you are not controller\n")
                return

            self.broadcast_text(
                f"[CIS] controller released: id={cid} name={client.name}\n"
            )
            self.log(f"controller released: id={cid} name={client.name}")

            self.controller_id = None
            self.maybe_grant_next_controller()

        elif cmd == "cancel":
            if not client.pending_request:
                self.send_client(cid, "[CIS] no pending request to cancel\n")
                return

            client.pending_request = False
            self.request_queue = [x for x in self.request_queue if x != cid]

            self.broadcast_text(
                f"[CIS] request canceled: id={cid} name={client.name} queue={self.queue_snapshot()}\n"
            )
            self.log(
                f"request canceled: id={cid} name={client.name} queue={self.queue_snapshot()}"
            )

        elif cmd == "who":
            lines = ["[CIS] participants:\n"]
            for xcid, xclient in self.clients.items():
                flag = ""
                if xcid == self.controller_id:
                    flag += " controller"
                if xclient.pending_request:
                    flag += " pending"

                lines.append(
                    f"  id={xclient.cid} name={xclient.name} role={xclient.role}{flag}\n"
                )

            lines.append(f"[CIS] queue={self.queue_snapshot()}\n")
            self.send_client(cid, "".join(lines))

        elif cmd == "name":
            if len(parts) < 2 or not parts[1].strip():
                self.send_client(cid, "[CIS] usage: name NEWNAME\n")
                return

            old_name = client.name
            client.name = parts[1].strip()

            self.broadcast_text(
                f"[CIS] client renamed: id={cid} old={old_name} new={client.name}\n"
            )
            self.log(f"client renamed: id={cid} old={old_name} new={client.name}")

            self.update_roles()

        elif cmd == "kick":
            if cid != self.controller_id:
                self.send_client(cid, "[CIS] only controller may kick clients\n")
                return

            if len(parts) < 2 or not parts[1].strip():
                self.send_client(cid, "[CIS] usage: kick ID\n")
                return

            arg = parts[1].strip()
            try:
                target_id = int(arg)
            except ValueError:
                self.send_client(cid, "[CIS] invalid client id\n")
                return

            if target_id == cid:
                self.send_client(cid, "[CIS] you cannot kick yourself\n")
                return

            if target_id not in self.clients:
                self.send_client(cid, "[CIS] target client does not exist\n")
                return

            target = self.clients[target_id]
            self.broadcast_text(
                f"[CIS] client kicked: id={target.cid} name={target.name} by id={cid}\n"
            )
            self.log(
                f"client kicked: id={target.cid} name={target.name} by id={cid}"
            )
            self.remove_client(target_id)

        elif cmd == "resize":
            if len(parts) < 2 or not parts[1].strip():
                self.send_client(cid, "[CIS] usage: resize ROWS COLS\n")
                return

            dims = parts[1].strip().split()
            if len(dims) != 2:
                self.send_client(cid, "[CIS] usage: resize ROWS COLS\n")
                return

            try:
                rows = int(dims[0])
                cols = int(dims[1])
            except ValueError:
                self.send_client(cid, "[CIS] resize arguments must be integers\n")
                return

            self.apply_resize(cid, rows, cols)

        else:
            self.send_client(cid, f"[CIS] unknown control command: {command_line}\n")

    def handle_protocol_line(self, cid: int, line: bytes) -> None:
        client = self.clients.get(cid)
        if client is None:
            return

        try:
            decoded = line.decode("utf-8", errors="replace").rstrip("\n")
        except Exception:
            self.send_client(cid, "[CIS] invalid protocol line\n")
            return

        if decoded.startswith("CONTROL "):
            command = decoded[len("CONTROL "):]
            self.handle_control_command(cid, command)
            return

        if decoded.startswith("INPUT "):
            if cid != self.controller_id:
                self.send_client(cid, "[CIS] read-only: you are observer\n")
                return

            hex_data = decoded[len("INPUT "):].strip()

            try:
                raw = bytes.fromhex(hex_data)
            except ValueError:
                self.send_client(cid, "[CIS] invalid INPUT payload\n")
                return

            try:
                os.write(self.pty_master, raw)
            except Exception:
                self.log("failed to write controller input to PTY")
            return

        self.send_client(cid, "[CIS] unknown protocol message\n")

    def handle_client_input(self, cid: int) -> None:
        client = self.clients.get(cid)
        if client is None:
            return

        try:
            data = client.sock.recv(4096)
        except BlockingIOError:
            return
        except Exception:
            self.remove_client(cid)
            return

        if not data:
            self.remove_client(cid)
            return

        client.recv_buffer += data

        while b"\n" in client.recv_buffer:
            line, client.recv_buffer = client.recv_buffer.split(b"\n", 1)
            line = line + b"\n"
            self.handle_protocol_line(cid, line)

    def handle_pty_output(self) -> None:
        try:
            data = os.read(self.pty_master, 4096)
        except BlockingIOError:
            return
        except OSError:
            self.running = False
            return

        if not data:
            self.running = False
            return

        self.broadcast_bytes(data)

    def remove_from_queue(self, cid: int) -> None:
        self.request_queue = [x for x in self.request_queue if x != cid]
        client = self.clients.get(cid)
        if client is not None:
            client.pending_request = False

    def remove_client(self, cid: int) -> None:
        client = self.clients.pop(cid, None)
        if client is None:
            return

        try:
            client.sock.close()
        except Exception:
            pass

        was_controller = cid == self.controller_id
        was_pending = client.pending_request

        self.remove_from_queue(cid)

        self.broadcast_text(f"\n[CIS] client left: id={cid} name={client.name}\n")
        self.log(f"client left: id={cid} name={client.name}")

        if was_pending:
            self.broadcast_text(
                f"[CIS] removed pending request for id={cid} queue={self.queue_snapshot()}\n"
            )
            self.log(f"removed pending request: id={cid} queue={self.queue_snapshot()}")

        if was_controller:
            self.controller_id = None
            self.broadcast_text(
                f"[CIS] controller disconnected: id={cid} name={client.name}\n"
            )
            self.log(f"controller disconnected: id={cid} name={client.name}")
            self.maybe_grant_next_controller()
        else:
            self.update_roles()

    def check_shell_exit(self) -> None:
        if self.child_pid is None:
            return

        pid, _ = os.waitpid(self.child_pid, os.WNOHANG)
        if pid == self.child_pid:
            self.log("shell exited")
            self.running = False

    def cleanup(self) -> None:
        self.log("cleanup started")

        for cid in list(self.clients.keys()):
            self.remove_client(cid)

        if self.server_sock is not None:
            try:
                self.server_sock.close()
            except Exception:
                pass

        if os.path.exists(self.sock_path):
            try:
                os.unlink(self.sock_path)
            except Exception:
                pass

        if self.child_pid is not None:
            try:
                os.kill(self.child_pid, signal.SIGHUP)
            except ProcessLookupError:
                pass
            except Exception:
                pass

            try:
                os.waitpid(self.child_pid, 0)
            except Exception:
                pass

        if self.pty_master is not None:
            try:
                os.close(self.pty_master)
            except Exception:
                pass

        self.log("cleanup finished")

    def run(self) -> None:
        self.start_shell()
        self.start_socket()

        while self.running:
            self.check_shell_exit()

            read_list = []

            if self.server_sock is not None:
                read_list.append(self.server_sock)

            if self.pty_master is not None:
                read_list.append(self.pty_master)

            for client in self.clients.values():
                read_list.append(client.sock)

            if not read_list:
                break

            try:
                readable, _, _ = select.select(read_list, [], [], 0.2)
            except InterruptedError:
                continue

            for obj in readable:
                if obj is self.server_sock:
                    self.accept_client()
                elif obj == self.pty_master:
                    self.handle_pty_output()
                else:
                    target_cid = None
                    for xcid, client in self.clients.items():
                        if client.sock is obj:
                            target_cid = xcid
                            break
                    if target_cid is not None:
                        self.handle_client_input(target_cid)

        self.cleanup()


def main():
    if len(sys.argv) < 2:
        print(f"usage: {sys.argv[0]} ./cis.sock [shell]", file=sys.stderr)
        sys.exit(1)

    sock_path = sys.argv[1]
    shell = sys.argv[2] if len(sys.argv) >= 3 else "/bin/bash"

    server = CISServer(sock_path, shell)

    def stop_handler(signum, frame):
        server.running = False

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    server.run()


if __name__ == "__main__":
    main()
