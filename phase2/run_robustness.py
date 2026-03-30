#!/usr/bin/env python3
import os
import socket
import time
from datetime import datetime, timezone

def iso_now():
    return datetime.now(timezone.utc).isoformat()

def connect_unix(sock_path: str) -> socket.socket:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(sock_path)
    return s

def main():
    sock_path = "./cis.sock"
    outdir = "appendix"
    os.makedirs(outdir, exist_ok=True)
    log_path = os.path.join(outdir, "robustness.txt")

    # Command: produce lots of output for a bit, then stop naturally
    # Using yes | head so it terminates, but long enough to disconnect mid-stream.
    cmd = "yes CIS_ROBUST | head -n 200000 > /dev/null; echo ROBUST_DONE\n"

    with open(log_path, "w", encoding="utf-8") as f:
        f.write("Robustness experiment: controller disconnect mid-command\n")
        f.write(f"generated_at_utc: {iso_now()}\n")
        f.write(f"socket: {sock_path}\n")
        f.write("scenario:\n")
        f.write("  1) connect as controller\n")
        f.write("  2) start a long-ish command\n")
        f.write("  3) disconnect controller while command still running\n")
        f.write("expected:\n")
        f.write("  - host stays up\n")
        f.write("  - session continues (shell remains alive)\n")
        f.write("  - next client can become controller\n\n")

        # Connect as controller
        t0 = iso_now()
        f.write(f"{t0}  controller_connect\n")
        s = connect_unix(sock_path)

        # Send command
        t1 = iso_now()
        f.write(f"{t1}  send_command: {cmd.strip()}\n")
        s.sendall(cmd.encode("utf-8"))

        # Wait a short moment to ensure command started
        time.sleep(0.05)

        # Disconnect controller abruptly
        t2 = iso_now()
        f.write(f"{t2}  controller_disconnect (close socket)\n")
        try:
            s.close()
        except Exception:
            pass

        f.write("\nNOTE: To validate session survival, connect from another terminal as observer/controller and run `echo STILL_ALIVE`.\n")

    print(f"[OK] wrote {log_path}")

if __name__ == "__main__":
    main()
