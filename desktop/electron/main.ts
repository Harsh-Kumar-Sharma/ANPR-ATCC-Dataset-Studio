import { app, BrowserWindow, dialog, shell } from "electron";
import { spawn, ChildProcess } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Populated by vite-plugin-electron in dev; undefined in a packaged build.
const VITE_DEV_SERVER_URL = process.env.VITE_DEV_SERVER_URL;
const IS_PACKAGED = !VITE_DEV_SERVER_URL;

const BACKEND_HEALTH_URL = "http://127.0.0.1:8000/health";
const BACKEND_READY_TIMEOUT_MS = 30_000;
const BACKEND_POLL_INTERVAL_MS = 300;

let mainWindow: BrowserWindow | null = null;
let backendProcess: ChildProcess | null = null;

function backendExecutablePath(): string {
  // electron-builder's extraResources puts the PyInstaller onedir
  // output under resources/backend/ - see desktop/package.json "build".
  const exeName = process.platform === "win32" ? "anpr-atcc-backend.exe" : "anpr-atcc-backend";
  return path.join(process.resourcesPath, "backend", exeName);
}

function startBackend(): void {
  const exePath = backendExecutablePath();
  backendProcess = spawn(exePath, [], { stdio: "ignore" });
  backendProcess.on("error", (err) => {
    console.error("Failed to start backend process:", err);
  });
  backendProcess.on("exit", (code) => {
    console.log("Backend process exited with code", code);
    backendProcess = null;
  });
}

function stopBackend(): void {
  if (backendProcess) {
    backendProcess.kill();
    backendProcess = null;
  }
}

async function waitForBackend(timeoutMs: number): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(BACKEND_HEALTH_URL);
      if (response.ok) return true;
    } catch {
      // backend not up yet - keep polling
    }
    await new Promise((resolve) => setTimeout(resolve, BACKEND_POLL_INTERVAL_MS));
  }
  return false;
}

/**
 * Let the app save a file the user asked for.
 *
 * Without this a download link does nothing visible: Electron picks
 * a folder on its own and says so to nobody, so a dataset zip the
 * user asked for lands somewhere they have to go hunting for. They
 * choose where it goes, and it is revealed when it lands.
 */
function handleDownloads(window: BrowserWindow): void {
  window.webContents.session.on("will-download", (_event, item) => {
    const chosen = dialog.showSaveDialogSync(window, {
      title: "Save dataset",
      defaultPath: item.getFilename(),
      filters: [{ name: "Zip archive", extensions: ["zip"] }],
    });

    if (!chosen) {
      item.cancel();
      return;
    }

    item.setSavePath(chosen);
    item.once("done", (_doneEvent, state) => {
      // Only on success: showing the folder after a failed download
      // points at a file that is not there.
      if (state === "completed") {
        shell.showItemInFolder(chosen);
      }
    });
  });
}

async function createWindow(): Promise<void> {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    title: "ANPR + ATCC Dataset Studio",
    webPreferences: {
      preload: path.join(__dirname, "preload.mjs"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  handleDownloads(mainWindow);

  if (VITE_DEV_SERVER_URL) {
    mainWindow.loadURL(VITE_DEV_SERVER_URL);
    return;
  }

  // In a packaged build the backend is a bundled child process, not
  // something the user started separately - launch it and give it a
  // head start before loading the UI that depends on it.
  await waitForBackend(BACKEND_READY_TIMEOUT_MS);
  mainWindow.loadFile(path.join(__dirname, "../dist/index.html"));
}

app.whenReady().then(() => {
  if (IS_PACKAGED) {
    startBackend();
  }
  createWindow();
});

app.on("window-all-closed", () => {
  stopBackend();
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});

app.on("before-quit", () => {
  stopBackend();
});
