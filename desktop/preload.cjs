const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld(
  "tasks",
  Object.freeze({
    request: (path, method = "GET", body) =>
      ipcRenderer.invoke("tasks:request", path, method, body),
    connect: (origin) => ipcRenderer.invoke("tasks:connect", origin),
    importAttachment: (item) => ipcRenderer.invoke("tasks:import", item),
    saveAttachment: (id) => ipcRenderer.invoke("tasks:save-attachment", id),
    backup: () => ipcRenderer.invoke("tasks:backup"),
    restore: () => ipcRenderer.invoke("tasks:restore"),
    external: (url) => ipcRenderer.invoke("tasks:external", url),
    service: (action) => ipcRenderer.invoke("tasks:service", action),
  }),
);
