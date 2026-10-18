/**
 * 过程 (process) pill placement + visibility rules — pure, DOM-free.
 *
 * Placement (one rule for live, finished, restored and toggled turns):
 * a process block renders directly AFTER the reply it produced, i.e. after the
 * first assistant / assistant-streaming block that follows it in model order.
 * A process with no reply after it (tools still running, confirm pending, error
 * or cancelled turns) keeps its model position. The order never depends on the
 * collapsed state, so toggling, re-rendering, reloading history or finishing the
 * stream cannot move the pill.
 *
 * Only `import type` here so node --experimental-strip-types can load it.
 */
import type { ProcessActivityBlock } from "./activity-state";

type BlockLike = { kind: string };

function isReplyBlock(block: BlockLike): boolean {
  return block.kind === "assistant" || block.kind === "assistant-streaming";
}

export function orderTurnBlocksForDisplay<T extends BlockLike>(blocks: readonly T[]): T[] {
  const deferred = new Map<number, T[]>();
  const out: T[] = [];
  for (let i = 0; i < blocks.length; i++) {
    const block = blocks[i];
    if (block.kind === "process") {
      let reply = -1;
      for (let j = i + 1; j < blocks.length; j++) {
        if (isReplyBlock(blocks[j])) {
          reply = j;
          break;
        }
      }
      if (reply >= 0) {
        const list = deferred.get(reply) ?? [];
        list.push(block);
        deferred.set(reply, list);
        continue;
      }
    }
    out.push(block);
    const after = deferred.get(i);
    if (after) out.push(...after);
  }
  return out;
}

/** True when a process block has nothing to show (no tools, no thinking text, not waiting). */
export function isProcessBlockEmpty(block: ProcessActivityBlock): boolean {
  if (block.llmPending) return false;
  if ((block.tools?.length ?? 0) > 0) return false;
  if ((block.reasoning ?? "").trim()) return false;
  if (block.reasoningPhase === "streaming") return false;
  for (const entry of block.entries ?? []) {
    if (entry.kind === "tool") return false;
    if (entry.phase === "streaming") return false;
    if (entry.text.trim()) return false;
  }
  return true;
}
