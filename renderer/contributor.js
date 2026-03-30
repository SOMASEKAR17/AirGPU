
const { ipcRenderer } = require("electron");



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

    document.getElementById("btn-start").addEventListener("click", () => {
      document.getElementById("config-page").style.display = "none";
      document.getElementById("dashboard-page").style.display = "flex";
      
      ipcRenderer.send("start-agent", {
        maxCpus: cpusSlider.value,
        maxRamGb: ramSlider.value,
        maxGpuVramMb: hasGpu ? gpuSlider.value : 0
      });
    });
  }
}

setupConfig();
