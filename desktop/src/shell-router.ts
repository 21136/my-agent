import type { AgentWsClient } from "./api/ws";
import { mountUnifiedShell } from "./shells/unified";
import type { ShellId } from "./settings";

export type { ShellId };

export function mountShell(
  root: HTMLElement,
  shell: ShellId,
  client: AgentWsClient,
): () => void {
  return mountUnifiedShell(root, client, shell);
}
