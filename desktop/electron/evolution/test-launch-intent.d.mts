import type { EvolutionTestHostContext } from "./test-host-config";
export function detectEvolutionTestLaunchIntent(appRoot: string, argv?: readonly string[], env?: NodeJS.ProcessEnv): boolean;
export function assertEvolutionTestProfileLoaded(testIntent: boolean, profile: EvolutionTestHostContext | null, loadError?: Error | null): void;
