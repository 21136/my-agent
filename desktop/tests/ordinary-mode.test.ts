import assert from "node:assert/strict";
import test from "node:test";

import {
  applyProjectThreadsEvent,
  deriveProjectGoalViewModel,
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
    mapMarkdown: "",
    projects: [],
    switchOverlay: null,
    switchInProgress: false,
    pendingPickerId: "",
    overlayPanel: null,
    railTab: "now",
    switchConfirmTarget: null,
    projectSearchQuery: "",
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
    detectedProject: null,
    undoDescription: "",
    undoTimerId: null,
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
    servicesCollapsed: true,
    turnInProgress: false,
    deliveryProfile: "solo",
    reviewVerdict: null,
    reviewBlockersCount: 0,
    reviewProgressBlocked: false,
    scopeConfirmedAt: "",
    projectEntry: "",
    projectTemplate: "",
    scopeNeedsReconfirm: false,
    flowPreviewStage: null,
    milestoneAccepted: false,
    milestoneAcceptedAt: null,
  } as ProjectPanelState;
  return { ...base, ...overrides };
}

test("confirmed plan does not fall back to first-round scope confirm", () => {
  const state = panel({
    planStatus: "confirmed",
    scopeConfirmedAt: "",
    executionStage: "requirements",
    workflowStage: "implementation",
    scopeNeedsReconfirm: true,
  });
  const view = deriveProjectGoalViewModel(state);
  assert.notEqual(view.title, "范围需要确认");
  assert.notEqual(view.action, "confirm-scope");
  assert.doesNotMatch(view.nextStep, /确认第一轮范围/);
});

test("draft plan does not ask for scope confirm before the plan is accepted", () => {
  const state = panel({
    planStatus: "pending",
    scopeConfirmedAt: "",
    executionStage: "requirements",
    workflowStage: "requirements",
    scopeNeedsReconfirm: false,
  });
  const view = deriveProjectGoalViewModel(state);
  assert.notEqual(view.title, "范围需要确认");
  assert.notEqual(view.action, "confirm-scope");
});

test("ordinary plan dirty and verification facts stay advisory while implementation remains available", () => {
  const state = panel({
    planStatus: "plan_dirty",
    executionStage: "verification",
    workflowStage: "verification",
    executionStageStatus: "blocked",
    executionStageReason: "verification_pending",
    turnEvidence: [{ tool: "run_tests", ok: true }],
  });
  assert.equal(deriveProjectGoalViewModel(state).status, "decision");
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
