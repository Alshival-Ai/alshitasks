const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld(
  "tasks",
  Object.freeze({
    request: (path, method = "GET", body) =>
      ipcRenderer.invoke("tasks:request", path, method, body),
    windowAction: (action) => ipcRenderer.invoke("tasks:window", action),
    theme: (preference) => ipcRenderer.invoke("tasks:theme", preference),
    onWindowState: (callback) => {
      const listener = (_event, maximized) => callback(maximized);
      ipcRenderer.on("tasks:window-state", listener);
      return () => ipcRenderer.removeListener("tasks:window-state", listener);
    },
    connect: () => ipcRenderer.invoke("tasks:connect"),
    importAttachment: (item) => ipcRenderer.invoke("tasks:import", item),
    saveAttachment: (id) => ipcRenderer.invoke("tasks:save-attachment", id),
    backup: () => ipcRenderer.invoke("tasks:backup"),
    restore: () => ipcRenderer.invoke("tasks:restore"),
    external: (url) => ipcRenderer.invoke("tasks:external", url),
    service: (action) => ipcRenderer.invoke("tasks:service", action),
  }),
);
