import type { AgentWsClient } from "./api/ws";
import { mountGrowShell } from "./shells/grow";
import { mountDailyShell } from "./shells/daily";
import { mountGovernPlaceholder } from "./shells/govern";
import { mountProjectShell } from "./shells/project";
import type { ShellId } from "./settings";

export type { ShellId };

export function mountShell(
  root: HTMLElement,
  shell: ShellId,
  client: AgentWsClient,
): () => void {
  switch (shell) {
    case "grow":
      return mountGrowShell(root, client, shell);
    case "daily":
      return mountDailyShell(root, client, shell);
    case "project":
      return mountProjectShell(root, client, shell);
    case "govern":
      return mountGovernPlaceholder(root);
    default:
      return mountGrowShell(root, client, "grow");
  }
}
