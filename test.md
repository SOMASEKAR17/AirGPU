# Compound Compute — Testing Guide

Step-by-step instructions to verify every feature of the platform.

---

## Prerequisites

Before testing, make sure you have the following installed:

| Requirement       | Check Command              | Expected              |
| ----------------- | -------------------------- | --------------------- |
| Python 3.10+      | `python --version`         | 3.10 or higher        |
| Node.js 18+       | `node --version`           | 18.x or higher        |
| Docker             | `docker --version`         | Any recent version    |
| Docker running     | `docker info`              | No errors             |
| Python packages    | `pip list`                 | fastapi, uvicorn, psutil, websockets |
| Node modules       | check `node_modules/`      | exists after `npm install` |

### One-Time Setup

```bash
# Install Python dependencies
pip install fastapi uvicorn psutil websockets

# Install Node dependencies
npm install
```

---

## Test 1 — Coordinator Starts Successfully

**Goal:** Verify the FastAPI coordinator server boots and listens on port 8000.

### Steps

1. Open **Terminal 1**
2. Run:
   ```bash
   python coordinator.py
   ```
3. You should see output like:
   ```
   INFO:     Uvicorn running on http://0.0.0.0:8000
   INFO:     Started server process [xxxxx]
   INFO:     Waiting for application startup.
   INFO:     Application startup complete.
   ```
4. Open a browser and go to `http://localhost:8000/docs`
5. You should see the **FastAPI Swagger UI** with:
   - `POST /submit-job` endpoint listed
   - The two WebSocket routes won't appear here (they're WS, not REST), but the POST should be visible

### Expected Result
- ✅ Server starts without errors
- ✅ Swagger docs load at `/docs`

---

## Test 2 — Electron App Launches & Role Selection

**Goal:** Verify the Electron app opens and shows the role selection screen.

### Steps

1. Keep the coordinator running from Test 1
2. Open **Terminal 2**
3. Run:
   ```bash
   npm start
   ```
4. The Electron window should open showing:
   - **"Compound"** logo (serif font, top center)
   - **"Distributed compute, simplified."** tagline
   - Two cards side by side:
     - 📤 **Submit a Job**
     - ⚡ **Contribute Compute**
   - Footer text: *"No account required · Everything runs in memory"*
   - Frameless window with traffic-light buttons (minimize, maximize, close) in the top-right
   - Abstract champagne-gold and lavender blurred blobs in the background

5. Hover over each card — they should:
   - Lift up slightly (translateY)
   - Show a deeper shadow
   - Reveal a black accent bar sliding in from the left along the top edge

### Expected Result
- ✅ App opens with no console errors
- ✅ Two role cards are visible and interactive
- ✅ Hover animations work smoothly
- ✅ Window control buttons (minimize/maximize/close) work

---

## Test 3 — Contributor Role: Agent Starts & Connects

**Goal:** Verify clicking "Contribute Compute" spawns the Python agent and connects to the coordinator.

### Steps

1. Coordinator must be running (Terminal 1)
2. In the Electron app, click **"Contribute Compute"**
3. The UI should switch to the Contributor dashboard showing:
   - **← Back** button in the title bar
   - **Status card** — red dot → should turn **green** with "Connected" after a moment
   - **Node ID card** — a UUID like `a3f1b2c4-...`
   - **CPU Usage** card — shows a percentage (e.g., `12.5%`)
   - **RAM Free** card — shows GB value (e.g., `5.82 GB`)
   - **Agent Log** panel at the bottom with lines like:
     ```
     [agent] node_id = a3f1b2c4-xxxx-xxxx-xxxx-xxxxxxxxxxxx
     [agent] connecting to ws://localhost:8000/ws/contributor
     [agent] connected to coordinator
     ```

4. In **Terminal 1** (coordinator), you should see:
   ```
   [coordinator] contributor connected  (cid=xxxxx)
   ```

5. Wait 10+ seconds — the CPU and RAM values should update (they refresh every 5 seconds)

6. Check the green dot has a subtle **pulsing animation**

### Expected Result
- ✅ Agent process starts automatically
- ✅ Connection status turns green
- ✅ Node ID is displayed
- ✅ CPU/RAM stats show real values and update every 5s
- ✅ Coordinator logs the contributor connection

---

## Test 4 — Submitter Role: UI Loads Correctly

**Goal:** Verify the submitter interface renders properly.

### Steps

1. Open **Terminal 3** and start a second Electron instance:
   ```bash
   npm start
   ```
2. Click **"Submit a Job"**
3. The UI should show:
   - **← Back** button in title bar
   - **Upload bar** at the top with:
     - "📁 Choose .py file" button
     - "No file selected" text
     - "Submit Job" button (grayed out / disabled)
   - **Output panel** below with:
     - "Output" header
     - Status badge showing "Idle" (grey)
     - Placeholder text: *"Logs will appear here after you submit a job"*

### Expected Result
- ✅ All UI elements render
- ✅ Submit button is disabled until a file is selected
- ✅ Log area shows placeholder

---

## Test 5 — File Selection

**Goal:** Verify the file picker works and only accepts `.py` files.

### Steps

1. On the submitter screen, click **"📁 Choose .py file"**
2. The native file dialog should open
3. Notice it filters for `.py` files only
4. Create a test script first — save this as `train.py` anywhere on your machine:
   ```python
   import time
   
   print("Starting training...")
   for epoch in range(1, 6):
       print(f"Epoch {epoch}/5 — loss: {1.0 / epoch:.4f}")
       time.sleep(1)
   print("Training complete!")
   ```
5. Select `train.py` in the file dialog
6. The UI should now show:
   - File name displayed: `train.py`
   - **Submit Job** button becomes **enabled** (no longer grayed out)

### Expected Result
- ✅ File dialog opens and filters `.py` files
- ✅ Selected filename appears in the upload bar
- ✅ Submit button becomes clickable

---

## Test 6 — End-to-End Job Execution

**Goal:** Submit a job, have it execute on the contributor's Docker, and stream logs back to the submitter in real time.

### Preconditions
- Terminal 1: Coordinator running (`python coordinator.py`)
- Terminal 2: Electron app in Contributor mode (green dot, connected)
- Terminal 3: Electron app in Submitter mode with `train.py` selected
- Docker is running on the contributor machine

### Steps

1. On the **Submitter** screen, click **"Submit Job"**
2. The status badge should change from "Idle" → **"Submitting…"** → **"Running"**
3. A job ID badge should appear in the output header (e.g., `a1b2c3d4…`)
4. Watch the **log output area** — lines should appear in real time:
   ```
   ⬤ Connected — streaming logs for job a1b2c3d4…
   Starting training...
   Epoch 1/5 — loss: 1.0000
   Epoch 2/5 — loss: 0.5000
   Epoch 3/5 — loss: 0.3333
   Epoch 4/5 — loss: 0.2500
   Epoch 5/5 — loss: 0.2000
   Training complete!
   ✓ Job Complete
   ```
5. The status badge should turn **green** with text **"Complete"**
6. The "✓ Job Complete" line should appear in **green** text

### On the Contributor side
7. Switch to the **Contributor** Electron window
8. You should see:
   - The **gold "Running Job" banner** appeared while the job was running (with the job ID)
   - Agent log shows:
     ```
     [agent] running job a1b2c3d4-...
     [agent] cmd: docker run --rm -v ...
     [agent] job a1b2c3d4-... complete (exit code 0)
     ```
   - The job banner disappears after completion

### On the Coordinator terminal
9. Check Terminal 1 for:
   ```
   [coordinator] submitter connected for job a1b2c3d4-...
   ```

### Expected Result
- ✅ Job is submitted and assigned to the contributor
- ✅ Docker container runs the script
- ✅ Logs stream line-by-line in real time to the submitter
- ✅ "Job Complete" appears in green when done
- ✅ Contributor shows the running job banner during execution
- ✅ Status badges update correctly throughout the lifecycle

---

## Test 7 — No Contributor Available

**Goal:** Verify graceful handling when no contributor is connected.

### Steps

1. Make sure **no Contributor** Electron instance is running (close it or click ← Back)
2. Keep the Coordinator running
3. On the **Submitter**, select a `.py` file and click **"Submit Job"**
4. The UI should show:
   - Status badge turns **red**: "No contributor"
   - Log area shows: `⚠ No contributor is connected. Start a contributor first.`
   - Submit button re-enables so you can try again

### Expected Result
- ✅ No crash or hang
- ✅ Clear error message displayed
- ✅ User can retry after connecting a contributor

---

## Test 8 —  Navigation (Back Button)

**Goal:** Verify the back button returns to role selection and properly cleans up.

### Steps

1. From the **Contributor** screen, click **← Back**
2. You should return to the role selection screen
3. The agent process should be killed (check Terminal 1 — coordinator should log disconnect):
   ```
   [coordinator] contributor disconnected (cid=xxxxx)
   ```
4. From the **Submitter** screen, click **← Back**
5. You should return to the role selection screen
6. You can pick either role again and it should work fresh

### Expected Result
- ✅ Back navigation works from both roles
- ✅ Agent process is terminated when leaving contributor mode
- ✅ Coordinator detects the disconnection
- ✅ Role selection screen loads cleanly again

---

## Test 9 — Window Controls

**Goal:** Verify the custom frameless window controls work.

### Steps

1. On any screen, test the three traffic-light buttons (top-right):
   - 🟡 **Minimize** — window minimizes to taskbar
   - 🟢 **Maximize** — window toggles between maximized and normal size
   - 🔴 **Close** — window closes, agent is killed if running
2. Drag the title bar area to move the window
3. Verify the title bar is draggable but buttons are clickable (not draggable)

### Expected Result
- ✅ All three window control buttons function correctly
- ✅ Title bar is draggable
- ✅ Closing the app kills the agent process

---

## Test 10 — Docker Not Running (Error Case)

**Goal:** Verify behavior when Docker is not available on the contributor machine.

### Steps

1. Stop Docker Desktop (or the Docker daemon)
2. Start the full pipeline: Coordinator → Contributor → Submitter
3. Submit a job
4. The contributor agent log should show a Docker error
5. The submitter may not receive logs (since Docker failed to start the container)

### Expected Result
- ⚠️ Agent logs the Docker error clearly
- ⚠️ No crash — the system remains stable
- ℹ️ This is expected behavior: Docker must be running for job execution

---

## Quick Reference — The 3-Terminal Setup

```
┌─────────────────────────────────────────────────┐
│ Terminal 1:  python coordinator.py               │
│              (keep this running the whole time)  │
├─────────────────────────────────────────────────┤
│ Terminal 2:  npm start                           │
│              → click "Contribute Compute"        │
│              (becomes the worker node)           │
├─────────────────────────────────────────────────┤
│ Terminal 3:  npm start                           │
│              → click "Submit a Job"              │
│              → pick train.py → Submit            │
│              → watch logs stream live            │
└─────────────────────────────────────────────────┘
```

---

## Sample Test Script — `train.py`

Save this anywhere and use it for testing:

```python
import time

print("=" * 40)
print("  COMPOUND COMPUTE — Test Job")
print("=" * 40)
print()
print("Starting training...")
print()

for epoch in range(1, 11):
    loss = 1.0 / epoch
    accuracy = 1 - (1.0 / (epoch + 1))
    print(f"  Epoch {epoch:>2}/10  |  loss: {loss:.4f}  |  acc: {accuracy:.2%}")
    time.sleep(0.8)

print()
print("Training complete!")
print(f"Final accuracy: {accuracy:.2%}")
print("Model saved to /app/model.pt")
```
