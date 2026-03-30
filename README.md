# Collaborative Interactive Shell (CIS)

This repository contains both Phase 1 and Phase 2 of the Collaborative Interactive Shell (CIS) project.

## Repository Structure

- `guidelines/`
  - Original course/project guideline PDF

- `phase1/`
  - Phase 1 proposal materials
  - prototype host
  - experiment scripts
  - appendix files for latency, robustness, and system information

- `phase2/`
  - Final implementation of the collaborative shell
  - server and client code
  - implementation report
  - demo transcript
  - postmortem
  - trace files
  - supporting appendix files

## Foundation

This project is built around:

- `bash`
- `PTY`
- `Unix domain socket`
- a server/client collaborative control design

## Main Features in Phase 2

- shared interactive shell session
- exactly one controller at a time
- observer read-only behavior for ordinary shell input
- FIFO floor-control policy
- request / release / cancel / who / name / kick
- controller disconnect handling
- fresh reconnect behavior
- graceful cleanup
- controller-driven resize propagation

## Notes

- ZIP archives are intentionally excluded from this repository.
- Generated PowerPoint files are intentionally excluded from this repository.
- The repository keeps both the Phase 1 and Phase 2 artifacts for project continuity.

