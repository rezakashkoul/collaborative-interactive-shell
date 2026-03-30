# Collaborative Interactive Shell (CIS)

Phase 2 implementation of a collaborative interactive shell for multiple already-authenticated users on the same machine.

## Overview

This project implements a shared shell session where multiple clients attach to one interactive shell running inside a PTY.

At any time:

- exactly one client is the **Controller**
- all other clients are **Observers**

The Controller’s shell input is forwarded to the shared shell.  
Observers see the same live shell output but cannot send ordinary shell input to the PTY.

This implementation follows the same Phase 1 design direction:

- existing shell foundation: `/bin/bash`
- PTY-backed interactive shell
- Unix domain socket for local communication
- single-threaded event-loop design

---

## Foundation

This implementation is aligned with the Phase 1 proposal.

- OS: Fedora Linux 43
- Kernel: Linux 6.17.12-300.fc43.x86_64
- Shell: `/bin/bash` GNU bash 5.3.0(1)-release

The shell runs inside a PTY so that interactive shell behavior is preserved.

---

## Main Features

- host starts one shared interactive shell
- multiple clients attach to the same shell session
- shell output is broadcast to all clients in real time
- exactly one controller at a time
- observers are read-only with respect to shell input
- FIFO floor-control policy
- one outstanding request per client
- request / release / cancel / who / name / kick commands
- controller disconnect handling
- observer detach and fresh reconnect
- server shutdown cleanup
- controller-driven PTY resize propagation

---

## Files

- `cis_server.py`  
  Main host/server process. Starts the PTY shell, accepts clients, tracks controller state, request queue, kick behavior, resize handling, disconnect cleanup, and shutdown cleanup.

- `cis_client.py`  
  Interactive client. Connects to the server, displays shared output, sends shell input, supports `Ctrl-T` command mode, handles role-aware `Ctrl-C`, and sends controller resize updates.

- `proto_host.py`  
  Phase 1 prototype used for the initial validation work.

- `run_latency.py`  
  Phase 1 latency experiment script.

- `run_robustness.py`  
  Phase 1 robustness experiment script.

- `appendix/`  
  Phase 1 appendix files.

- `trace/`  
  Phase 2 evidence and notes for tested scenarios.

- `demo_transcript.txt`  
  Demo transcript covering attach, request/release, and disconnect/promotion.

- `POSTMORTEM.txt`  
  One-page postmortem covering the hardest bug, redesign ideas, and fairness policy.

---

## Build / Run

No build step is required.

Optional:


```bash
chmod +x cis_server.py
chmod +x cis_client.py

```

### Start the server


```bash
cd /home/rkashko/os/project1
rm -f cis.sock
python3 cis_server.py ./cis.sock

```

### Start a client

Open another terminal:


```bash
cd /home/rkashko/os/project1
python3 cis_client.py ./cis.sock

```

Start additional clients the same way.

---

## Client Interface

The client has two modes.

### 1. Normal mode

In normal mode, typed input is treated as shell input.

- If the client is the Controller, input is forwarded to the shared shell.
- If the client is an Observer, ordinary shell input is rejected.

### 2. Command mode

Press:


```text
Ctrl-T

```

This enters command mode.

Supported command mode commands:


```text
request
release
cancel
who
name NEWNAME
kick ID
quit

```

### Meaning of commands

- `request`  
  Request controller access.

- `release`  
  Release controller access.

- `cancel`  
  Cancel a pending request.

- `who`  
  Show current CIS participant state, including:
  - client id
  - client name
  - role
  - pending status
  - queue state

- `name NEWNAME`  
  Rename the current client.

- `kick ID`  
  Remove another client from the session. Only the current Controller may use this.

- `quit`  
  Exit the CIS client.

---

## Important Clarification: command-mode `who` vs shell `who`

These are different.

### Command-mode `who`

Use:


```text
Ctrl-T
who

```

This shows internal CIS participant and queue state.

### Shell command `who`

If you type:


```bash
who

```

in normal mode, it runs the normal system shell command inside the shared shell.

Do not confuse these during demo or oral defense.

---

## Floor-Control Policy

### Roles

- **Controller**: shell input reaches the PTY
- **Observer**: shell input does not reach the PTY

### Fairness Policy

Fairness policy: **FIFO**

Rules:

- Observers may request control.
- Each client may have **at most one outstanding request**.
- Duplicate pending requests are ignored.
- When the Controller releases control, the first queued requester becomes Controller.
- When the Controller disconnects, the first queued requester becomes Controller.
- If the queue is empty, the session remains alive with no Controller assigned.

### Outstanding Request

An outstanding request is a request that has been registered but has not yet been:

- granted
- canceled
- removed because of disconnect

---

## Protocol Design

The implementation separates control commands from ordinary shell input.

### Control messages

The client sends control operations as:


```text
CONTROL request
CONTROL release
CONTROL cancel
CONTROL who
CONTROL name NEWNAME
CONTROL kick ID
CONTROL resize ROWS COLS

```

### Shell input messages

Ordinary shell input is sent separately as:


```text
INPUT <hex-encoded-bytes>

```

This separation was necessary to avoid mixing line-oriented control logic with interactive shell bytes.

---

## Resize Policy

Window resize support is implemented with a simple policy:

- only the current **Controller** may resize the shared PTY
- observer-side window resizing is local only
- observer resize is not forwarded to the PTY

### Mechanism

- the client captures `SIGWINCH`
- the client reads terminal rows/cols
- the client sends:


```text
CONTROL resize ROWS COLS

```

- the server applies the resize to the PTY using `TIOCSWINSZ`

This gives minimal but correct PTY resize propagation without conflicting observer window sizes.

---

## OS Mechanisms Used

This implementation uses the same OS-level ideas identified in Phase 1.

### PTY-backed shell

The shared shell runs inside a PTY.

### Process management

The implementation uses:

- fork/exec to launch the shell
- `waitpid` to reap the shell child
- signals for shutdown

### I/O multiplexing

The server uses a single-threaded event loop with `select()` across:

- PTY master
- server socket
- connected client sockets

The client also uses `select()` across:

- local stdin
- connected socket

### Terminal handling

The client places the terminal into raw mode in order to:

- capture `Ctrl-T`
- support interactive shell input
- forward `Ctrl-C` correctly depending on role
- detect terminal resize events

### Unix domain socket

The implementation uses a Unix domain socket on the same machine, relying on local filesystem permissions rather than a remote transport.

---

## Failure Handling

### Controller disconnect

If the Controller disconnects, the shell remains alive.  
The next queued requester becomes Controller automatically.  
If no queued requester exists, the session remains alive with no Controller assigned.

### Duplicate requests

A client cannot queue itself multiple times.

### Observer exit

An Observer may exit without affecting the shared shell session.

### Reconnect / reattach

Reconnect is supported as a **fresh reconnect**:

- the client disconnects
- a later reconnect succeeds as a new client connection
- the reconnecting client receives a new client id
- the reconnecting client joins as an Observer unless later granted control

Identity-preserving reattachment is not implemented.

### Server shutdown

When the server shuts down:

- client connections are closed
- the socket path is removed
- the shell child is signaled and reaped
- the PTY descriptor is closed

### Ctrl-C behavior

The client handles `Ctrl-C` differently depending on state:

- in command mode, `Ctrl-C` cancels command mode
- if the client is an Observer, `Ctrl-C` exits the client
- if the client is the Controller, `Ctrl-C` is forwarded to the shell

---

## Administrative Control

### Kick

The current Controller may remove another connected client from the session:


```text
Ctrl-T
kick ID

```

Policy:

- only the current Controller may kick clients
- self-kick is not allowed
- observers may not kick clients

### Ban

Persistent ban is **not implemented** in this phase.

Reason:  
A robust ban mechanism requires stable client identity across reconnects, and this implementation currently supports reconnect as a fresh new client with a new id.

---

## Network Drop / Reconnect Interpretation

This implementation uses a Unix domain socket on the same machine rather than a remote network transport.

Therefore, for this project:

- “network drop / reconnect” is interpreted as **client disconnect followed by fresh reconnect**
- packet-level or remote network fault handling is not implemented
- reconnect behavior is implemented in a same-machine local-socket form

---

## Current Limitations

The implementation intentionally focuses on the core assignment behavior.

Not fully implemented:

- identity-preserving reconnect
- persistent ban policy
- advanced multi-client resize arbitration beyond controller-owned PTY size
- remote network fault handling beyond local disconnect/reconnect semantics

---

## Tested Scenarios

The following behaviors were validated during development and recorded in project artifacts:

- initial controller assignment
- observer read-only enforcement
- FIFO request queue
- duplicate request rejection
- controller release and queued promotion
- controller disconnect with queued observer promotion
- observer exit with `Ctrl-C`
- controller forwarding `Ctrl-C` to shell
- kick by controller
- kick rejection for observer
- server shutdown cleanup
- fresh reconnect behavior
- controller-driven PTY resize propagation

---

## Trace / Evidence Files

The `trace/` directory contains structured scenario evidence:

- `controller_disconnect.txt`
- `kick_test.txt`
- `server_shutdown.txt`
- `reconnect.txt`
- `detach_reattach_policy.txt`
- `ban_policy.txt`
- `network_drop_reconnect_note.txt`
- `resize_test.txt`

These files document the tested behavior and the implementation policies used in this phase.

---

## Example Demo Flow

### Server terminal


```bash
python3 cis_server.py ./cis.sock

```

### Client 1 terminal

Client 1 becomes Controller.


```bash
python3 cis_client.py ./cis.sock

```

### Client 2 terminal

Client 2 becomes Observer.


```bash
python3 cis_client.py ./cis.sock

```

### Client 2 requests control


```text
Ctrl-T
request

```

### Client 1 releases control


```text
Ctrl-T
release

```

### Client 2 runs a shell command


```bash
whoami

```

### Client 1 checks CIS state


```text
Ctrl-T
who

```

### Client 1 kicks another client


```text
Ctrl-T
kick 2

```

---

## Submission Artifacts

Phase 2 submission should include at least:

- `cis_server.py`
- `cis_client.py`
- `README.md`
- `demo_transcript.txt`
- `POSTMORTEM.txt`
- `trace/`

Optional additional context:

- Phase 1 appendix and proposal materials

---

## Summary

This Phase 2 implementation extends the Phase 1 proposal into a working collaborative shell system with:

- PTY-backed shared shell execution
- explicit Controller / Observer separation
- FIFO floor control
- practical reconnect behavior
- kick support
- controller-driven resize propagation
- graceful shutdown cleanup

The design stays aligned with the original proposal while fixing earlier control-path ambiguity and improving robustness and usability.
