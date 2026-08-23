import { spawn, spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "node:net";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = resolve(frontendRoot, "..");
const isWindows = process.platform === "win32";
const backendPython = isWindows
  ? resolve(repositoryRoot, ".venv", "Scripts", "python.exe")
  : "python";
const runId = process.env.GITHUB_RUN_ID ?? String(process.pid);
const children = [];
const ownedPorts = [];
const ownedProcessIds = new Set();
let cleaningUp = false;

function start(command, args, options) {
  const child = spawn(command, args, {
    ...options,
    stdio: "inherit",
    windowsHide: true,
  });
  children.push(child);
  return child;
}

function stop(child) {
  if (!child.pid || child.exitCode !== null) return;
  if (isWindows) {
    spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], {
      stdio: "ignore",
      windowsHide: true,
    });
    return;
  }
  try {
    process.kill(-child.pid, "SIGTERM");
  } catch {
    // 进程可能已经随测试结束退出。
  }
}

function windowsPortOwnerIds(port) {
  const result = spawnSync("netstat", ["-ano", "-p", "TCP"], {
    encoding: "utf8",
    windowsHide: true,
  });
  const processIds = new Set();
  for (const line of String(result.stdout ?? "").split(/\r?\n/)) {
    const columns = line.trim().split(/\s+/);
    if (
      columns.length >= 5 &&
      columns[1]?.endsWith(`:${port}`) &&
      columns[3] === "LISTENING"
    ) {
      processIds.add(columns[4]);
    }
  }
  return processIds;
}

function stopWindowsProcess(processId) {
  if (String(processId) === String(process.pid)) return;
  spawnSync("taskkill", ["/pid", String(processId), "/T", "/F"], {
    stdio: "ignore",
    windowsHide: true,
  });
}

function stopWindowsPortOwner(port) {
  for (const processId of windowsPortOwnerIds(port)) {
    stopWindowsProcess(processId);
  }
}

async function waitFor(url, timeoutMs = 60_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {
      // 服务还在启动，继续轮询。
    }
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 250));
  }
  throw new Error(`等待服务启动超时：${url}`);
}

async function freePort() {
  return await new Promise((resolvePromise, rejectPromise) => {
    const server = createServer();
    server.unref();
    server.once("error", rejectPromise);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close();
        rejectPromise(new Error("无法分配端到端测试端口"));
        return;
      }
      const port = address.port;
      server.close(() => resolvePromise(port));
    });
  });
}

async function main() {
  const backendPort = await freePort();
  const frontendPort = await freePort();
  ownedPorts.push(backendPort, frontendPort);
  const backend = start(
    backendPython,
    [
      "-m",
      "uvicorn",
      "backend.app.main:app",
      "--host",
      "127.0.0.1",
      "--port",
      String(backendPort),
      "--timeout-graceful-shutdown",
      "2",
    ],
    {
      cwd: repositoryRoot,
      detached: !isWindows,
      env: {
        ...process.env,
        DEVTEAM_ENV: "test",
        DEVTEAM_LLM_PROVIDER: "demo",
        DEVTEAM_DATABASE_URL: `sqlite:///./data/e2e-${runId}.db`,
        DEVTEAM_DATABASE_AUTO_CREATE: "true",
      },
    },
  );
  const frontend = start(
    process.execPath,
    [
      resolve(frontendRoot, "node_modules", "vite", "bin", "vite.js"),
      "--host",
      "127.0.0.1",
      "--port",
      String(frontendPort),
      "--strictPort",
    ],
    {
      cwd: frontendRoot,
      detached: !isWindows,
      env: {
        ...process.env,
        VITE_API_ROOT: "/api/v1",
        VITE_API_PROXY_TARGET: `http://127.0.0.1:${backendPort}`,
      },
    },
  );

  backend.once("exit", (code) => {
    if (!cleaningUp && code && code !== 0) {
      console.error(`后端测试服务异常退出：${code}`);
    }
  });
  frontend.once("exit", (code) => {
    if (!cleaningUp && code && code !== 0) {
      console.error(`前端测试服务异常退出：${code}`);
    }
  });

  await Promise.all([
    waitFor(`http://127.0.0.1:${backendPort}/api/v1/ready`),
    waitFor(`http://127.0.0.1:${frontendPort}`),
  ]);
  if (isWindows) {
    for (const port of ownedPorts) {
      for (const processId of windowsPortOwnerIds(port)) {
        ownedProcessIds.add(processId);
      }
    }
  }

  const runner = start(
    process.execPath,
    [
      resolve(frontendRoot, "node_modules", "@playwright", "test", "cli.js"),
      "test",
      "--config",
      resolve(frontendRoot, "playwright.config.ts"),
    ],
    {
      cwd: frontendRoot,
      detached: !isWindows,
      env: {
        ...process.env,
        DEVTEAM_E2E_BASE_URL: `http://127.0.0.1:${frontendPort}`,
      },
    },
  );
  const exitCode = await new Promise((resolvePromise) => {
    runner.once("exit", (code) => resolvePromise(code ?? 1));
  });
  console.log(`浏览器测试进程退出码：${exitCode}`);
  process.exitCode = exitCode;
}

try {
  await main();
} finally {
  cleaningUp = true;
  if (isWindows) {
    for (const processId of ownedProcessIds) stopWindowsProcess(processId);
    for (const port of ownedPorts) stopWindowsPortOwner(port);
  }
  for (const child of children.reverse()) stop(child);
}
