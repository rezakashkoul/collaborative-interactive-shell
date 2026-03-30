# Collaborative Interactive Shell (CIS)

This repository contains both **Phase 1** and **Phase 2** of the Collaborative Interactive Shell (CIS) project.

Collaborative Interactive Shell (CIS) is a shared terminal environment in which multiple already-authenticated users on the same machine can attach to the same shell session, observe the same live terminal output, and coordinate control over shell input.

At any given time, exactly one client acts as the **Controller**, while all other clients act as **Observers**. The Controller’s input is forwarded to the shared shell. Observers can see the same live output, but their ordinary shell input does not reach the PTY.

The project evolves in two stages. **Phase 1** focuses on design, architectural direction, and validation experiments. **Phase 2** turns that design into a working implementation with demonstration artifacts, traces, and documentation.

---

## Repository Structure

The repository is organized into three main parts.

### `guidelines/`

This directory contains the CIS project specification and implementation scope.

### `phase1/`

This directory contains the Phase 1 design and validation materials, including:

- the proposal document
- the prototype host
- the latency experiment script
- the robustness experiment script
- appendix files for commands, latency, robustness, and system information

### `phase2/`

This directory contains the final implementation and supporting artifacts, including:

- `cis_server.py`
- `cis_client.py`
- the Phase 2 implementation report
- the demo transcript
- the postmortem
- trace files
- appendix files
- the Phase 2 README

---

## Foundation

This project is built around:

- an existing shell: `/bin/bash`
- PTY-backed interactive shell execution
- Unix domain socket communication on the same machine
- a single-threaded server/client collaborative control design

This approach preserves real interactive shell behavior while keeping the implementation focused on the collaboration layer rather than building a shell from scratch.

---

## Phase 1

Phase 1 establishes the design direction and validates the approach.

It includes:

- the proposal document
- the initial PTY-based architecture
- prototype experimentation
- latency measurements
- robustness validation
- appendix artifacts supporting the proposal

The major design choices introduced in Phase 1 include:

- existing shell foundation
- PTY-backed execution
- controller / observer role separation
- FIFO floor-control policy
- reproducible experiments
- emphasis on operating-system mechanisms and terminal correctness

---

## Phase 2

Phase 2 turns the original design into a working collaborative shell system.

Main implementation features include:

- host starts one shared interactive shell
- multiple clients attach to the same shell session
- shell output is broadcast to all clients in real time
- exactly one controller at a time
- observers are read-only with respect to ordinary shell input
- FIFO floor-control policy
- one outstanding request per client
- request / release / cancel / who / name / kick commands
- controller disconnect handling
- observer detach and fresh reconnect
- server shutdown cleanup
- controller-driven PTY resize propagation

---

## Protocol Design

One major implementation refinement in Phase 2 is the explicit separation between **control commands** and **ordinary shell input**.

Control actions are sent separately from shell-input bytes. This avoids mixing collaboration control with interactive shell typing and improves terminal correctness.

The Phase 2 client uses a simple command mode entered with `Ctrl-T`.

Supported command-mode commands are:

- `request`
- `release`
- `cancel`
- `who`
- `name NEWNAME`
- `kick ID`
- `quit`

---

## Floor-Control Policy

The collaboration model supports two roles:

- **Controller**: shell input reaches the PTY
- **Observer**: shell input does not reach the PTY

The fairness policy is **FIFO**.

Rules:

- Observers may request control.
- Each client may have at most one outstanding request.
- Duplicate pending requests are ignored.
- When the Controller releases control, the first queued requester becomes Controller.
- When the Controller disconnects, the first queued requester becomes Controller.
- If the queue is empty, the session remains alive with no Controller assigned.

An outstanding request is a request that has been registered but has not yet been granted, canceled, or removed because of disconnect.

---

## OS Mechanisms Used

The implementation relies on operating-system primitives including:

- PTYs for running an interactive shell
- `fork/exec` to launch the shell
- `waitpid` for child cleanup
- signals for shutdown and terminal behavior
- `select()`-based I/O multiplexing
- terminal raw mode handling on the client
- resize propagation through `SIGWINCH` and PTY window-size updates

The overall design uses a single-threaded event loop for simplicity and determinism.

---

## Failure Handling

The implementation handles these behaviors explicitly:

- controller disconnect during an active session
- duplicate request rejection
- observer exit without disrupting the shell
- reconnect as a fresh client connection
- server shutdown with cleanup of sockets, PTY resources, and child process state

Reconnect is supported functionally, but identity-preserving reattachment is not implemented.

---

## Current Limitations

The implementation intentionally focuses on the core collaborative-shell behavior.

Not fully implemented:

- identity-preserving reconnect
- persistent ban policy
- advanced multi-client resize arbitration beyond the controller-owned PTY size
- remote network fault handling beyond local disconnect/reconnect semantics

---

## Build and Run

The final implementation is located in `phase2/`.

Typical usage:

### Start the server

```bash
cd /home/rkashko/os/project1
rm -f cis.sock
python3 cis_server.py ./cis.sock
```

### Start a client

```bash
cd /home/rkashko/os/project1
python3 cis_client.py ./cis.sock
```

See `phase2/README.md` for the full Phase 2 implementation details and usage notes.

---

## Artifacts

This repository includes documentation and evidence across both phases, including:

- project specification / guideline
- Phase 1 proposal
- Phase 2 implementation report
- source code
- README files
- demo transcript
- appendix files
- trace / log files
- postmortem

---

## Notes

- ZIP archives are intentionally excluded from this repository.
- Generated PowerPoint files are intentionally excluded from this repository.
- The repository keeps both Phase 1 and Phase 2 artifacts for continuity between design and implementation.

---

## Summary

Collaborative Interactive Shell (CIS) is a systems-oriented project that combines interactive shell execution, PTY-based terminal behavior, multi-client coordination, floor-control policy, fairness enforcement, disconnect handling, resize handling, cleanup, and documentation.

The final implementation remains aligned with the original design direction while improving protocol clarity, robustness, and practical usability.
