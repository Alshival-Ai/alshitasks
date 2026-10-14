const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld(
  "tasks",
  Object.freeze({
    request: (path, method = "GET", body) =>
      ipcRenderer.invoke("tasks:request", path, method, body),
    getUpdates: () => ipcRenderer.invoke("tasks:updates-get"),
    checkUpdates: () => ipcRenderer.invoke("tasks:updates-check"),
    setAutomaticUpdates: enabled => ipcRenderer.invoke("tasks:updates-enabled", enabled),
    installUpdate: relaunch => ipcRenderer.invoke("tasks:updates-install", relaunch),
    onUpdates: callback => {
      const listener = (_event, state) => callback(state);
      ipcRenderer.on("tasks:updates-changed", listener);
      return () => ipcRenderer.removeListener("tasks:updates-changed", listener);
    },
    onUpdatesOpen: callback => {
      const listener = () => callback();
      ipcRenderer.on("tasks:updates-open", listener);
      return () => ipcRenderer.removeListener("tasks:updates-open", listener);
    },
    getStartup: () => ipcRenderer.invoke("tasks:startup-get"),
    setStartup: (enabled) => ipcRenderer.invoke("tasks:startup-set", enabled),
    windowAction: (action) => ipcRenderer.invoke("tasks:window", action),
    theme: (preference) => ipcRenderer.invoke("tasks:theme", preference),
    onWindowState: (callback) => {
      const listener = (_event, maximized) => callback(maximized);
      ipcRenderer.on("tasks:window-state", listener);
      return () => ipcRenderer.removeListener("tasks:window-state", listener);
    },
    initialNotification: () => ipcRenderer.invoke("tasks:notification-initial"),
    onNotification: (callback) => {
      const listener = (_event, id) => callback(id);
      ipcRenderer.on("tasks:notification", listener);
      return () => ipcRenderer.removeListener("tasks:notification", listener);
    },
    onChanged: (callback) => {
      const listener = () => callback();
      ipcRenderer.on("tasks:changed", listener);
      return () => ipcRenderer.removeListener("tasks:changed", listener);
    },
    connect: () => ipcRenderer.invoke("tasks:connect"),
    importAttachment: (item, replaces) =>
      ipcRenderer.invoke("tasks:import", item, replaces),
    importSshKey: (resource, settings) =>
      ipcRenderer.invoke("tasks:ssh-key", resource, settings),
    saveAttachment: (id) => ipcRenderer.invoke("tasks:save-attachment", id),
    backup: () => ipcRenderer.invoke("tasks:backup"),
    restore: () => ipcRenderer.invoke("tasks:restore"),
    external: (url) => ipcRenderer.invoke("tasks:external", url),
    service: (action) => ipcRenderer.invoke("tasks:service", action),
  }),
);
