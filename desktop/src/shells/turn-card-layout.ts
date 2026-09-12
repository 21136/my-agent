import type { ChatBlock } from "./chat-state";
import { adoptPathFromNotice } from "./unified/output-display";

export type TurnCardGroup = {
  turnIndex: number;
  turnKey: string;
  blocks: ChatBlock[];
};

export type ChatRenderSegment =
  | { kind: "orphan"; blocks: ChatBlock[] }
  | { kind: "turn-card"; group: TurnCardGroup };

function blockStartsTurn(block: ChatBlock): number | null {
  if (block.kind === "user" || block.kind === "plan-subagent" || block.kind === "review-subagent") {
    return block.turnIndex;
  }
  return null;
}

export function liveTurnCardProcessBlocks(
  blocks: ChatBlock[],
  currentTurnIndex: number,
): Array<Extract<ChatBlock, { kind: "process" }>> {
  const segments = segmentChatBlocks(blocks);
  for (let i = segments.length - 1; i >= 0; i--) {
    const seg = segments[i];
    if (seg.kind !== "turn-card") continue;
    if (seg.group.turnIndex === currentTurnIndex) {
      return seg.group.blocks.filter((b): b is Extract<ChatBlock, { kind: "process" }> => b.kind === "process");
    }
  }
  const last = segments[segments.length - 1];
  if (last?.kind === "turn-card") {
    return last.group.blocks.filter((b): b is Extract<ChatBlock, { kind: "process" }> => b.kind === "process");
  }
  return [];
}

/** Group chat blocks into turn cards (UX-028 M1). */
export function segmentChatBlocks(blocks: ChatBlock[]): ChatRenderSegment[] {
  const segments: ChatRenderSegment[] = [];
  let orphanBuf: ChatBlock[] = [];
  let current: TurnCardGroup | null = null;

  const flushOrphans = () => {
    if (!orphanBuf.length) return;
    segments.push({ kind: "orphan", blocks: orphanBuf });
    orphanBuf = [];
  };

  const flushTurn = () => {
    if (current?.blocks.length) {
      segments.push({ kind: "turn-card", group: current });
    }
    current = null;
  };

  const attachToCurrent = (block: ChatBlock) => {
    if (!current) {
      orphanBuf.push(block);
      return;
    }
    if (block.kind === "process") {
      current.turnKey = block.turnKey;
    } else if (block.kind === "assistant-streaming" && !current.turnKey) {
      current.turnKey = block.turnKey;
    }
    current.blocks.push(block);
  };

  for (const block of blocks) {
    const startTurn = blockStartsTurn(block);
    if (startTurn !== null) {
      flushOrphans();
      flushTurn();
      current = { turnIndex: startTurn, turnKey: "", blocks: [block] };
      continue;
    }

    if (block.kind === "notice" && adoptPathFromNotice(block.text)) {
      attachToCurrent(block);
      continue;
    }

    if (block.kind === "notice" && !current) {
      orphanBuf.push(block);
      continue;
    }

    attachToCurrent(block);
  }

  flushOrphans();
  flushTurn();
  return segments;
}

export function segmentPrint(
  segment: ChatRenderSegment,
  blockPrint: (block: ChatBlock) => string,
): string {
  if (segment.kind === "orphan") {
    return `O:${segment.blocks.map(blockPrint).join("|")}`;
  }
  const { turnIndex, turnKey, blocks } = segment.group;
  return `T:${turnIndex}:${turnKey}:${blocks.map(blockPrint).join("|")}`;
}

export function renderTurnCardShell(
  group: TurnCardGroup,
  innerHtml: string,
  opts: { live: boolean },
): string {
  const liveCls = opts.live ? " is-live" : "";
  const turnKeyAttr = group.turnKey
    ? ` data-turn-key="${group.turnKey.replace(/"/g, "&quot;")}"`
    : "";
  return `<section class="unified-turn-card${liveCls}" data-turn-index="${group.turnIndex}"${turnKeyAttr} aria-label="对话回合">${innerHtml}</section>`;
}
