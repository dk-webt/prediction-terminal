import { app, BrowserWindow, ipcMain, shell } from 'electron'
import { join } from 'path'
import { spawn, execSync, ChildProcess } from 'child_process'

// Suppress Chromium Autofill DevTools errors
app.commandLine.appendSwitch('disable-features', 'AutofillServerCommunication')

let mainWindow: BrowserWindow | null = null
let pythonServer: ChildProcess | null = null

// api_server.py lives one directory up from terminal/ (i.e., in prediciton/)
function getPythonPaths() {
  // app.getAppPath() → …/prediciton/terminal in dev
  const appDir = app.getAppPath()
  const serverScript = join(appDir, '..', 'api_server.py')
  const serverCwd = join(appDir, '..')
  return { serverScript, serverCwd }
}

function killProcessOnPort(port: number) {
  try {
    // Works on macOS and Linux
    const pids = execSync(`lsof -ti:${port}`, { encoding: 'utf-8' }).trim()
    if (pids) {
      console.log(`[main] Killing existing process(es) on port ${port}: ${pids.replace(/\n/g, ', ')}`)
      execSync(`kill ${pids.replace(/\n/g, ' ')}`)
    }
  } catch {
    // No process on port — expected on clean start
  }
}

function startPythonServer() {
  const { serverScript, serverCwd } = getPythonPaths()
  killProcessOnPort(8081)
  console.log('[main] Starting Python server:', serverScript)

  pythonServer = spawn('python3', [serverScript], {
    cwd: serverCwd,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env },
  })

  pythonServer.stdout?.on('data', (data: Buffer) => {
    process.stdout.write('[python] ' + data.toString())
  })
  pythonServer.stderr?.on('data', (data: Buffer) => {
    process.stderr.write('[python] ' + data.toString())
  })
  pythonServer.on('exit', (code) => {
    console.log('[main] Python server exited with code', code)
    pythonServer = null
  })
}

function killPythonServer() {
  if (pythonServer) {
    pythonServer.kill('SIGTERM')
    pythonServer = null
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1600,
    height: 900,
    minWidth: 1100,
    minHeight: 650,
    backgroundColor: '#000000',
    autoHideMenuBar: true,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: false,
    },
  })

  mainWindow.setTitle('Prediction Market Terminal')
  mainWindow.removeMenu()

  // Open external links in the system browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })

  if (process.env['ELECTRON_RENDERER_URL']) {
    // Dev mode: load from Vite dev server
    mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
    mainWindow.webContents.openDevTools({ mode: 'detach' })
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }

  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

app.whenReady().then(() => {
  startPythonServer()

  // Give the Python server 1.5s to start before loading the window
  setTimeout(createWindow, 1500)

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  killPythonServer()
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', () => {
  killPythonServer()
})

// IPC: renderer requests app quit
ipcMain.on('app-quit', () => {
  killPythonServer()
  app.quit()
})
