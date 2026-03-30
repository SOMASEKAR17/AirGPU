const { app, BrowserWindow, ipcMain, shell } = require("electron");
const path = require("path");
const fs = require("fs");
const { spawn, exec } = require("child_process");
const http = require("http");
const crypto = require("crypto");



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
let currentAuthToken = null;
let currentUser = null;

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

  mainWindow.loadFile(path.join(__dirname, "renderer", "login.html"));
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

ipcMain.handle('set-auth-token', (event, token) => {
    currentAuthToken = token;
});
ipcMain.handle('get-auth-token', () => {
    return currentAuthToken;
});
ipcMain.handle('set-user', (event, user) => {
    currentUser = user;
});
ipcMain.handle('get-user', () => {
    return currentUser;
});
ipcMain.handle('logout', () => {
    currentAuthToken = null;
    currentUser = null;
    mainWindow.loadFile(path.join(__dirname, 'renderer', 'login.html'));
});

ipcMain.handle('start-google-auth', async () => {
    return new Promise((resolve, reject) => {
        let server = http.createServer((req, res) => {
            const url = new URL(req.url, 'http://localhost:9876');

            if (url.pathname === '/' || url.pathname === '/auth-page') {
                res.writeHead(200, { 'Content-Type': 'text/html' });
              res.end(`
    <!DOCTYPE html>
    <html>
    <head>
        <title>Compound \u2014 Authenticating</title>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500&family=Playfair+Display:wght@600&display=swap" rel="stylesheet">
        <style>
            :root {
                --bg: #FDFCF8; /* Ivory/Cream background from reference */
                --text: #1A1A1A;
                --accent: #E8D9A0; /* Subtle gold */
                --slate: #4A4A4A;
            }
            body { 
                background: var(--bg); color: var(--text); 
                font-family: 'Inter', sans-serif;
                display: flex; align-items: center; justify-content: center;
                height: 100vh; margin: 0; 
                -webkit-font-smoothing: antialiased;
            }
            .container { text-align: center; max-width: 400px; animation: fadeIn 0.8s ease; }
            h1 { 
                font-family: 'Playfair Display', serif; 
                font-size: 32px; margin-bottom: 12px; font-weight: 600; 
            }
            .loader {
                width: 40px; height: 2px;
                background: rgba(0,0,0,0.05);
                margin: 24px auto; position: relative; overflow: hidden;
            }
            .loader-bar {
                position: absolute; width: 50%; height: 100%;
                background: var(--text);
                animation: loading 1.5s infinite ease-in-out;
            }
            @keyframes loading {
                0% { left: -50%; } 100% { left: 100%; }
            }
            @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
            p { color: var(--slate); font-size: 14px; letter-spacing: 0.01em; }
            #error-btn { 
                display: none; margin-top: 20px; padding: 12px 24px; 
                background: var(--text); color: white; border: none; 
                border-radius: 4px; cursor: pointer; font-family: 'Inter'; 
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>compound</h1>
            <div class="loader"><div class="loader-bar"></div></div>
            <p id="status">Verifying session...</p>
            <button id="error-btn" onclick="retry()">Try Again</button>
        </div>

        <script type="module">
    import { initializeApp } from 'https://www.gstatic.com/firebasejs/10.7.1/firebase-app.js';
    import { getAuth, signInWithRedirect, getRedirectResult, GoogleAuthProvider } from 'https://www.gstatic.com/firebasejs/10.7.1/firebase-auth.js';

    const firebaseConfig = {
        apiKey: "AIzaSyCI_KpsrIMHGdmeZ_SRBYtpE6JLzE4yTPg",
        authDomain: "airgpu-928f3.firebaseapp.com",
        projectId: "airgpu-928f3",
        storageBucket: "airgpu-928f3.firebasestorage.app",
        messagingSenderId: "450670816358",
        appId: "1:450670816358:web:6d4679efa0a458a680d1a7"
    };

    const app = initializeApp(firebaseConfig);
    const auth = getAuth(app);
    const provider = new GoogleAuthProvider();

    async function handleAuth() {
        const status = document.getElementById('status');
        const errorBtn = document.getElementById('error-btn');
        
        try {
            const result = await getRedirectResult(auth);
            
            if (result && result.user) {
                status.innerText = 'Identity confirmed. Returning to app...';
                const token = await result.user.getIdToken();
                
                const params = new URLSearchParams({
                    token,
                    displayName: result.user.displayName || '',
                    email: result.user.email || '',
                    photoURL: result.user.photoURL || ''
                });
                
                await fetch('/callback?' + params.toString());
                window.close();
                return;
            }

if (auth.currentUser) {
    status.innerText = 'Session found. Returning to app...';
    const token = await auth.currentUser.getIdToken();
    const user = auth.currentUser;
    
    const params = new URLSearchParams({
        token,
        displayName: user.displayName || '',
        email: user.email || '',
        photoURL: user.photoURL || ''
    });
    
    await fetch('/callback?' + params.toString());
    window.close();
    return;
}

            status.innerText = 'Redirecting to secure login...';
            setTimeout(() => {
                signInWithRedirect(auth, provider);
            }, 1500);

        } catch (e) {
            console.error("Auth Error:", e);
            status.innerHTML = '<span style="color:#bc4749">Session expired or blocked.</span>';
            errorBtn.style.display = 'inline-block';
            errorBtn.innerText = 'Restart Sign In';
            errorBtn.onclick = () => signInWithRedirect(auth, provider);
        }
    }

    handleAuth();
</script>
    </body>
    </html>
`);
                return;
            }

            if (url.pathname === '/callback') {
                const token = url.searchParams.get('token');
                const displayName = url.searchParams.get('displayName') || '';
                const email = url.searchParams.get('email') || '';
                const photoURL = url.searchParams.get('photoURL') || '';

                res.writeHead(200, { 'Content-Type': 'text/html' });
res.end(`
    <!DOCTYPE html>
    <html>
    <head>
        <title>Compound — Authenticated</title>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500&family=Playfair+Display:wght@600&display=swap" rel="stylesheet">
        <style>
            :root { --bg: #FDFCF8; --text: #1A1A1A; --slate: #4A4A4A; }
            body {
                background: var(--bg); color: var(--text);
                font-family: 'Inter', sans-serif;
                display: flex; align-items: center; justify-content: center;
                height: 100vh; margin: 0;
                -webkit-font-smoothing: antialiased;
            }
            .container { text-align: center; max-width: 400px; animation: fadeIn 0.6s ease; }
            h1 { font-family: 'Playfair Display', serif; font-size: 32px; margin-bottom: 12px; }
            .check { font-size: 40px; margin-bottom: 16px; }
            p { color: var(--slate); font-size: 14px; }
            @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="check">✓</div>
            <h1>compound</h1>
            <p>Login successful — you can close this tab.</p>
        </div>
        <script>
            // attempt to auto-close, works if tab was opened by the app
            setTimeout(() => window.close(), 1500);
        </script>
    </body>
    </html>
`);

                currentAuthToken = token;
                currentUser = { displayName, email, photoURL };

                resolve({ token, displayName, email, photoURL });

                setTimeout(() => {
                    server.close();
                }, 500);
            }
        });

        server.listen(9876, () => {
            shell.openExternal('http://localhost:9876/auth-page');
        });

        server.on('error', (err) => {
            if (err.code === 'EADDRINUSE') {
                reject(new Error("Auth server port 9876 is busy \u2014 close other apps and retry"));
            } else {
                reject(err);
            }
        });
    });
});



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



ipcMain.on("start-agent", (event, { maxCpus = 2, maxRamGb = 4, maxGpuVramMb = 0, authToken = "" } = {}) => {
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
      CONTRIB_MAX_GPU_VRAM_MB: maxGpuVramMb.toString(),
      CONTRIB_AUTH_TOKEN: authToken
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
