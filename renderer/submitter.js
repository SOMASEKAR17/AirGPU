
const { ipcRenderer } = require("electron");
const fs = require("fs");



const fileInput   = document.getElementById("file-input");
const fileName    = document.getElementById("file-name");
const reqInput    = document.getElementById("req-input");
const reqName     = document.getElementById("req-name");
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



const COORDINATOR_HTTP = "http://10.212.87.185:8000";
const COORDINATOR_WS   = "ws://10.212.87.185:8000";

let selectedFilePath = null;
let selectedFileContents = null;
let selectedReqPath = null;
let selectedReqContents = null;



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



btnSubmit.addEventListener("click", async () => {
  if (!selectedFileContents) return;

  btnSubmit.disabled = true;
  
  setStatus("scanning", "Scanning");
  appendLogLine("⟳ Scanning code for security violations...");

  try {
    const res = await fetch(`${COORDINATOR_HTTP}/submit-job`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ 
        script: selectedFileContents,
        requirements: selectedReqContents,
        use_gpu: useGpuCheckbox.checked
      }),
    });

    const data = await res.json();
    
    
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



function connectSubmitterWS(jobId) {
  const ws = new WebSocket(`${COORDINATOR_WS}/ws/submitter/${jobId}`);

  ws.onopen = () => {
    appendLogLine(`⬤ Connected — streaming logs for job ${jobId.slice(0, 8)}…`);
  };

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);

    if (msg.type === "log") {
      appendLogLine(msg.line);
    } else if (msg.type === "status") {
      if (msg.status === "pending" && msg.message) {
        
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
      const doneEl = document.createElement("div");
      doneEl.className = "log-line log-complete";
      doneEl.textContent = "✓ Job Complete";
      logBody.appendChild(doneEl);
      logBody.scrollTop = logBody.scrollHeight;

      setStatus("complete", "Complete");
      btnSubmit.disabled = false;
      ws.close();
    }
  };

  ws.onerror = () => {
    appendLogLine("✗ WebSocket error");
    setStatus("error", "Disconnected");
    btnSubmit.disabled = false;
  };

  ws.onclose = () => {
    
  };
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
