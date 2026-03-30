const { ipcRenderer } = require("electron");
const fs = require("fs");

window.electronAPI = window.electronAPI || {
  setAuthToken: (token) => ipcRenderer.invoke('set-auth-token', token),
  getAuthToken: () => ipcRenderer.invoke('get-auth-token'),
  setUser: (user) => ipcRenderer.invoke('set-user', user),
  getUser: () => ipcRenderer.invoke('get-user'),
  logout: () => ipcRenderer.invoke('logout'),
  navigate: (page) => ipcRenderer.send('navigate', page),
  startAgent: (cfg) => ipcRenderer.send('start-agent', cfg),
  openCreditsPage: () => ipcRenderer.invoke('open-credits-page'),
  getCoordinatorBase: () => ipcRenderer.invoke('get-coordinator-base'),
  updateCreditDisplay: (balance) => ipcRenderer.invoke('update-credit-display', balance)
};

const fileInput   = document.getElementById("file-input");
const fileName    = document.getElementById("file-name");
const reqInput    = document.getElementById("req-input");
const reqName     = document.getElementById("req-name");
const datasetInput = document.getElementById("dataset-input");
const datasetName = document.getElementById("dataset-name");
const btnSubmit   = document.getElementById("btn-submit");
const logBody     = document.getElementById("log-body");
const placeholder = document.getElementById("log-placeholder");
const statusBadge = document.getElementById("status-badge");
const statusText  = document.getElementById("status-text");
const useGpuCheckbox = document.getElementById("use-gpu-checkbox");

document.getElementById("btn-minimize").addEventListener("click", () => ipcRenderer.send("window-minimize"));
document.getElementById("btn-maximize").addEventListener("click", () => ipcRenderer.send("window-maximize"));
document.getElementById("btn-close").addEventListener("click",    () => ipcRenderer.send("window-close"));
document.getElementById("btn-back").addEventListener("click",     () => ipcRenderer.send("go-home"));

const COORDINATOR_HTTP = "https://airgpu.onrender.com";
const COORDINATOR_WS   = "wss://airgpu.onrender.com";

let selectedFilePath = null;
let selectedFileContents = null;
let selectedReqPath = null;
let selectedReqContents = null;
let selectedDatasetContents = null;
let selectedDatasetFilename = null;

fileInput.addEventListener("change", () => {
  const file = fileInput.files[0];
  if (!file) return;

  selectedFilePath = file.path || file.name;
  fileName.textContent = file.name;

  const reader = new FileReader();
  reader.onload = () => {
    selectedFileContents = reader.result;
    btnSubmit.disabled = false;
  };
  reader.readAsText(file);
});

reqInput.addEventListener("change", () => {
  const file = reqInput.files[0];
  if (!file) {
    selectedReqPath = null;
    selectedReqContents = null;
    reqName.textContent = "No requirements";
    return;
  }

  selectedReqPath = file.path || file.name;
  reqName.textContent = file.name;

  const reader = new FileReader();
  reader.onload = () => {
    selectedReqContents = reader.result;
  };
  reader.readAsText(file);
});

datasetInput.addEventListener("change", () => {
    const file = datasetInput.files[0];
    if (!file) {
        selectedDatasetContents = null;
        selectedDatasetFilename = null;
        datasetName.textContent = "No dataset";
        return;
    }
    selectedDatasetFilename = file.name;
    datasetName.textContent = file.name;
    const reader = new FileReader();
    reader.onload = () => {
        const arrayBuffer = reader.result;
        const bytes = new Uint8Array(arrayBuffer);
        let binary = "";
        for (let i = 0; i < bytes.byteLength; i++) {
            binary += String.fromCharCode(bytes[i]);
        }
        selectedDatasetContents = btoa(binary);
    };
    reader.readAsArrayBuffer(file);
});

btnSubmit.addEventListener("click", async () => {
  if (!selectedFileContents) return;

  btnSubmit.disabled = true;
  
  setStatus("scanning", "Scanning");
  appendLogLine("⟳ Scanning code for security violations...");

  try {
    const token = await window.electronAPI.getAuthToken();
    const user = await window.electronAPI.getUser();
    const submitterEmail = user?.email || "anonymous";

    const durationEstimate = 60.0;
    const cpuCores = 2.0;
    const ramGb = 4;
    const gpuVramGb = useGpuCheckbox && useGpuCheckbox.checked ? 6.0 : 0.0;

    const cpuCost = cpuCores * durationEstimate * 0.01;
    const ramCost = ramGb * durationEstimate * 0.005;
    const gpuCost = gpuVramGb * durationEstimate * 0.05;
    const estimatedCost = Math.max(1.0, parseFloat((cpuCost + ramCost + gpuCost).toFixed(2)));

    const res = await fetch(`${COORDINATOR_HTTP}/submit-job`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { "Authorization": `Bearer ${token}` } : {})
      },
      body: JSON.stringify({ 
        script: selectedFileContents,
        requirements: selectedReqContents || null,
        use_gpu: useGpuCheckbox ? useGpuCheckbox.checked : false,
        submitter_email: submitterEmail,
        estimated_cost: estimatedCost,
        cpu_cores: cpuCores,
        ram_gb: ramGb,
        gpu_vram_gb: gpuVramGb,
        duration_estimate_seconds: durationEstimate,
        dataset_filename: selectedDatasetFilename || null,
        output_extensions: [".pkl", ".pt", ".h5", ".csv", ".json", ".txt", ".png", ".jpg", ".npy", ".npz", ".onnx", ".pth"]
      }),
    });

    const data = await res.json();
    
    if (data.rejected && data.reason === "insufficient_credits") {
        setStatus("error", "Error");
        appendLogLine(`✗ Insufficient credits — balance: ${data.balance} credits`);
        appendLogLine(`Purchase credits to continue submitting jobs.`);
        btnSubmit.disabled = false;
        return;
    }

    if (data.rejected) {
        setStatus("error", "Error");
        appendLogLine("✗ Job rejected — code security scan failed\n");
        if (data.scan_violations && data.scan_violations.length > 0) {
            data.scan_violations.forEach(v => {
                appendLogLine(`  ⚠ ${v}`);
            });
        }
        appendLogLine("\nFix the violations above and resubmit.");
        
        btnSubmit.disabled = false;
        return;
    }

    const jobId = data.job_id;

    if (selectedDatasetContents && selectedDatasetFilename) {
        appendLogLine(`⟳ Uploading dataset: ${selectedDatasetFilename}...`);
        try {
            const uploadRes = await fetch(`${COORDINATOR_HTTP}/upload-dataset`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    ...(token ? { "Authorization": `Bearer ${token}` } : {})
                },
                body: JSON.stringify({
                    job_id: jobId,
                    filename: selectedDatasetFilename,
                    data: selectedDatasetContents
                })
            });
            const uploadData = await uploadRes.json();
            if (uploadData.saved) {
                appendLogLine(`✓ Dataset uploaded: ${selectedDatasetFilename}`);
            } else {
                appendLogLine(`⚠ Dataset upload failed — job will run without dataset`);
            }
        } catch(e) {
            appendLogLine(`⚠ Dataset upload error: ${e.message}`);
        }
    }

    const jobIdEl = document.querySelector(".log-job-id");
    if (jobIdEl) {
      jobIdEl.textContent = jobId.slice(0, 8) + "…";
      jobIdEl.title = jobId;
    } else {
      const badge = document.createElement("span");
      badge.className = "log-job-id";
      badge.textContent = jobId.slice(0, 8) + "…";
      badge.title = jobId;
      document.querySelector(".log-header").appendChild(badge);
    }

    clearLog();
    connectSubmitterWS(jobId);

  } catch (err) {
    setStatus("error", "Error");
    appendLogLine(`✗ Failed to submit: ${err.message}`);
    btnSubmit.disabled = false;
  }
});

async function connectSubmitterWS(jobId) {
  const token = await window.electronAPI.getAuthToken();
  const wsUrl = `${COORDINATOR_WS}/ws/submitter/${jobId}${token ? `?token=${token}` : ''}`;
  const ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    appendLogLine(`⬤ Connected — streaming logs for job ${jobId.slice(0, 8)}…`);
  };

  ws.onmessage = async (event) => {
    const msg = JSON.parse(event.data);

    if (msg.type === "log") {
      appendLogLine(msg.line);
    } else if (msg.type === "checkpoint") {
      appendLogLine(`✓ Checkpoint saved — epoch ${msg.epoch}`);
    } else if (msg.type === "failed") {
      setStatus("error", "Error");
      appendLogLine(`✗ ${msg.message}`);
      btnSubmit.disabled = false;
    } else if (msg.type === "output_file") {
        const panel = document.getElementById("output-files-panel");
        const list = document.getElementById("output-files-list");
        const count = document.getElementById("output-files-count");
        panel.style.display = "block";
        const row = document.createElement("div");
        row.className = "output-file-row";
        row.innerHTML = `
            <span class="output-file-name">${msg.filename}</span>
            <div style="display:flex;align-items:center;gap:12px;">
                <button class="download-btn" onclick="downloadOutputFile('${msg.job_id}', '${msg.filename}')">
                    ↓ Download
                </button>
            </div>
        `;
        list.appendChild(row);
        const fileCount = list.querySelectorAll(".output-file-row").length;
        count.textContent = `${fileCount} file${fileCount !== 1 ? "s" : ""}`;
        appendLogLine(`✓ Output file ready: ${msg.filename}`);
    } else if (msg.type === "status") {
      if (msg.checkpoint_epoch > 0) {
        appendLogLine(`↺ ${msg.message} (will resume from epoch ${msg.checkpoint_epoch})`);
      } else if (msg.status === "pending" && msg.message) {
        appendLogLine(`⚠ ${msg.message}`);
        setStatus("queued", "Queued");
      } else if (msg.status === "pending") {
        setStatus("queued", "Queued");
        appendLogLine(`⏳ Job queued — position ${msg.queue_position} in line`);
        if (useGpuCheckbox.checked) {
          appendLogLine(`Waiting for a GPU contributor...`);
        } else {
          appendLogLine(`Waiting for an available contributor...`);
        }
      } else if (msg.status === "running") {
        setStatus("running", "Running");
        appendLogLine(`▶ Job assigned to a contributor — starting execution`);
      }
    } else if (msg.type === "done") {
      setStatus("complete", "Complete");
      const doneEl = document.createElement("div");
      doneEl.className = "log-line log-complete";
      doneEl.textContent = "✓ Job Complete";
      logBody.appendChild(doneEl);
      if (msg.actual_cost !== undefined) {
          const costEl = document.createElement("div");
          costEl.className = "log-line";
          costEl.style.color = "#C9A84C";
          costEl.textContent = `Cost: ${parseFloat(msg.actual_cost).toFixed(2)} credits`;
          logBody.appendChild(costEl);
      }
      logBody.scrollTop = logBody.scrollHeight;
      btnSubmit.disabled = false;
      refreshCreditBalance();
      await fetchAndDisplayOutputFiles(jobId);
      ws.close();
    }
  };

  ws.onerror = () => {
    appendLogLine("✗ WebSocket error");
    setStatus("error", "Disconnected");
    btnSubmit.disabled = false;
  };
}

async function downloadOutputFile(jobId, filename) {
    try {
        const url = `${COORDINATOR_HTTP}/download-output/${jobId}/${filename}`;
        const res = await fetch(url);
        const blob = await res.blob();
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(a.href);
    } catch(e) {
        appendLogLine(`✗ Download failed: ${e.message}`);
    }
}
window.downloadOutputFile = downloadOutputFile;

async function fetchAndDisplayOutputFiles(jobId) {
    try {
        const res = await fetch(`${COORDINATOR_HTTP}/list-outputs/${jobId}`);
        const data = await res.json();
        if (!data.files || data.files.length === 0) return;
        const panel = document.getElementById("output-files-panel");
        const list = document.getElementById("output-files-list");
        const count = document.getElementById("output-files-count");
        panel.style.display = "block";
        list.innerHTML = "";
        data.files.forEach(file => {
            const sizeKB = (file.size_bytes / 1024).toFixed(1);
            const row = document.createElement("div");
            row.className = "output-file-row";
            row.innerHTML = `
                <span class="output-file-name">${file.filename}</span>
                <div style="display:flex;align-items:center;gap:12px;">
                    <span class="output-file-size">${sizeKB} KB</span>
                    <button class="download-btn" onclick="downloadOutputFile('${jobId}', '${file.filename}')">
                        ↓ Download
                    </button>
                </div>
            `;
            list.appendChild(row);
        });
        count.textContent = `${data.files.length} file${data.files.length !== 1 ? "s" : ""}`;
    } catch(e) {}
}

function clearLog() {
  if (placeholder) placeholder.remove();
  while (logBody.firstChild) {
    logBody.removeChild(logBody.firstChild);
  }
}

function appendLogLine(text) {
  if (placeholder && placeholder.parentNode) placeholder.remove();
  const el = document.createElement("div");
  el.className = "log-line";
  el.textContent = text;
  logBody.appendChild(el);
  logBody.scrollTop = logBody.scrollHeight;
}

function setStatus(type, text) {
  statusBadge.className = `status-badge ${type}`;
  statusText.textContent = text;
}

async function refreshCreditBalance() {
    try {
        const token = await window.electronAPI.getAuthToken();
        if (!token) return;
        const decoded = JSON.parse(atob(token.split('.')[1]));
        const uid = decoded.user_id || decoded.uid || decoded.sub;
        if (!uid) return;
        const res = await fetch(`${COORDINATOR_HTTP}/credits/${uid}`);
        const data = await res.json();
        const balance = parseFloat(data.balance).toFixed(1);
        await window.electronAPI.updateCreditDisplay(balance);
    } catch(e) {}
}

refreshCreditBalance();
