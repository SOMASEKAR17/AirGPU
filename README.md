# Compound Compute

> **Distributed compute, simplified.**
> Submit Python scripts from one machine and have them execute inside Docker containers on another — all coordinated through a central server and wrapped in a polished Electron desktop app.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
  - [High-Level Diagram](#high-level-diagram)
  - [Three-Layer Model](#three-layer-model)
  - [Data Flow](#data-flow)
- [Tech Stack](#tech-stack)
- [File Reference](#file-reference)
  - [Root Files](#root-files)
  - [Renderer Files](#renderer-files)
- [Getting Started](#getting-started)

---

## Overview

Compound Compute is a **distributed compute platform** built as an Electron desktop app. It allows users to take on one of two roles:

| Role | What it does |
|---|---|
| **Submitter** | Uploads a Python (`.py`) script to the coordinator and streams live execution logs back to the UI. |
| **Contributor** | Donates local CPU/RAM. Jobs arrive via WebSocket, execute inside a sandboxed Docker container, and stdout is streamed back in real time. |

A central **Coordinator** (FastAPI server) sits between the two and handles job routing, WebSocket log relay, and contributor health tracking.

---

## Architecture

### High-Level Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                     Electron Desktop App                         │
│  ┌─────────────┐     ┌─────────────────┐     ┌──────────────┐   │
│  │  index.html  │────▶│ submitter.html  │     │contributor   │   │
│  │ (Role Select)│     │   + submitter.js│     │.html + .js   │   │
│  └─────────────┘     └────────┬────────┘     └──────┬───────┘   │
│                               │                     │            │
│                     HTTP POST │              IPC (stdout)        │
│         main.js ──────────────┼──────── spawns ──▶ agent.py     │
│    (Electron Main Process)    │              (Python sidecar)    │
└───────────────────────────────┼──────────────────────┼───────────┘
                                │                      │
                    ┌───────────▼──────────────────────▼───────────┐
                    │          coordinator.py (FastAPI)             │
                    │               Port 8000                      │
                    │                                              │
                    │  POST /submit-job ─ accepts scripts          │
                    │  WS   /ws/contributor ─ agent heartbeat/log  │
                    │  WS   /ws/submitter/{job_id} ─ log stream    │
                    └──────────────────────────────────────────────┘
```

### Three-Layer Model

```
┌───────────────────────────────────────────────┐
│  Layer 1 — Presentation (Electron + HTML/JS)  │
│  • Role selection, file upload, log panels    │
│  • Custom frameless window with macOS-style   │
│    traffic-light buttons                      │
├───────────────────────────────────────────────┤
│  Layer 2 — Coordination (FastAPI / Python)    │
│  • REST endpoint for job submission           │
│  • WebSocket hub: routes logs from            │
│    contributors → submitters                  │
│  • In-memory job + contributor state          │
├───────────────────────────────────────────────┤
│  Layer 3 — Execution (Python Agent + Docker)  │
│  • Heartbeats (CPU/RAM) every 5 s             │
│  • Receives job via WS, writes script to      │
│    temp file, runs inside Docker container    │
│  • Streams stdout line-by-line back to        │
│    coordinator                                │
└───────────────────────────────────────────────┘
```

### Data Flow

```
1. Submitter picks a .py file → reads contents in browser
2. HTTP POST /submit-job { script } → Coordinator
3. Coordinator finds an idle Contributor → sends { type: "job", script } over WS
4. Contributor agent writes script to temp dir, runs `docker run python:3.11-slim`
5. Each stdout line → { type: "log", line } sent back over WS to Coordinator
6. Coordinator relays log to Submitter's WS connection (/ws/submitter/{job_id})
7. On process exit → { type: "done" } is sent; Submitter UI shows ✓ Complete
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Desktop shell | **Electron** v28 (frameless window, IPC) |
| Frontend | **Vanilla HTML/CSS/JS** (Inter + Playfair Display + JetBrains Mono fonts) |
| Coordinator | **Python / FastAPI** with WebSocket support |
| Agent | **Python** (asyncio + websockets + psutil) |
| Sandboxing | **Docker** (`python:3.11-slim` image) |
| Design system | Custom CSS variables — see `design.md` |

---

## File Reference

### Root Files

| File | Purpose |
|---|---|
| [`package.json`](package.json) | NPM manifest. Declares Electron as the sole dependency and `npm start` → `electron .` |
| [`main.js`](main.js) | **Electron main process.** Creates the frameless `BrowserWindow`, handles IPC for navigation (`navigate`, `go-home`), window controls (minimize/maximize/close), and manages the `agent.py` sidecar lifecycle (`start-agent` / `stop-agent`). |
| [`coordinator.py`](coordinator.py) | **FastAPI coordinator server** (port 8000). Exposes `POST /submit-job` to accept scripts, `WS /ws/contributor` for agents to connect and send heartbeats/logs, and `WS /ws/submitter/{job_id}` for submitters to receive live log streams. All state (contributors, jobs, connections) is held in memory. |
| [`agent.py`](agent.py) | **Contributor agent sidecar.** Connects to the coordinator via WebSocket, sends CPU/RAM heartbeats every 5 s using `psutil`, receives job payloads, writes the script to a temp file, executes it inside a Docker container (`docker run --rm -v ... python:3.11-slim python /app/job.py`), and streams stdout back line-by-line. Auto-reconnects on disconnect. |
| [`design.md`](design.md) | **Brand & design guideline.** Documents the "Modern Financial Sophistication" aesthetic — color palette (Primary Black, Champagne Gold, Soft Lavender-Grey, Success Green), typography rules (Playfair Display serif for branding, Inter sans-serif for UI), the abstract ribbon/glassmorphism motif, and layout principles (negative space, no borders, single CTA). |

### Renderer Files

All renderer files live in the `renderer/` directory and are loaded by Electron.

| File | Purpose |
|---|---|
| [`renderer/index.html`](renderer/index.html) | **Role selection landing page.** Displays the "Compound" logo, tagline, and two role cards — "Submit a Job" and "Contribute Compute." Clicking a card triggers IPC navigation to the corresponding view. Includes abstract blurred ribbon background blobs for the glassmorphism aesthetic and fade-up entrance animations. |
| [`renderer/submitter.html`](renderer/submitter.html) | **Submitter dashboard UI.** Contains a file upload bar (`.py` picker + "Submit Job" button), a log output panel with a status badge (Idle → Running → Complete / Error), and the shared title bar with a ← Back button. All styling is self-contained in a `<style>` block following the design system. |
| [`renderer/submitter.js`](renderer/submitter.js) | **Submitter page logic.** Reads the selected `.py` file via `FileReader`, sends it to the coordinator via `fetch POST /submit-job`, then opens a WebSocket to `/ws/submitter/{job_id}` to stream execution logs into the output panel. Handles status transitions (idle → running → complete / error) and re-enables the submit button on completion. |
| [`renderer/contributor.html`](renderer/contributor.html) | **Contributor dashboard UI.** Shows four stat cards (Connection Status with animated pulse dot, Node ID, CPU Usage, RAM Free), a gold job-running banner, and an agent log panel. All data is populated dynamically by `contributor.js`. |
| [`renderer/contributor.js`](renderer/contributor.js) | **Contributor page logic.** Listens for `agent-log` IPC events from `main.js` (which pipes `agent.py` stdout), parses lines to detect connection state, node ID, and job start/complete events. Polls local CPU/RAM stats every 5 s by shelling out to `psutil` via `execSync`. Caps the log panel at 500 lines for performance. |

---

## Getting Started

### Prerequisites

- **Node.js** ≥ 18
- **Python** ≥ 3.10 with `fastapi`, `uvicorn`, `psutil`, `websockets`
- **Docker** (for sandboxed job execution)

### Run

```bash
# 1. Install Node dependencies
npm install

# 2. Start the coordinator (in a separate terminal)
python coordinator.py          # → http://localhost:8000

# 3. Launch the Electron app
npm start
```

From the app, choose **Contribute Compute** on one instance (this auto-starts `agent.py`) and **Submit a Job** on another to test end-to-end execution.
