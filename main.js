
const { app, BrowserWindow, ipcMain } = require("electron");
const path = require("path");
const fs = require("fs");
const { spawn, exec } = require("child_process");



function findPython() {
  const base = __dirname;
  
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

let mainWindow = null;
let agentProcess = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1100,
    height: 740,
    minWidth: 900,
    minHeight: 600,
    backgroundColor: "#FFFFFF",
    frame: false,            
    titleBarStyle: "hidden",
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
    },
  });

  mainWindow.loadFile(path.join(__dirname, "renderer", "index.html"));
}



ipcMain.on("navigate", (_event, page) => {
  mainWindow.loadFile(path.join(__dirname, "renderer", page));
});

ipcMain.on("go-home", () => {
  killAgent();
  mainWindow.loadFile(path.join(__dirname, "renderer", "index.html"));
});



ipcMain.on("window-minimize", () => mainWindow.minimize());
ipcMain.on("window-maximize", () => {
  mainWindow.isMaximized() ? mainWindow.unmaximize() : mainWindow.maximize();
});
ipcMain.on("window-close", () => mainWindow.close());



ipcMain.handle('get-gpu-stats', async () => {
    return new Promise((resolve) => {
        exec(
            'nvidia-smi --query-gpu=name,memory.total,memory.free,utilization.gpu --format=csv,noheader,nounits',
            (error, stdout) => {
                if (error || !stdout.trim()) {
                    resolve(null);
                    return;
                }
                const parts = stdout.trim().split(',').map(s => s.trim());
                resolve({
                    name: parts[0],
                    vramTotal: parseInt(parts[1]),
                    vramFree: parseInt(parts[2]),
                    utilization: parseInt(parts[3])
                });
            }
        );
    });
});

ipcMain.handle("check-gpu", async () => {
  return new Promise((resolve) => {
    exec("nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits", (err, stdout) => {
      if (err) {
        resolve({ hasGpu: false });
        return;
      }
      const parts = stdout.trim().split(",");
      if (parts.length >= 2) {
        resolve({
          hasGpu: true,
          gpuName: parts[0].trim(),
          vramTotalMb: parseInt(parts[1].trim(), 10)
        });
      } else {
        resolve({ hasGpu: false });
      }
    });
  });
});



ipcMain.on("start-agent", (event, { maxCpus = 2, maxRamGb = 4, maxGpuVramMb = 0 } = {}) => {
  if (agentProcess) return;

  const pythonExe = findPython();
  const agentPath = path.join(__dirname, "agent.py");
  console.log(`[main] using python: ${pythonExe}`);
  agentProcess = spawn(pythonExe, ["-u", agentPath], {
    stdio: ["pipe", "pipe", "pipe"],
    env: {
      ...process.env,
      CONTRIB_MAX_CPUS: maxCpus.toString(),
      CONTRIB_MAX_RAM_GB: maxRamGb.toString(),
      CONTRIB_MAX_GPU_VRAM_MB: maxGpuVramMb.toString()
    }
  });

  agentProcess.stdout.on("data", (data) => {
    const lines = data.toString().split("\n").filter(Boolean);
    lines.forEach((line) => {
      console.log(`[agent stdout] ${line}`);
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send("agent-log", line);
      }
    });
  });

  agentProcess.stderr.on("data", (data) => {
    const lines = data.toString().split("\n").filter(Boolean);
    lines.forEach((line) => {
      console.error(`[agent stderr] ${line}`);
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send("agent-log", `[stderr] ${line}`);
      }
    });
  });

  agentProcess.on("error", (err) => {
    console.error(`[main] agent error:`, err);
  });

  agentProcess.on("exit", (code, signal) => {
    console.log(`[main] agent exited with code ${code} and signal ${signal}`);
  });

  agentProcess.on("close", (code, signal) => {
    console.log(`[main] agent closed with code ${code} and signal ${signal}`);
    agentProcess = null;
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("agent-stopped");
    }
  });
});

function killAgent() {
  if (agentProcess) {
    agentProcess.kill();
    agentProcess = null;
  }
}

ipcMain.on("stop-agent", killAgent);



app.whenReady().then(createWindow);

app.on("window-all-closed", () => {
  killAgent();
  app.quit();
});

app.on("before-quit", killAgent);
