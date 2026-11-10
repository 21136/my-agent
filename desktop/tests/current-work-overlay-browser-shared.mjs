/**
 * Shared Win32 overlay helpers: composer confirms + 当前工作 popover (companion home).
 */
export {
  EDGE_LAUNCH_ARGS,
  edge,
  pw,
  resolvePython,
  acceptComposerConfirmById,
  latestUnsettledConfirmRequestId,
} from "./multi-project-schedule-real-host-browser-shared.mjs";

export const CURRENT_WORK_ENTRY = 'button.unified-topbar-current-work[aria-controls="unified-current-work-popover"]';

export async function acceptLatestComposerConfirm(page, { timeout = 45_000, afterConfirmRequestCount = 0 } = {}) {
  await page.waitForFunction(
    min => {
      const events = window.receivedEvents ?? [];
      const requests = events.filter(event => event.type === "confirm.request");
      if (requests.length > min) {
        for (let index = requests.length - 1; index >= min; index -= 1) {
          const id = requests[index]?.request_id;
          if (typeof id !== "string" || !id) continue;
          const settled = events.some(event => event.type === "confirm.done"
            && event.request_id === id && ["y", "n", "timeout"].includes(event.choice));
          if (!settled) return true;
        }
      }
      return Boolean(document.querySelector("[data-pending-confirm-hint]"));
    },
    afterConfirmRequestCount,
    { timeout },
  );

  const deadline = Date.now() + timeout;
  let requestId = "";
  while (Date.now() < deadline) {
    requestId = await page.evaluate(min => {
      const events = window.receivedEvents ?? [];
      const requests = events.filter(event => event.type === "confirm.request");
      for (let index = requests.length - 1; index >= min; index -= 1) {
        const id = requests[index]?.request_id;
        if (typeof id !== "string" || !id) continue;
        const settled = events.some(event => event.type === "confirm.done"
          && event.request_id === id && ["y", "n", "timeout"].includes(event.choice));
        if (!settled) return id;
      }
      return "";
    }, afterConfirmRequestCount);
    if (requestId) break;
    await page.waitForTimeout(100);
  }
  if (!requestId) throw new Error("unsettled confirm.request missing");

  const { acceptComposerConfirmById } = await import("./multi-project-schedule-real-host-browser-shared.mjs");
  const accepted = await acceptComposerConfirmById(page, requestId, { timeout: Math.max(deadline - Date.now(), 1_000) });
  if (!accepted) throw new Error(`composer confirm not accepted: ${requestId}`);
  return requestId;
}

/** Visible 进行中 tab: excludes hidden `cw-contract-body` duplicate markup. */
export function currentWorkQueueBody(workRoot) {
  return workRoot.locator('[data-cw-segment-panel="queue"]:not([hidden]) .current-work-zone-body');
}

export function currentWorkItemByTitle(workRoot, title) {
  return currentWorkQueueBody(workRoot)
    .locator(`.unified-current-work-item:has(strong:text-is("${title}"))`);
}

/** One session-level reorganize bar (independent items are one group per item in API). */
export function currentWorkReorganizeBar(workRoot) {
  return currentWorkQueueBody(workRoot).locator("[data-cw-reorganize-bar]").first();
}

export async function openCurrentWorkOverlay(page) {
  await page.waitForFunction(() => window.receivedEvents?.some(e => e.type === "session.banner"));
  const entry = page.locator(CURRENT_WORK_ENTRY);
  await entry.waitFor({ state: "visible" });
  await entry.click();
  await page.waitForFunction(() => document.querySelector(".unified-shell.is-current-work-overlay"));
  await page.waitForFunction(() => {
    const popover = document.querySelector("#unified-current-work-popover");
    const chat = document.querySelector(".unified-stage > .unified-chat");
    return popover instanceof HTMLElement
      && !popover.hidden
      && chat instanceof HTMLElement
      && getComputedStyle(chat).flexBasis !== "50%";
  });
  await page.waitForFunction(() => window.receivedEvents?.some(e => e.type === "work-records.overview.result"));
  return page.locator("#unified-current-work");
}

export async function waitControlDone(page, action, status = "applied") {
  await page.waitForFunction(
    ([expectedAction, expectedStatus]) => window.receivedEvents?.some(event => event.type === "work-record.control.done"
      && event.action === expectedAction && event.status === expectedStatus),
    [action, status],
    { timeout: 60_000 },
  );
}

export async function refreshCurrentWorkOverview(page) {
  const before = await page.evaluate(() => window.receivedEvents?.filter(e => e.type === "work-records.overview.result").length ?? 0);
  await page.evaluate(() => window.client.queryWorkRecordsOverview());
  await page.waitForFunction(min => (window.receivedEvents?.filter(e => e.type === "work-records.overview.result").length ?? 0) > min, before);
  return page.evaluate(() => window.receivedEvents?.findLast(e => e.type === "work-records.overview.result"));
}

/** Wait for shell-registered overview refresh after an applied control (revision CAS). */
export async function waitOverviewAfterAppliedControl(page, action, { timeout = 20_000 } = {}) {
  await page.waitForFunction(
    act => {
      const events = window.receivedEvents ?? [];
      const doneIndex = events.findLastIndex(event => event.type === "work-record.control.done"
        && event.action === act && event.status === "applied");
      if (doneIndex < 0) return false;
      const later = events.slice(doneIndex + 1).find(event => event.type === "work-records.overview.result");
      return Boolean(later && Number.isSafeInteger(later.work_record_revision));
    },
    action,
    { timeout },
  );
}
