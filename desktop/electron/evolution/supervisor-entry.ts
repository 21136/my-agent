import { serveControl, type HostBootstrap } from "./transport";
import type { EvolutionSupervisor } from "./supervisor";

/** Trusted composition boundary used by Electron main; importing this module is inert. */
export async function serveSupervisor(input:{bootstrap:HostBootstrap;supervisor:EvolutionSupervisor}):Promise<{close():Promise<void>}>{return await serveControl(input);}
