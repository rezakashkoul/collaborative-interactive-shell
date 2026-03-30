#!/usr/bin/env python3
import argparse
import os
import socket
import selectors
import time
from datetime import datetime, timezone
from statistics import mean, median

BUF = 8192

def iso_now():
    return datetime.now(timezone.utc).isoformat()

def connect_unix(sock_path: str) -> socket.socket:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(sock_path)
    s.setblocking(False)
    return s

def percentile(sorted_vals, p):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    d0 = sorted_vals[f] * (c - k)
    d1 = sorted_vals[c] * (k - f)
    return d0 + d1

def summarize(latencies_ms):
    vals = sorted(latencies_ms)
    return {
        "count": len(vals),
        "min_ms": vals[0] if vals else None,
        "median_ms": median(vals) if vals else None,
        "mean_ms": mean(vals) if vals else None,
        "p95_ms": percentile(vals, 95),
        "max_ms": vals[-1] if vals else None,
    }

def run_for_N(sock_path: str, N: int, trials: int, timeout_s: float, csv_path: str):
    # One controller + N observers
    controller = connect_unix(sock_path)
    observers = [connect_unix(sock_path) for _ in range(N)]
    clients = [("controller", 0, controller)] + [("observer", i+1, observers[i]) for i in range(N)]

    sel = selectors.DefaultSelector()
    for role, idx, s in clients:
        sel.register(s, selectors.EVENT_READ, data=(role, idx))

    # Drain any initial banners
    drain_deadline = time.time() + 0.2
    bufs = {s: b"" for _, _, s in clients}
    while time.time() < drain_deadline:
        events = sel.select(timeout=0.05)
        for key, _ in events:
            s = key.fileobj
            try:
                chunk = s.recv(BUF)
            except BlockingIOError:
                continue
            if not chunk:
                continue
            bufs[s] += chunk
            if len(bufs[s]) > 32768:
                bufs[s] = bufs[s][-32768:]

    latencies_ms_by_role = {("controller",0): [], **{("observer",i+1): [] for i in range(N)}}

    with open(csv_path, "a", encoding="utf-8") as f:
        for t in range(trials):
            marker = f"__CISMARK_N{N}_T{t}_{time.time_ns()}__"
            cmd = f"echo {marker}\n".encode("utf-8")

            # reset buffers for this trial (keep small tail safety)
            for _, _, s in clients:
                bufs[s] = b""

            t_send = time.perf_counter_ns()
            controller.sendall(cmd)

            seen = {}
            deadline = time.time() + timeout_s
            marker_b = marker.encode("utf-8")

            while time.time() < deadline and len(seen) < len(clients):
                events = sel.select(timeout=0.05)
                if not events:
                    continue
                for key, _ in events:
                    s = key.fileobj
                    role, idx = key.data
                    try:
                        chunk = s.recv(BUF)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        # disconnected
                        continue
                    bufs[s] += chunk
                    if len(bufs[s]) > 65536:
                        bufs[s] = bufs[s][-65536:]

                    if (role, idx) not in seen and marker_b in bufs[s]:
                        t_recv = time.perf_counter_ns()
                        lat_ms = (t_recv - t_send) / 1_000_000.0
                        seen[(role, idx)] = (t_recv, lat_ms)

            # Write rows
            ts = iso_now()
            for role, idx, _s in clients:
                key = (role, idx)
                if key in seen:
                    t_recv, lat_ms = seen[key]
                    latencies_ms_by_role[key].append(lat_ms)
                    status = "ok"
                else:
                    t_recv, lat_ms = ("", "")
                    status = "timeout"
                f.write(f"{ts},{N},{t},{role},{idx},{t_send},{t_recv},{lat_ms},{status}\n")

    # Close
    for _, _, s in clients:
        try:
            sel.unregister(s)
        except Exception:
            pass
        try:
            s.close()
        except Exception:
            pass

    return latencies_ms_by_role

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sock", required=True, help="Unix socket path (e.g., ./cis.sock)")
    ap.add_argument("--trials", type=int, default=50)
    ap.add_argument("--timeout", type=float, default=2.0)
    ap.add_argument("--Ns", default="1,2,4", help="comma-separated observer counts")
    ap.add_argument("--outdir", default="appendix")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    csv_path = os.path.join(args.outdir, "latency_raw.csv")
    txt_path = os.path.join(args.outdir, "latency.txt")

    # CSV header (only if file is new/empty)
    if not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0:
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("timestamp_utc,N_obs,trial,role,client_index,t_send_ns,t_recv_ns,latency_ms,status\n")

    Ns = [int(x.strip()) for x in args.Ns.split(",") if x.strip()]

    all_summaries = []
    for N in Ns:
        lat_by_role = run_for_N(args.sock, N, args.trials, args.timeout, csv_path)

        # summarize observers only + controller separately
        ctrl_vals = lat_by_role.get(("controller",0), [])
        obs_vals = []
        for i in range(1, N+1):
            obs_vals.extend(lat_by_role.get(("observer", i), []))

        s_ctrl = summarize(ctrl_vals)
        s_obs = summarize(obs_vals)

        all_summaries.append((N, s_ctrl, s_obs))

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("Latency experiment summary (echo marker)\n")
        f.write(f"socket: {args.sock}\n")
        f.write(f"trials per N: {args.trials}\n")
        f.write(f"timeout per trial: {args.timeout}s\n")
        f.write(f"generated_at_utc: {iso_now()}\n\n")
        for N, s_ctrl, s_obs in all_summaries:
            f.write(f"N_obs={N}\n")
            f.write(f"  controller: count={s_ctrl['count']} min={s_ctrl['min_ms']:.3f} med={s_ctrl['median_ms']:.3f} mean={s_ctrl['mean_ms']:.3f} p95={s_ctrl['p95_ms']:.3f} max={s_ctrl['max_ms']:.3f}\n")
            f.write(f"  observers : count={s_obs['count']} min={s_obs['min_ms']:.3f} med={s_obs['median_ms']:.3f} mean={s_obs['mean_ms']:.3f} p95={s_obs['p95_ms']:.3f} max={s_obs['max_ms']:.3f}\n\n")

    print(f"[OK] wrote {csv_path}")
    print(f"[OK] wrote {txt_path}")

if __name__ == "__main__":
    main()
