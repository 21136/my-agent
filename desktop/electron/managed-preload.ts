import { contextBridge, ipcRenderer } from "electron";

// A deliberately narrow surface. The managed Host validates each request and
// supplies its private Python control token outside the renderer process.
contextBridge.exposeInMainWorld("myAgentManaged", {
  health: () => ipcRenderer.invoke("managed:health"),
  listSessions: () => ipcRenderer.invoke("managed:session-list"),
  createSession: (input: { sessionId: string; title: string }) =>
    ipcRenderer.invoke("managed:session-create", input),
  readSession: (sessionId: string) => ipcRenderer.invoke("managed:session-read", sessionId),
  appendDocument: (input: { sessionId: string; text: string }) =>
    ipcRenderer.invoke("managed:document-append", input),
  getMode: () => ipcRenderer.invoke("managed:mode"),
});
