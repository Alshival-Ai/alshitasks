const {
  app,
  Menu,
  nativeTheme,
  BrowserWindow,
  ipcMain,
  dialog,
  shell,
  protocol,
  net,
  session,
  Notification,
} = require("electron");
const http = require("node:http"),
  path = require("node:path"),
  fs = require("node:fs"),
  os = require("node:os");
const { spawn, execFile } = require("node:child_process");

const root = path.resolve(__dirname, "..");
const testRoot = process.env.ALSHITASKS_TEST_ROOT;
const runtime = testRoot
  ? path.join(testRoot, "runtime")
  : path.join(
      process.env.XDG_RUNTIME_DIR || `/run/user/${process.getuid()}`,
      "alshitasks",
    );
const socket = path.join(runtime, "service.sock");
let win,
  fallback,
  alerts = new Set();
app.setName("Alshival.Ai");
app.setPath(
  "userData",
  testRoot
    ? path.join(testRoot, "desktop")
    : path.join(
        process.env.XDG_CONFIG_HOME || path.join(os.homedir(), ".config"),
        "alshitasks",
        "desktop",
      ),
);
protocol.registerSchemesAsPrivileged([
  {
    scheme: "alshitasks",
    privileges: { standard: true, secure: true, supportFetchAPI: true },
  },
]);
if (!app.requestSingleInstanceLock()) app.quit();
app.on("second-instance", () => {
  win?.show();
  win?.focus();
});
const command = (cmd, args) =>
  new Promise((resolve, reject) =>
    execFile(cmd, args, { timeout: 15000 }, (e, out) =>
      e
        ? reject(
            new Error(
              "Operation failed; use the service status command for details",
            ),
          )
        : resolve(out),
    ),
  );
function request(url, method = "GET", body) {
  return new Promise((resolve, reject) => {
    const data = body === undefined ? "" : JSON.stringify(body);
    const req = http.request(
      {
        socketPath: socket,
        path: url,
        method,
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(data),
        },
      },
      (response) => {
        let result = "";
        response.setEncoding("utf8");
        response.on("data", (chunk) => {
          result += chunk;
          if (result.length > 32 * 1024 * 1024)
            req.destroy(new Error("Response too large"));
        });
        response.on("end", () => {
          try {
            const value = JSON.parse(result);
            if (response.statusCode >= 400)
              reject(new Error(value.error?.message || "Service error"));
            else resolve(value);
          } catch {
            reject(new Error("Invalid service response"));
          }
        });
      },
    );
    req.setTimeout(30000, () => req.destroy(new Error("Service timeout")));
    req.on("error", reject);
    req.end(data);
  });
}
function serviceEnvironment() {
  const env = { ...process.env };
  for (const name of Object.keys(env)) {
    if (
      /^(OPENAI|ANTHROPIC|GITHUB|GH_TOKEN|OP_|ALSHIVAL_.*(TOKEN|KEY|SECRET)|DATABASE_URL|AWS_|AZURE_|GOOGLE_APPLICATION_CREDENTIALS)/.test(
        name,
      )
    )
      delete env[name];
  }
  return env;
}
async function serviceStart() {
  try {
    await request("/v1/health");
    return;
  } catch {}
  // Install a per-user unit on first launch; never enable lingering or system services.
  const config = path.join(
    process.env.XDG_CONFIG_HOME || path.join(os.homedir(), ".config"),
    "systemd",
    "user",
  );
  if (testRoot) {
    fallback = spawn(
      "/usr/bin/python3",
      [
        path.join(root, "service/alshitasks-cli.py"),
        "--root",
        testRoot,
        "service",
      ],
      { stdio: "ignore", env: serviceEnvironment() },
    );
    for (let i = 0; i < 100; i++) {
      try {
        await request("/v1/health");
        return;
      } catch {
        await new Promise((r) => setTimeout(r, 50));
      }
    }
    throw new Error("Test service startup failed");
  }
  fs.mkdirSync(config, { recursive: true });
  const python = "/usr/bin/python3";
  const script = path.join(root, "service", "alshitasks-cli.py");
  const unit =
    "[Unit]\nDescription=Alshival.Ai per-user background service\nAfter=graphical-session.target\n[Service]\nType=simple\nExecStart=" +
    python +
    ' "' +
    script.replaceAll('"', '\\"') +
    '" service\nRestart=on-failure\nUnsetEnvironment=OPENAI_API_KEY GITHUB_PAT GH_TOKEN GITHUB_TOKEN OP_SERVICE_ACCOUNT_TOKEN DATABASE_URL\nUMask=0077\nNoNewPrivileges=true\n[Install]\nWantedBy=default.target\n';
  fs.writeFileSync(path.join(config, "alshitasks.service"), unit, {
    mode: 0o600,
  });
  try {
    await command("systemctl", ["--user", "daemon-reload"]);
    await command("systemctl", [
      "--user",
      "enable",
      "--now",
      "alshitasks.service",
    ]);
  } catch {
    fallback = spawn(python, [script, "service"], {
      env: serviceEnvironment(),
      detached: true,
      stdio: "ignore",
    });
    fallback.unref();
  }
  for (let i = 0; i < 50; i++) {
    try {
      await request("/v1/health");
      return;
    } catch {
      await new Promise((r) => setTimeout(r, 100));
    }
  }
  throw new Error(
    "Local service did not start. Run alshitasks service for diagnostics.",
  );
}
function trusted(event) {
  if (
    !win ||
    event.sender !== win.webContents ||
    event.senderFrame !== win.webContents.mainFrame ||
    event.senderFrame.url !== "alshitasks://app/index.html"
  )
    throw new Error("Untrusted IPC sender");
}
const handle = (name, fn) =>
  ipcMain.handle(name, async (event, ...args) => {
    trusted(event);
    return fn(...args);
  });
handle("tasks:request", (url, method, body) => {
  if (
    typeof url !== "string" ||
    !/^\/v1\/[a-zA-Z0-9/_?=&%.,:-]+$/.test(url) ||
    !["GET", "POST", "PATCH", "DELETE"].includes(method) ||
    url.startsWith("/v1/backup") ||
    url.startsWith("/v1/stop") ||
    url.startsWith("/v1/ssh/key") ||
    (url.startsWith("/v1/attachments") &&
      !["GET", "DELETE"].includes(method) &&
      !(
        method === "POST" && /^\/v1\/attachments\/[a-f0-9-]+\/review$/.test(url)
      ))
  )
    throw new Error("Invalid desktop request");
  if (JSON.stringify(body ?? {}).length > 1024 * 1024)
    throw new Error("Request too large");
  return request(url, method, body);
});
handle("tasks:window", (action) => {
  if (!["state", "minimize", "maximize", "close"].includes(action))
    throw new Error("Invalid window action");
  if (action === "minimize") win.minimize();
  if (action === "maximize")
    win.isMaximized() ? win.unmaximize() : win.maximize();
  if (action === "close") {
    win.close();
    return false;
  }
  return win.isMaximized();
});
handle("tasks:theme", (preference) => {
  if (!["light", "dark", "system"].includes(preference))
    throw new Error("Invalid theme");
  nativeTheme.themeSource = preference;
  win.setBackgroundColor(
    nativeTheme.shouldUseDarkColors ? "#000000" : "#f6f4ef",
  );
});
handle("tasks:connect", async () => {
  const result = await request("/v1/portal/connect", "POST", {});
  const url = new URL(result.url);
  if (
    url.origin !== "https://alshival.ai" ||
    url.pathname !== "/oauth/authorize/"
  )
    throw new Error("Invalid sign-in destination");
  await shell.openExternal(result.url);
  return { started: true };
});
handle("tasks:external", (url) => {
  const u = new URL(url);
  if (!["https:", "http:"].includes(u.protocol) || u.username || u.password)
    throw new Error("Unsupported link");
  return shell.openExternal(u.href);
});
handle("tasks:import", async (item, replaces) => {
  if (replaces && !/^[a-f0-9-]{36}$/.test(replaces))
    throw new Error("Invalid attachment");
  if (!/^[a-f0-9-]{36}$/.test(item)) throw new Error("Invalid item");
  const value = await dialog.showOpenDialog(win, { properties: ["openFile"] });
  if (value.canceled) return;
  const file = value.filePaths[0];
  if (fs.statSync(file).size > 20 * 1024 * 1024)
    throw new Error("Attachment exceeds 20 MiB");
  return request("/v1/attachments", "POST", {
    item_id: item,
    replaces,
    name: path.basename(file),
    content: fs.readFileSync(file).toString("base64"),
  });
});
handle("tasks:ssh-key", async (resource, settings) => {
  if (!/^[a-f0-9-]{36}$/.test(resource)) throw new Error("Invalid resource");
  const selected = await dialog.showOpenDialog(win, {
    title: "Choose a local SSH private key",
    properties: ["openFile"],
  });
  if (selected.canceled) return;
  return request("/v1/ssh/key", "POST", {
    ...settings,
    resource_id: resource,
    path: selected.filePaths[0],
  });
});
handle("tasks:save-attachment", async (id) => {
  if (!/^[a-f0-9-]{36}$/.test(id)) throw new Error("Invalid attachment");
  const attachment = await request("/v1/attachments/" + id);
  const v = await dialog.showSaveDialog(win, {
    defaultPath: path.basename(attachment.name),
  });
  if (!v.canceled)
    fs.writeFileSync(v.filePath, Buffer.from(attachment.content, "base64"), {
      flag: "wx",
      mode: 0o600,
    });
});
handle("tasks:backup", async () => {
  const v = await dialog.showSaveDialog(win, {
    defaultPath: `alshitasks-${new Date().toISOString().slice(0, 10)}.zip`,
  });
  if (!v.canceled) return request("/v1/backup", "POST", { path: v.filePath });
});
handle("tasks:restore", async () => {
  const v = await dialog.showOpenDialog(win, {
    properties: ["openFile"],
    filters: [{ name: "Alshival.Ai backup", extensions: ["zip"] }],
  });
  if (v.canceled) return;
  const answer = await dialog.showMessageBox(win, {
    type: "question",
    buttons: ["Cancel", "Restore"],
    defaultId: 0,
    message:
      "Restore this backup? Current data will be preserved in a recovery folder. Connections, grants and monitoring credentials require setup again.",
  });
  if (answer.response !== 1) return;
  await command("systemctl", ["--user", "stop", "alshitasks.service"]);
  const result = await command("/usr/bin/python3", [
    path.join(root, "service/alshitasks-cli.py"),
    "restore",
    v.filePaths[0],
  ]);
  await serviceStart();
  win.reload();
  return result;
});
handle("tasks:service", async (action) => {
  if (action === "start") {
    await serviceStart();
    return;
  }
  if (action === "stop") {
    await request("/v1/stop", "POST");
    await command("systemctl", ["--user", "stop", "alshitasks.service"]).catch(
      () => {},
    );
    return;
  }
  throw new Error("Unknown service control");
});
app.enableSandbox();
app
  .whenReady()
  .then(async () => {
    protocol.handle("alshitasks", async (request) => {
      const u = new URL(request.url);
      if (u.host !== "app") return new Response("", { status: 403 });
      const file = path.resolve(
        root,
        "dist",
        "." + decodeURIComponent(u.pathname),
      );
      if (!file.startsWith(path.join(root, "dist") + path.sep))
        return new Response("", { status: 403 });
      const types = {
        ".html": "text/html",
        ".js": "text/javascript",
        ".css": "text/css",
        ".ttf": "font/ttf",
        ".png": "image/png",
        ".svg": "image/svg+xml",
      };
      const type = types[path.extname(file)];
      if (!type) return new Response("", { status: 404 });
      try {
        return new Response(await fs.promises.readFile(file), {
          headers: {
            "Content-Type": type,
            "X-Content-Type-Options": "nosniff",
          },
        });
      } catch {
        return new Response("", { status: 404 });
      }
    });
    session.defaultSession.setPermissionRequestHandler(
      (_wc, _permission, callback) => callback(false),
    );
    session.defaultSession.webRequest.onBeforeRequest((details, callback) =>
      callback({ cancel: !details.url.startsWith("alshitasks://app/") }),
    );
    await serviceStart();
    Menu.setApplicationMenu(null);
    win = new BrowserWindow({
      frame: false,
      show: false,
      width: 1380,
      height: 900,
      minWidth: 760,
      minHeight: 560,
      title: "Alshival.Ai",
      icon: path.join(root, "desktop/assets/alshival-256x256.png"),
      backgroundColor: "#f6f4ef",
      webPreferences: {
        preload: path.join(__dirname, "preload.cjs"),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        webSecurity: true,
        allowRunningInsecureContent: false,
      },
    });
    win.once("ready-to-show", () => win.show());
    for (const event of ["maximize", "unmaximize"])
      win.on(event, () =>
        win.webContents.send("tasks:window-state", win.isMaximized()),
      );
    win.webContents.setWindowOpenHandler(({ url }) => {
      if (/^https?:/.test(url)) void shell.openExternal(url);
      return { action: "deny" };
    });
    win.webContents.on("will-navigate", (event, url) => {
      if (url !== "alshitasks://app/index.html") {
        event.preventDefault();
        if (/^https?:/.test(url)) void shell.openExternal(url);
      }
    });
    await win.loadURL("alshitasks://app/index.html");
    let lastRevision,
      checkingRevision = false;
    setInterval(async () => {
      if (checkingRevision || !win || win.isDestroyed()) return;
      checkingRevision = true;
      try {
        const { revision } = await request("/v1/revision");
        if (revision !== lastRevision) {
          lastRevision = revision;
          win.webContents.send("tasks:changed");
        }
      } catch {
      } finally {
        checkingRevision = false;
      }
    }, 250).unref();
    setInterval(async () => {
      try {
        const v = await request("/v1/monitoring");
        for (const i of v.incidents) {
          if (alerts.has(i.id)) continue;
          alerts.add(i.id);
          if (Notification.isSupported())
            new Notification({
              title: `Resource ${i.health}`,
              body: i.detail,
            }).show();
        }
      } catch {}
    }, 15000).unref();
  })
  .catch((e) => {
    console.error("Desktop startup failed:", e.message);
    dialog.showErrorBox("Alshival.Ai", e.message);
    app.quit();
  });
app.on("window-all-closed", () => {
  if (testRoot) fallback?.kill();
  app.quit();
}); // Service deliberately stays running.
