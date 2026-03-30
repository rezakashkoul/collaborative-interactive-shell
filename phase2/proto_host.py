#!/usr/bin/env python3
import os
import pty
import sys
import socket
import selectors
import fcntl
import errno
import signal

BUF = 8192

def set_nonblocking(fd: int):
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

def spawn_shell(shell_path: str):
    master_fd, slave_fd = pty.openpty()

    pid = os.fork()
    if pid == 0:
        # Child: become session leader and attach slave as controlling TTY
        os.setsid()
        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd, 2)
        try:
            os.close(master_fd)
        except OSError:
            pass
        try:
            os.close(slave_fd)
        except OSError:
            pass

        # Interactive shell
        os.execv(shell_path, [os.path.basename(shell_path), "-i"])

    # Parent
    os.close(slave_fd)
    set_nonblocking(master_fd)
    return pid, master_fd

def safe_send(sock: socket.socket, data: bytes) -> bool:
    try:
        sock.sendall(data)
        return True
    except (BrokenPipeError, ConnectionResetError, OSError):
        return False

def main():
    sock_path = sys.argv[1] if len(sys.argv) >= 2 else "/tmp/cis.sock"
    shell_path = sys.argv[2] if len(sys.argv) >= 3 else "/bin/bash"

    # Cleanup old socket file
    try:
        os.unlink(sock_path)
    except FileNotFoundError:
        pass

    # Spawn bash in PTY
    child_pid, pty_master = spawn_shell(shell_path)

    # Create Unix domain socket server
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    os.chmod(sock_path, 0o660)
    srv.listen(32)
    srv.setblocking(False)

    sel = selectors.DefaultSelector()
    sel.register(srv, selectors.EVENT_READ, data=("server", None))

    # Register PTY master via fileobj integer fd
    sel.register(pty_master, selectors.EVENT_READ, data=("pty", None))

    clients = []  # list of sockets
    controller = None  # socket

    def broadcast(data: bytes):
        nonlocal clients, controller
        dead = []
        for c in clients:
            if not safe_send(c, data):
                dead.append(c)
        for c in dead:
            try:
                sel.unregister(c)
            except Exception:
                pass
            try:
                c.close()
            except Exception:
                pass
            if c in clients:
                clients.remove(c)
            if c is controller:
                controller = None

    def promote_controller():
        nonlocal controller
        if controller is not None:
            return
        if clients:
            controller = clients[0]
            safe_send(controller, b"\r\n[CIS] You are CONTROLLER now.\r\n")
            for c in clients[1:]:
                safe_send(c, b"\r\n[CIS] You are OBSERVER (read-only).\r\n")

    def drop_client(c: socket.socket):
        nonlocal controller, clients
        try:
            sel.unregister(c)
        except Exception:
            pass
        try:
            c.close()
        except Exception:
            pass
        if c in clients:
            clients.remove(c)
        was_controller = (c is controller)
        if was_controller:
            controller = None
            broadcast(b"\r\n[CIS] Controller disconnected. Promoting next client.\r\n")
            promote_controller()

    def shutdown(signum=None, frame=None):
        try:
            broadcast(b"\r\n[CIS] Host shutting down.\r\n")
        except Exception:
            pass
        try:
            sel.unregister(srv)
        except Exception:
            pass
        try:
            srv.close()
        except Exception:
            pass
        try:
            os.close(pty_master)
        except Exception:
            pass
        try:
            os.unlink(sock_path)
        except Exception:
            pass
        try:
            os.kill(child_pid, signal.SIGHUP)
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print(f"[CIS] host started")
    print(f"[CIS] socket: {sock_path}")
    print(f"[CIS] shell : {shell_path}")
    print(f"[CIS] child pid: {child_pid}")

    # Main event loop
    while True:
        events = sel.select(timeout=None)
        for key, mask in events:
            kind, _ = key.data

            if kind == "server":
                conn, _ = srv.accept()
                conn.setblocking(False)
                clients.append(conn)
                sel.register(conn, selectors.EVENT_READ, data=("client", conn))

                if controller is None:
                    controller = conn
                    safe_send(conn, b"[CIS] Connected. You are CONTROLLER.\r\n")
                else:
                    safe_send(conn, b"[CIS] Connected. You are OBSERVER (read-only).\r\n")

                safe_send(conn, b"[CIS] Tip: open another terminal and connect again to be an observer.\r\n")
                safe_send(conn, b"[CIS] -----------------------------------------------\r\n")

            elif kind == "pty":
                try:
                    data = os.read(pty_master, BUF)
                except OSError as e:
                    if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                        continue
                    shutdown()
                if not data:
                    broadcast(b"\r\n[CIS] Shell exited.\r\n")
                    shutdown()
                broadcast(data)

            elif kind == "client":
                c = key.fileobj
                try:
                    data = c.recv(BUF)
                except (ConnectionResetError, OSError):
                    drop_client(c)
                    continue

                if not data:
                    drop_client(c)
                    continue

                # Only controller input goes to PTY
                if c is controller:
                    try:
                        os.write(pty_master, data)
                    except OSError:
                        shutdown()
                else:
                    # Ignore observer input (keep system simple)
                    pass

if __name__ == "__main__":
    main()
