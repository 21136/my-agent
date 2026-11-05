import assert from "node:assert/strict";
import test from "node:test";
import {
  WORK_CARD_DEFAULT_VISIBLE_REQUIREMENTS,
  renderWorkCardSnapshot,
} from "../src/shells/unified/work-card-presentation.ts";

const sessionId = "session-default-rows";
const cardId = `wc-${"a".repeat(64)}`;

function legacyCard(requirementCount: number, viewedAt: string | null = null) {
  const requirements = Array.from({ length: requirementCount }, (_, index) => ({
    requirement_id: `req-${index}`,
    text: `要求 ${index}`,
    status: index === 0 ? "blocked" : "pending",
  }));
  return {
    card_id: cardId,
    saved_at: "2026-01-01T00:00:00Z",
    viewed_at: viewedAt,
    snapshot: {
      session_id: sessionId,
      completeness: "complete",
      captured_at: "2026-01-01T00:00:00Z",
      cards: [{
        title: "演示项目",
        status: "active",
        requirements,
      }],
    },
  };
}

test("work card snapshots default to five ranked requirements with expand affordance", () => {
  assert.equal(WORK_CARD_DEFAULT_VISIBLE_REQUIREMENTS, 5);
  const html = renderWorkCardSnapshot(legacyCard(6));
  const defaultList = html.split('<details class="work-card-all"')[0];
  assert.equal((defaultList.match(/class="work-card-requirement"/g) || []).length, 5);
  assert.match(html, /查看全部 6 项/);
  assert.match(html, /work-card-all-list[\s\S]*req-5/);
  assert.doesNotMatch(defaultList, /req-5/);
  assert.match(defaultList, /work-card-requirement-text">要求 0/);
});

test("viewed_at surfaces in the card header without implying acceptance", () => {
  const unknown = renderWorkCardSnapshot(legacyCard(1, null));
  assert.match(unknown, /卡片查看状态未确认/);
  const viewed = renderWorkCardSnapshot(legacyCard(1, "2026-01-02T10:15:00+08:00"));
  assert.match(viewed, /已查看详情/);
  assert.match(viewed, /2026-01-02T10:15:00\+08:00/);
  assert.match(viewed, /不代表认可或验收/);
});
