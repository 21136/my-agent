import type { IpcMain } from "electron";
import type { createSelfEvolutionConfirmationService } from "./self-evolution-confirmation";

export const SELF_EVOLUTION_CONFIRMATION_IPC_CHANNEL = "self-evolution:request-native-confirmation";
type Service<Event> = ReturnType<typeof createSelfEvolutionConfirmationService<Event>>;

/** Bind one renderer invocation to the exact Electron event; extra args are rejected. */
export function registerSelfEvolutionConfirmationIpc<Event>(
  ipcMain: Pick<IpcMain, "handle">,
  service: Service<Event>,
): void {
  ipcMain.handle(SELF_EVOLUTION_CONFIRMATION_IPC_CHANNEL, (event, ...args) => {
    if (args.length !== 1) return { ok: false, code: "invalid_request" };
    return service.request(event as Event, args[0]);
  });
}
