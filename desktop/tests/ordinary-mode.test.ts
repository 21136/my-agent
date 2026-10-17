import assert from "node:assert/strict";
import test from "node:test";

import {
  applyProjectThreadsEvent,
  deriveHeaderNextStepView,
  deriveProjectGoalViewModel,
  ordinaryDraftDecisionVisible,
  renderChangeBanner,
  renderStagePlanCard,
  type ProjectPanelState,
} from "../src/shells/unified/project-panel.ts";
import {
  canRequestProjectDocs,
  noticeMirrorsToStatus,
  sessionBannerStatus,
  sessionListMeta,
  userFacingContextStatus,
  userFacingErrorStatus,
  userFacingThreadTitle,
} from "../src/shells/unified/ordinary-guards.ts";

function panel(overrides: Partial<ProjectPanelState> = {}): ProjectPanelState {
  const base = {
    projectId: "music-player",
    projectSummary: "",
    planStatus: "confirmed",
    tasksMarkdown: "",
    mapMarkdown: "",
    tasksDone: 0,
    tasksTotal: 1,
    tasksAllDone: false,
    planOverlay: null,
    projects: [],
    switchOverlay: null,
    switchInProgress: false,
    pendingPickerId: "",
    overlayPanel: null,
    railTab: "now",
    taskPhases: [],
    taskSnapshot: { lines: new Set<number>(), lineTexts: new Map<number, string>() },
    planBannerCollapsed: false,
    changeTimelineExpanded: false,
    switchConfirmTarget: null,
    projectSearchQuery: "",
    planChangeLog: [],
    changeTimeline: [],
    executionStage: "design",
    workflowStage: "implementation",
    needsDesignConfirm: false,
    executionStageStatus: "ready",
    executionStageReason: "",
    executionStageBlockers: [],
    executionStageMissing: [],
    executionStageWarnings: [],
    executionStageAffected: [],
    executionStageDeferred: [],
    executionStageArtifacts: [],
    highlightChanges: false,
    highlightedLines: new Set<number>(),
    projectDocs: [],
    currentDocPath: "",
    currentDocContent: "",
    newDocName: "",
    renamingDocPath: "",
    renameDraft: "",
    deleteConfirmPath: "",
    docView: "preview",
    docEditDraft: "",
    docDirty: false,
    docSaving: false,
    quickAddText: "",
    detectedProject: null,
    planWarnings: [],
    planWarningsPending: [],
    dismissedWarningFingerprint: "",
    planWarningStage: null,
    undoDescription: "",
    undoTimerId: null,
    degradationLevel: "L1",
    degradationLabel: "",
    changesLevel: null,
    dismissedTaskChangeFingerprint: "",
    autoConfirmTimerId: null,
    externalChanges: false,
    suggestions: [],
    autoFixNotices: [],
    operationalNotices: [],
    dismissedOperationalNoticeFingerprint: "",
    partnerNotices: [],
    partnerBusy: false,
    nextTask: null,
    nextTaskLine: null,
    services: [],
    servicesLoading: false,
    servicesError: "",
    servicesLogName: "",
    servicesLogText: "",
    terminalSessions: [],
    terminalsLoading: false,
    terminalsError: "",
    terminalsCollapsed: true,
    terminalDetails: null,
    terminalOutput: "",
    terminalOutputCursor: 0,
    terminalOutputLoading: false,
    terminalOutputError: "",
    terminalOutputCursorReset: false,
    terminalOutputTruncated: false,
    turnArmedId: "",
    turnArmedText: "",
    turnEvidence: [],
    turnGateNotice: "",
    turnPostcondition: "none",
    turnCircuitOpen: [],
    turnPlaybookId: "",
    turnFailureClass: "",
    lastTurnFinishReason: "",
    activeSessionId: "",
    threads: [],
    threadsLoading: false,
    currentSessionId: "",
    mainFocus: "chat",
    reviewFocusId: null,
    servicesCollapsed: true,
    suggestionAdoptFlash: null,
    adoptedFooterMessage: null,
    adoptPendingId: null,
    turnInProgress: false,
    deliveryProfile: "solo",
    reviewVerdict: null,
    reviewBlockersCount: 0,
    reviewProgressBlocked: false,
    scopeConfirmedAt: "",
    projectEntry: "",
    projectTemplate: "",
    runawayEnabled: false,
    runawayStatus: "",
    runawayCheckpoint: "",
    runawayRepairCount: 0,
    runawayLastVerification: null,
    runawayPausedReason: null,
    runawayAcceptancePassed: false,
    runawayVerificationEvidence: null,
    runawayVerificationEvidencePath: null,
    runawayVersion: 0,
    runawayPhase: "",
    runawayUserLine: "",
    runawayMode: "auto",
    runawayBlocked: false,
    runawayChecklist: null,
    runawayCancelAvailable: false,
    runawayBlock: null,
    scopeNeedsReconfirm: false,
    flowPreviewStage: null,
    milestoneAccepted: false,
    milestoneAcceptedAt: null,
    codeFollowup: null,
    nextTurnChangeSummary: null,
    dropTaskPendingId: null,
    dropTaskPendingWorking: false,
  } as ProjectPanelState;
  return { ...base, ...overrides };
}

test("confirmed plan does not fall back to first-round scope confirm", () => {
  const state = panel({
    planStatus: "confirmed",
    scopeConfirmedAt: "",
    tasksTotal: 1,
    executionStage: "requirements",
    workflowStage: "implementation",
    scopeNeedsReconfirm: true,
  });
  const view = deriveProjectGoalViewModel(state);
  assert.notEqual(view.title, "范围需要确认");
  assert.notEqual(view.action, "confirm-scope");
  assert.doesNotMatch(view.nextStep, /确认第一轮范围/);
  const header = deriveHeaderNextStepView(state);
  assert.equal(header.actions.some((item) => item.action === "confirm-scope"), false);
  const card = renderStagePlanCard(state);
  assert.equal(card.includes('data-action="confirm-scope"'), false);
  assert.equal(card.includes("确认第一轮范围后"), false);
  assert.equal(card.includes("先确认这一轮的范围"), false);
});

test("draft plan with open tasks still offers scope confirm before the plan is accepted", () => {
  const state = panel({
    planStatus: "pending",
    scopeConfirmedAt: "",
    tasksTotal: 1,
    executionStage: "requirements",
    workflowStage: "requirements",
    scopeNeedsReconfirm: false,
  });
  const view = deriveProjectGoalViewModel(state);
  assert.notEqual(view.title, "范围需要确认");
  assert.notEqual(view.action, "confirm-scope");
  const card = renderStagePlanCard(state);
  assert.equal(card.includes('data-action="confirm-scope"'), false);
});

test("stamped scope on a draft plan stays a soft signal", () => {
  const state = panel({
    planStatus: "draft",
    scopeConfirmedAt: "2026-10-10T00:00:00Z",
    tasksTotal: 0,
    executionStage: "requirements",
    workflowStage: "requirements",
  });
  const card = renderStagePlanCard(state);
  assert.doesNotMatch(card, /确认范围|范围已确认/);
  assert.equal(card.includes('data-action="confirm-scope"'), false);
});

test("draft decision is one surface after a reply-only turn", () => {
  const state = panel({
    planStatus: "draft",
    projectEntry: "",
    projectTemplate: "",
    tasksTotal: 1,
    executionStage: "requirements",
    workflowStage: "requirements",
    planOverlay: {
      requestId: "req-1",
      title: "计划待确认",
      summary: "music-player",
      tasksPreview: "- [ ] T-001",
      planStatus: "draft",
    },
  });
  assert.equal(ordinaryDraftDecisionVisible(state), false);
  const header = deriveHeaderNextStepView(state);
  assert.equal(header.actions.some((item) => item.action === "confirm-plan"), false);
  assert.equal(header.actions.some((item) => item.action === "direct-implement"), false);
  const banner = renderChangeBanner(state);
  assert.doesNotMatch(banner, /方案待确认|确认开工|修改计划/);
});

test("direct-entry draft still shows a plan banner when a confirm request arrives", () => {
  const state = panel({
    planStatus: "draft",
    projectEntry: "direct",
    projectTemplate: "",
    planOverlay: {
      requestId: "req-direct",
      title: "计划待确认",
      summary: "music-player",
      tasksPreview: "- [ ] T-001",
      planStatus: "draft",
    },
  });
  assert.equal(ordinaryDraftDecisionVisible(state), false);
  const banner = renderChangeBanner(state);
  assert.doesNotMatch(banner, /方案待确认|确认开工/);
});


test("ordinary draft and legacy stages do not expose blocking stage or plan CTAs", () => {
  const state = panel({
    planStatus: "draft",
    projectEntry: "",
    planOverlay: {
      requestId: "req-draft",
      title: "方案待确认",
      summary: "draft",
      tasksPreview: "- [ ] T-001",
      planStatus: "draft",
    },
    executionStage: "requirements",
    workflowStage: "documentation",
    needsDesignConfirm: true,
    tasksTotal: 1,
  });
  assert.equal(ordinaryDraftDecisionVisible(state), false);
  const header = deriveHeaderNextStepView(state);
  assert.equal(header.actions.some((item) => item.action === "confirm-plan"), false);
  assert.equal(header.actions.some((item) => item.action === "direct-implement"), false);
  assert.doesNotMatch(renderChangeBanner(state), /确认开工|方案待确认|确认变更/);
  assert.doesNotMatch(renderStagePlanCard(state), /确认范围|确认设计|规划后开工|直接实现/);
});

test("ordinary plan dirty and verification facts stay advisory while implementation remains available", () => {
  const state = panel({
    planStatus: "plan_dirty",
    executionStage: "verification",
    workflowStage: "verification",
    executionStageStatus: "blocked",
    executionStageReason: "verification_pending",
    tasksTotal: 1,
    tasksDone: 0,
    turnEvidence: [{ tool: "run_tests", ok: true }],
  });
  assert.equal(deriveProjectGoalViewModel(state).status, "decision");
  assert.doesNotMatch(renderChangeBanner(state), /方案有变更|确认变更|暂停并查看变更/);
  const card = renderStagePlanCard(state);
  assert.match(card, /验证|当前进展/);
  assert.doesNotMatch(card, /不能继续|确认方案/);
});

test("change banner does not render the CHG ledger", () => {
  const state = panel({
    planStatus: "confirmed",
    changeTimeline: [
      {
        change_id: "CHG-001",
        adopted_at: "2026-10-10T00:00:00Z",
        source: "adopt",
        proposal_id: "p-1",
        paths: ["src/app.py"],
        summary: "internal",
        requirements: ["REQ-001"],
        tasks: ["T-001"],
        acceptance: [],
        verification: [],
        stale_docs: [],
        replan_required: false,
        before_revision: "r1",
        after_revision: "r2",
      },
    ],
  });
  const banner = renderChangeBanner(state);
  assert.equal(banner.includes("CHG"), false);
  assert.equal(banner.includes("影响时间线"), false);
  assert.equal(banner.includes("toggle-change-timeline"), false);
});

test("a failed tool inside a completed ordinary turn does not fail the project", () => {
  const state = panel({
    turnInProgress: false,
    lastTurnFinishReason: "stop",
    turnFailureClass: "A",
    turnEvidence: [
      { tool: "patch_file", ok: false },
      { tool: "read_file", ok: true },
    ],
    executionStage: "implementation",
    workflowStage: "implementation",
  });
  const view = deriveProjectGoalViewModel(state);
  assert.notEqual(view.status, "failed");
  assert.notEqual(view.title, "上一步没有完成");
  assert.doesNotMatch(view.nextStep, /查看失败原因/);
  const header = deriveHeaderNextStepView(state);
  assert.equal(header.actions.some((item) => item.label === "查看失败原因"), false);
});

test("an aborted or failed ordinary turn still marks the project failed", () => {
  for (const reason of ["error", "cancelled", "timeout", "tool_loop_exceeded"]) {
    const state = panel({
      turnInProgress: false,
      lastTurnFinishReason: reason,
      turnEvidence: [{ tool: "patch_file", ok: false }],
      executionStage: "implementation",
      workflowStage: "implementation",
    });
    const view = deriveProjectGoalViewModel(state);
    assert.equal(view.title, "上一步没有完成", reason);
    assert.match(view.nextStep, /查看失败原因/);
  }
  const stageFailed = panel({
    lastTurnFinishReason: "stop",
    executionStageStatus: "failed",
    executionStage: "implementation",
    workflowStage: "implementation",
  });
  assert.equal(deriveProjectGoalViewModel(stageFailed).title, "上一步没有完成");
  const postcondition = panel({
    lastTurnFinishReason: "stop",
    turnPostcondition: "fail",
    executionStage: "implementation",
    workflowStage: "implementation",
  });
  assert.equal(deriveProjectGoalViewModel(postcondition).title, "上一步没有完成");
});

test("a model-service error leaves the ordinary project goal unchanged", () => {
  const base = {
    turnInProgress: false,
    planStatus: "draft" as const,
    tasksTotal: 1,
    tasksDone: 0,
    tasksAllDone: false,
    projectEntry: "",
    projectTemplate: "",
    executionStage: "requirements",
    workflowStage: "requirements",
    executionStageStatus: "ready",
    turnFailureClass: "",
  };
  const live = deriveProjectGoalViewModel(panel({
    ...base,
    lastTurnFinishReason: "provider_error",
  }));
  const reloaded = deriveProjectGoalViewModel(panel({
    ...base,
    lastTurnFinishReason: "",
  }));
  assert.equal(live.title, reloaded.title);
  assert.equal(live.nextStep, reloaded.nextStep);
  assert.equal(live.summary, reloaded.summary);
  assert.equal(live.status, reloaded.status);
  assert.equal(live.title, "准备开始下一步");
  assert.doesNotMatch(live.summary, /最近一次执行或验证没有通过/);
  assert.doesNotMatch(live.nextStep, /查看失败原因/);
});

test("docs requests and error status stay useful without a project", () => {
  assert.equal(canRequestProjectDocs(""), false);
  assert.equal(canRequestProjectDocs("  "), false);
  assert.equal(canRequestProjectDocs("music-player"), true);
  assert.equal(userFacingErrorStatus("no active project session"), "请先打开项目");
  assert.equal(userFacingErrorStatus("错误"), "出了点问题，请重试");
  assert.equal(userFacingErrorStatus(""), "出了点问题，请重试");
  assert.match(userFacingErrorStatus("disk full"), /^出错了：/);
});

test("session ids stay out of status, notices, and thread titles", () => {
  const raw = "续接项目 music-player 的专用会话 20261010-034c1c9d";
  assert.equal(noticeMirrorsToStatus(raw), false);
  assert.equal(noticeMirrorsToStatus("已续接项目 music-player"), false);
  assert.equal(noticeMirrorsToStatus("狂奔已恢复"), true);
  assert.equal(userFacingContextStatus(raw), "已打开项目");
  assert.equal(sessionBannerStatus("0x567-flash", "普通"), "0x567-flash · 普通");
  assert.equal(sessionBannerStatus("0x567-flash", ""), "0x567-flash");
  assert.equal(sessionListMeta("", "20261010-034c1c9d"), "会话记录");
  assert.equal(userFacingThreadTitle("", "20261010-034c1c9d"), "未命名会话");
  assert.equal(userFacingThreadTitle("20261010-034c1c9d", "20261010-034c1c9d"), "未命名会话");
  assert.equal(userFacingThreadTitle("播放器讨论", "20261010-034c1c9d"), "播放器讨论");

  const state = panel();
  applyProjectThreadsEvent(state, {
    type: "project.threads",
    project_id: "music-player",
    active_session_id: "20261010-034c1c9d",
    archived_session_ids: [],
    threads: [
      {
        session_id: "20261010-034c1c9d",
        title: "20261010-034c1c9d",
        archived: false,
      },
    ],
    is_active: true,
  });
  assert.equal(state.threads[0]?.title, "未命名会话");
});

test("automatic project plan card avoids the old mode name", () => {
  const html = renderStagePlanCard(panel({
    runawayEnabled: true,
    runawayVersion: 2,
    executionStage: "design",
    workflowStage: "design",
  }));
  assert.doesNotMatch(html, /狂奔/);
});
