'use strict';
const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("managedConfirm", {
  reply(ok) {
    ipcRenderer.send("managed-confirm-result", Boolean(ok));
  },
});
