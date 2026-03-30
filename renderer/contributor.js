const { ipcRenderer } = require("electron");

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



const connDot      = document.getElementById("conn-dot");
const connLabel    = document.getElementById("conn-label");
const nodeIdEl     = document.getElementById("node-id");
const cpuVal       = document.getElementById("cpu-val");
const ramVal       = document.getElementById("ram-val");
const jobBanner    = document.getElementById("job-banner");
const jobIdEl      = document.getElementById("job-id");
const logBody      = document.getElementById("log-body");
const placeholder  = document.getElementById("log-placeholder");

const cpusSlider   = document.getElementById("cpus-slider");
const ramSlider    = document.getElementById("ram-slider");
const cpusValText  = document.getElementById("cfg-cpus-val");
const ramValText   = document.getElementById("cfg-ram-val");



document.getElementById("btn-minimize").addEventListener("click", () => ipcRenderer.send("window-minimize"));
document.getElementById("btn-maximize").addEventListener("click", () => ipcRenderer.send("window-maximize"));
document.getElementById("btn-close").addEventListener("click",    () => ipcRenderer.send("window-close"));
document.getElementById("btn-back").addEventListener("click",     () => ipcRenderer.send("go-home"));



let isConnected = false;
let currentJobId = null;
let currentNodeId = null;
let coordinatorUrl = "ws://hopper.proxy.rlwy.net:32592/ws/contributor";

(async () => {
    try {
        const base = await window.electronAPI.getCoordinatorBase();
        if (base) {
            coordinatorUrl = base.replace("http://", "ws://").replace("https://", "wss://") + "/ws/contributor";
        }
    } catch(e) {}
})();

setInterval(() => {
    if (currentNodeId) fetchAndRenderHistory();
}, 10000);

async function fetchAndRenderHistory() {
    const nodeId = currentNodeId;
    if (!nodeId) return;
    try {
        const base = await window.electronAPI.getCoordinatorBase();
        const [statsRes, jobsRes] = await Promise.all([
            fetch(`${base}/db/contributor/${nodeId}`),
            fetch(`${base}/db/contributor/${nodeId}/jobs`)
        ]);
        const stats = await statsRes.json();
        const jobsData = await jobsRes.json();
        const jobsList = jobsData.jobs || [];
        const totalJobs = stats.total_jobs_executed || 0;
        const cpuHours = ((stats.total_cpu_time_seconds || 0) / 3600).toFixed(2);
        const gpuHours = ((stats.total_gpu_time_seconds || 0) / 3600).toFixed(2);
        const creditsEarned = (stats.total_credits_earned || 0).toFixed(2);
        document.getElementById("history-count").textContent = `${totalJobs} job${totalJobs !== 1 ? "s" : ""} completed`;
        document.getElementById("cpu-hours").textContent = `${cpuHours}h`;
        document.getElementById("gpu-hours").textContent = `${gpuHours}h`;
        document.getElementById("credits-earned").textContent = creditsEarned;
        const list = document.getElementById("history-list");
        if (!jobsList || jobsList.length === 0) {
            list.innerHTML = `<div id="history-empty">No jobs completed yet — waiting for work...</div>`;
            return;
        }
        list.innerHTML = "";
        jobsList.forEach((job, i) => {
            const row = document.createElement("div");
            row.className = "history-row";
            row.style.animationDelay = `${i * 0.05}s`;
            const gpuTag = job.use_gpu
                ? `<span class="resource-tag gpu-tag">GPU · ${job.gpu_name || "GPU"}</span>`
                : `<span class="resource-tag">No GPU</span>`;
            const duration = job.duration_seconds != null ? `${job.duration_seconds}s` : "—";
            const cpuTime = job.cpu_time_seconds != null ? `${job.cpu_time_seconds}s CPU` : "";
            const earnedTag = job.contributor_earned_credits
                ? `<span class="resource-tag" style="color:#C9A84C">+${job.contributor_earned_credits} cr</span>`
                : "";
            row.innerHTML = `
                <div class="history-row-top">
                    <span class="job-id">job #${job.job_id.slice(0, 8)}</span>
                    <span class="submitter-email">${job.submitter_email || "anonymous"}</span>
                    <span class="job-duration">${duration}</span>
                </div>
                <div class="history-row-bottom">
                    <span class="resource-tag">CPU · ${job.cpu_cores || "?"} cores</span>
                    <span class="resource-tag">RAM · ${job.ram_gb || "?"}GB</span>
                    ${gpuTag}
                    ${earnedTag}
                    ${cpuTime ? `<span class="resource-tag">${cpuTime}</span>` : ""}
                </div>
            `;
            list.appendChild(row);
        });
    } catch (e) {
        console.error("history fetch failed:", e);
    }
}



ipcRenderer.on("agent-log", (_event, line) => {
  appendLog(line);
  parseAgentLine(line);
});

ipcRenderer.on("agent-stopped", () => {
  setConnected(false);
  appendLog("[ui] agent process stopped");
});



function parseAgentLine(line) {
  
  if (line.includes("connected to coordinator")) {
    setConnected(true);
  }
  if (line.includes("connection closed") || line.includes("connection error")) {
    setConnected(false);
  }

  if (line.includes("[agent] node_id =")) {
    currentNodeId = line.split("node_id = ")[1]?.trim();
  }

  if (line.includes("job") && line.includes("complete")) {
    setTimeout(fetchAndRenderHistory, 500);
  }

  const nodeMatch = line.match(/node_id\s*=\s*([a-f0-9-]+)/i);
  if (nodeMatch) {
    nodeIdEl.textContent = nodeMatch[1];
  }

  
  const jobStartMatch = line.match(/running job\s+([a-f0-9-]+)/i);
  if (jobStartMatch) {
    currentJobId = jobStartMatch[1];
    jobIdEl.textContent = currentJobId.slice(0, 8) + "…";
    jobIdEl.title = currentJobId;
    jobBanner.classList.add("visible");
  }

  
  const jobDoneMatch = line.match(/job\s+([a-f0-9-]+)\s+complete/i);
  if (jobDoneMatch) {
    currentJobId = null;
    jobBanner.classList.remove("visible");
  }
}









const { execSync } = require("child_process");
const path = require("path");
const fs = require("fs");

function findPython() {
  
  const base = path.resolve(__dirname, "..");
  const win = path.join(base, ".venv", "Scripts", "python.exe");
  if (fs.existsSync(win)) return win;
  const unix = path.join(base, ".venv", "bin", "python");
  if (fs.existsSync(unix)) return unix;
  const winEnv = path.join(base, "env", "Scripts", "python.exe");
  if (fs.existsSync(winEnv)) return winEnv;
  const unixEnv = path.join(base, "env", "bin", "python");
  if (fs.existsSync(unixEnv)) return unixEnv;
  return "python";
}

const PYTHON_EXE = findPython();

function updateLocalStats() {
  try {
    const out = execSync(
      `"${PYTHON_EXE}" -c "import psutil,json;print(json.dumps({'cpu':psutil.cpu_percent(interval=0.5),'ram':round(psutil.virtual_memory().available/(1024**3),2)}))"`,
      { timeout: 5000 }
    ).toString().trim();
    const stats = JSON.parse(out);
    cpuVal.textContent = stats.cpu.toFixed(1) + "%";
    ramVal.textContent = stats.ram.toFixed(1) + " GB";
  } catch {
    
  }
}


updateLocalStats();
setInterval(updateLocalStats, 5000);

async function updateGpuStats() {
    try {
        const gpu = await ipcRenderer.invoke('get-gpu-stats');
        if (gpu) {
            const vramUsed = gpu.vramTotal - gpu.vramFree;
            const vramUsedGB = (vramUsed / 1024).toFixed(1);
            const vramTotalGB = (gpu.vramTotal / 1024).toFixed(1);
            document.getElementById('gpu-card').style.display = '';
            document.getElementById('gpu-name-display').textContent = gpu.name;
            document.getElementById('gpu-vram-display').textContent =
                `VRAM: ${vramUsedGB}GB / ${vramTotalGB}GB`;
            document.getElementById('gpu-util-display').textContent =
                `Util: ${gpu.utilization}%`;
        } else {
            document.getElementById('gpu-card').style.display = 'none';
        }
    } catch (e) {
        document.getElementById('gpu-card').style.display = 'none';
    }
}


updateGpuStats();
setInterval(updateGpuStats, 5000);



function setConnected(connected) {
  isConnected = connected;
  if (connected) {
    connDot.classList.add("connected");
    connLabel.textContent = "Connected";
  } else {
    connDot.classList.remove("connected");
    connLabel.textContent = "Disconnected";
  }
}

function appendLog(text) {
  if (placeholder && placeholder.parentNode) placeholder.remove();
  const el = document.createElement("div");
  el.className = "log-line";
  el.textContent = text;
  logBody.appendChild(el);
  
  while (logBody.childElementCount > 500) {
    logBody.removeChild(logBody.firstChild);
  }
  logBody.scrollTop = logBody.scrollHeight;
}



async function setupConfig() {
  const maxCpusExt = execSync(`"${PYTHON_EXE}" -c "import os; print(os.cpu_count() or 8)"`).toString().trim();
  const maxRamExt = execSync(`"${PYTHON_EXE}" -c "import psutil; print(round(psutil.virtual_memory().total / (1024**3)))"`).toString().trim();
  
  const gpuInfo = await ipcRenderer.invoke("check-gpu");
  let hasGpu = false;

  if (cpusSlider && ramSlider) {
    cpusSlider.max = maxCpusExt;
    ramSlider.max = maxRamExt;

    document.querySelector("#cpus-slider + .config-hint").innerHTML = `min: 1 <span>max: ${maxCpusExt}</span>`;
    document.querySelector("#ram-slider + .config-hint").innerHTML = `min: 1GB <span>max: ${maxRamExt}GB</span>`;

    cpusSlider.addEventListener("input", e => cpusValText.textContent = e.target.value + " cores");
    ramSlider.addEventListener("input", e => ramValText.textContent = e.target.value + " GB");
    
    const gpuGroup = document.getElementById("gpu-group");
    const gpuSlider = document.getElementById("gpu-slider");
    const gpuValText = document.getElementById("cfg-gpu-val");
    const gpuNameText = document.getElementById("cfg-gpu-name");
    const gpuHintText = document.getElementById("cfg-gpu-hint");

    if (gpuInfo.hasGpu && gpuGroup && gpuSlider) {
      hasGpu = true;
      gpuGroup.style.display = "block";
      gpuNameText.textContent = gpuInfo.gpuName;
      
      const vramTotal = gpuInfo.vramTotalMb;
      gpuSlider.max = vramTotal;
      gpuSlider.min = 1024;
      gpuSlider.step = 512;
      const initialVal = Math.floor(vramTotal / 2 / 512) * 512;
      gpuSlider.value = initialVal;
      
      gpuValText.textContent = (initialVal / 1024).toFixed(1) + " GB";
      gpuHintText.innerHTML = `<span>max: ${(vramTotal / 1024).toFixed(1)}GB</span>`;
      
      gpuSlider.addEventListener("input", e => {
        gpuValText.textContent = (e.target.value / 1024).toFixed(1) + " GB";
      });
    }

    document.getElementById("btn-start").addEventListener("click", async () => {
      document.getElementById("config-page").style.display = "none";
      document.getElementById("dashboard-page").style.display = "flex";
      
      const token = await window.electronAPI.getAuthToken();
      ipcRenderer.send("start-agent", {
        maxCpus: cpusSlider.value,
        maxRamGb: ramSlider.value,
        maxGpuVramMb: hasGpu ? gpuSlider.value : 0,
        authToken: token || ""
      });
    });
  }
}

setupConfig();
