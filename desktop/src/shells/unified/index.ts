import { createShellDevAdoptionController, formatDualLaneStrip } from "./shell-dev-adoption";
import { hydrateAgentImageElements, hydrateAgentTextElements, hydrateAgentVideoElements } from "../../agent-image-hydrate";
import { bindImageLightboxClick } from "../../image-lightbox";
import { setAgentBusy } from "../../agent-busy";
import { wireComposerAttachments } from "../../composer-attachments";
import { mountFileDrop } from "../../file-drop";
import { hydrateMermaid, renderMarkdown } from "../../markdown";
import { bindLazyMarkdownLookup, observeLazyMarkdown, resetLazyMarkdownObserver } from "../../lazy-markdown";
import { formatUserMessageHtml, parseUserMessage } from "../../user-message";
import { messageReadingLayout, readingBodyHtml, prepareLazyReadingBody } from "./message-reading";
import { activityRevealScrollDelta } from "./activity-scroll";
import { ProjectWorkerProgressStatus, PROJECT_WORKER_PROGRESS_TTL_MS } from "./project-worker-progress";
import { formatTerminalOutputForDisplay } from "./terminal-output";
import { segmentChatBlocks, segmentPrint, renderTurnCardShell, liveTurnCardProcessBlocks, type ChatRenderSegment } from "../turn-card-layout";
import { activityEntriesPrint, ensureProcessEntries, isProcessThinkingLive, type ProcessActivityBlock, type ThinkEntry } from "../activity-state";
import { isProcessBlockEmpty, orderTurnBlocksForDisplay } from "../process-placement";
import { getProcessTools, renderActivityTimeline, syncThinkDom, timelineHasThinking } from "../activity-timeline";
import { canSubmitOrdinaryConfirm, chatSessionAwaitingUserConfirm, companionHomeComposerOwnsOrdinaryConfirm, composerShouldShowOrdinaryConfirmActions, createChatSession, escapeHtml, ordinaryConfirmHasOverlayPrimarySurface, ordinaryConfirmWouldRenderInChat, primaryPendingConfirmPreview, resolveActiveOrdinaryConfirmRequestId, syncOrdinaryConfirmPending, turnEndStatusText, checkerVerdictStatusText, formatToolElapsed, isConfirmInProgressLabel, userMessagePersistenceLabel, type ChatBlock } from "../chat-state";
import {
  parallelChatDeliveryLabel,
  parallelChatUserFootnote,
  polishStatusBarNotice,
  polishSystemNoticeText,
  toolPromptChatStatusLabel,
  workInstructionChatStatusLabel,
} from "./system-notice-copy";
import { settledToolStatus } from "../tool-outcome";
import { renderTopbar, type TopbarState } from "./topbar";
import { renderProposals, currentProposal, nextProposalIndex, type ProposalsState } from "./proposals";
import { renderMemoryShelf, memoryShelfLabel, type MemoryShelfState } from "./memory-shelf";
import {
  canRequestProjectDocs,
  noticeMirrorsToStatus,
  sessionBannerStatus,
  sessionListMeta,
  userFacingContextStatus,
  userFacingErrorStatus,
} from "./ordinary-guards";
import {
  setupProjectPanel,
  renderProjectSidebar,
  renderProjectGoalCard,
  deriveProjectGoalViewModel,
  hasProjectBlocker,
  renderProjectBlockerDetails,
  applyProjectStateEvent,
  resetProjectScopedState,
  beginAtomicProjectSwitch,
  shouldApplyProjectEvent,
  applyProjectListEvent,
  applyProjectThreadsEvent,
  isViewingArchivedThread,
  renderRailStageEmpty,
  renderProjectsStage,
  type FlowStage,
  type ProjectPanelState,
  type ProjectPanelCallbacks,
} from "./project-panel";
import {
  displayDocTitle,
  docCatalogMenuItems,
  docStem,
  finalizeDocumentOutline,
  mountPopupMenu,
  normalizeNewDocPath,
  renderDocOutlineHtml,
  renderDocumentReaderHtml,
  renameDraftForDoc,
  sortProjectDocs,
} from "./doc-reading";
import { isRailTab, mainFocusForRail, type MainFocus, type RailTab } from "./rail";
import {
  renderSessionFiltersHtml,
  renderSessionListHtml,
  type SessionFilter,
  type SessionListInput,
} from "./session-list";
import { isCrossProjectSession } from "./project-switch-state";
import {
  adoptPathFromNotice,
  lastFailedTool,
  processPillLabel,
  renderAdoptChip,
  renderReviewCallout,
  renderToolFailAlert,
  resolveEffectiveToolName,
} from "./output-display";
import { renderProjectCompanionDockHtml } from "./sidebar-companion";
import { bindChatFontControl, renderChatFontControl, syncChatFontControl } from "./chat-font-control";
import { isSettingsSection, renderSettingsPane, renderSettingsSidebar, type SettingsSection } from "./settings-panel";
import { writeTheme } from "../../settings";
import { renderMusicPanel } from "./music-panel";
import { renderShellDevPanel } from "./shell-dev-panel";
import { shellDevPreviewBounds } from "./shell-dev-view-bounds";
import { createManagedReviewController } from "./shell-dev-review";
import { mountManagedReviewBrowser } from "./managed-review-browser";
import { mountManagedSourceSelection } from "./managed-source-selection";
import type { AgentWsClient, ApprovalPreferenceMode, LlmModelListItem, LlmRoleModelPreferences, MemoryItem, ProposalItem, ServerEvent, WorkRecordsOverviewResultEvent } from "../../api/ws";
import { renderGoalBudgetNotice, renderHistoricalGoalPauseMarker } from "./goal-budget";
import { buildScheduleDispatchWorkers, buildScheduleLaunchMessage, renderScheduleLaunchForm, scheduledWorkerCandidates } from "./schedule-launch";
import { appendMergedHistoryPage, clearMergedHistory, type MergedHistoryRow, type MergedHistoryState } from "./merged-history-pages";
import { evolutionView, type EvolutionProjection, type EvolutionView } from "../../evolution-state";
import { parseProjectWorkerVerification, parseWorkRecordAutoCandidate, renderConfirmCardHtml, renderComposerOrdinaryConfirmDetailHtml, summarizeConfirmPreview, isWorkCardRecallPreview, isConfirmChoiceAllowed, renderWorkCardRecallReadable, renderWorkCardRecallBatch, parseWorkRecordReplyExcerpt, isProjectWorkerBatchAuthorizationPreview, renderProjectWorkerBatchAuthorizationHtml } from "./confirm-preview";
import { WorkCardPresentationRegistry, WorkCardResultExcerptView, renderWorkCardEntry, requestHistoryCardRead, failPendingHistoryCardRead, shouldClearPendingHistoryCardReads, clearPendingHistoryCardReads, bindWorkCardResultViewEvents, bindWorkCardSnapshotViewEvents, receiveWorkCardPresentation, companionHomeDefersWorkCardChatPresentation } from "./work-card-presentation";
import { mergeCurrentWorkOverviewDetailPage, appendCurrentWorkOverviewPage, buildCurrentWorkOverviewPanel, collectWorkRecordDependencyRefs, renderCurrentWorkWorkbenchPage, currentWorkOverviewRecheckDelayMs, dispatchCurrentWorkOverviewRequest, shouldFallbackOverviewDetailPaging, shouldRefreshOverviewForExecutionState, shouldRestartOverviewAfterExpiredCursor, shouldScheduleOverviewVisibilityRecheck } from "./current-work-overview";
import type { CurrentWorkLiveSessionInput } from "./current-work-live-session";
import { mountToolDisplayEditor, openToolDisplayEditor } from "../../copy/tool-display-editor";
import { onToolDisplayOverridesChange } from "../../copy/tool-display-overrides";
import {
  addCompanionSticker,
  ambientRoutineStatus,
  buildCompanionSyncPayload,
  calibrateCompanionFirstMetFromSessions,
  currentPartnerName,
  loadCompanionBond,
  petAvatarSrc,
  petTurnState,
  recordCompanionCare,
  removeCompanionSticker,
  renderIntimatePresenceStageHtml,
  renderPetAvatarHtml,
  renderPetNameHtml,
  touchCompanionVisit,
  type PetTurnSummary,
} from "./companion";
import { advanceCompanionWorld, recordExplicitSharedEvent, type CompanionWorldState } from "./companion-world";
import { loadCompanionWorld, saveCompanionWorld } from "./companion-world-store";
import { buildCompanionWorldTurnContext } from "./companion-world-context";
import { verifiedProjectEventToSharedEvent } from "./companion-world-app-events";
import { normalizeTurnEvidenceEvent } from "./turn-evidence";
import "./unified.css";
import "./topbar-shortcuts.css";
import "./work-card.css";
import "./current-work-overview.css";
import "./schedule-launch.css";
import { projectRecentWorkActivity } from "./work-activity";
import "./neon-twin.css";
import "./composer-companion.css";
import "./sidebar-v2.css";
import "./message-reading.css";
import "./settings-panel.css";
import "./music-panel.css";
import "./shell-dev-panel.css";
import "./single-chat-tools.css";

export type Perspective = "default" | "project" | "night";

const FOCUS_TURNS = 2;
const RECALL_TURNS = 3;

async function openSessionThenBeginSwitch(
  openSession: () => Promise<void>,
  beginSwitch: () => void,
): Promise<void> {
  await openSession();
  beginSwitch();
}

function isRecallIntent(intent: string, intentLabel: string): boolean {
  return intent === "recall" || intentLabel.includes("回顾");
}

function mediaFileName(displayRef: string): string {
  const trimmed = displayRef.startsWith("local:") ? displayRef.slice("local:".length) : displayRef;
  const parts = trimmed.replace(/\\/g, "/").split("/");
  return parts[parts.length - 1] || trimmed;
}

function recentTurnIndices(blocks: ChatBlock[], k: number): number[] {
  const turns = new Set<number>();
  for (const block of blocks) {
    if (
      block.kind === "user" ||
      block.kind === "assistant" ||
      block.kind === "assistant-streaming"
    ) {
      turns.add(block.turnIndex);
    }
  }
  return [...turns].sort((a, b) => b - a).slice(0, k);
}

function isRecentTurn(turnIndex: number, currentTurnIndex: number): boolean {
  return turnIndex >= currentTurnIndex - (FOCUS_TURNS - 1);
}

function computeInitialPerspective(
  _activeShell: string | undefined,
  projectRoot: string | undefined,
): Perspective {
  // Phase 34: workbench always uses project layout (sidebar on).
  if (projectRoot) return "project";
  return "project";
}

export function mountUnifiedShell(
  root: HTMLElement,
  client: AgentWsClient,
  shellId: string = "grow",
): () => void {
  // ---- perspective state (Phase 34: default = workbench / project layout) ----
  let perspective: Perspective = "project";
  let perspectiveLocked = true;

  function setPerspective(p: Perspective, reason: string = "manual"): void {
    if (perspectiveLocked && reason === "auto" && p !== "project") return;
    // Workbench keeps project layout; night is not an entry mode.
    if (p === "default") p = "project";
    if (perspective !== p) {
      renderedPrints = [];
      renderedSegmentPrints = [];
    }
    perspective = p;
    shellEl.setAttribute("data-perspective", p === "night" ? "project" : p);
    // The legacy sidebar remains in the DOM for its event wiring, never as navigation.
    sidebarEl.hidden = true;
    updatePlaceholder();
    renderChat();
    renderConfirmGlass();
    syncWorkingVisual();
    refreshServices();
    updateWorkbenchEmpty();
  }

  // ---- ui state ----
  let autoRecognition = { sessionId: "", enabled: false, pending: "", action: "" as "" | "enable" | "revoke", error: false };
  const globalRoleModels: { preferences: LlmRoleModelPreferences; models: LlmModelListItem[]; pendingRole: "main" | "subagent" | ""; error: string } = {
    preferences: { main: "", subagent: "" }, models: [], pendingRole: "", error: "",
  };
  let approvalPreferences: { mode: ApprovalPreferenceMode; savedMode: ApprovalPreferenceMode; known: boolean; pending: boolean; available: boolean; error: boolean; message: string } = {
    mode: "manual", savedMode: "manual", known: false, pending: false, available: false, error: false, message: "",
  };
  let currentWorkOverviewResult: WorkRecordsOverviewResultEvent | null = null;
  let currentSessionWorkOverviewResult: WorkRecordsOverviewResultEvent | null = null;
  let currentWorkOverviewOpen = false;
  const selectedMultiProjectOverviewIds = new Set<string>();
  const currentWorkOverviewExpandedRequirementItemIds = new Set<string>();
  let currentWorkOverviewLoadingMore = false;
  let currentWorkOverviewUnavailable = false;
  let currentWorkOverviewRefreshTimer: number | null = null;
  let currentWorkVisibilityRecheckTimer: number | null = null;
  let currentWorkOverviewRefreshQueued = false;
  const currentWorkOverviewExecutionStates = new Map<string, string>();
  let currentWorkOverviewActiveRequest: string | null = null;
  let currentWorkOverviewDetailLoadingCursor: string | null = null;
  let currentWorkOverviewDetailPagingSupported = true;
  type OverviewDetailTarget = { kind: "items" | "requirements"; groupKind: "project" | "independent" | "linked_source_snapshot"; groupId: string; itemId?: string };
  const currentWorkOverviewRequests = new Map<string, { sessionId: string; cursor?: string; detail?: OverviewDetailTarget; history?: { projectId: string; itemId: string; requirementId: string }; mergedHistory?: { cursor?: string }; detailPaging: boolean; projectIds?: string[] }>();
  let mergedHistory: MergedHistoryState = { rows: [], page: null, sources: [] };
  const clearMergedHistoryState = (): void => { mergedHistory = clearMergedHistory(mergedHistory); };
  const topbarState: TopbarState = {
    proposals: [] as ProposalItem[],
    intentLabel: "",
    checkerLabel: "",
    memoryLabel: "",
    memoryCount: 0,
    pendingMemoryCount: 0,
    projectLabel: "",
    contextLabel: "",
    sessionCount: 0,
    headerCtas: [],
  };

  let sessionsDropdown: Array<{
    session_id: string;
    title: string;
    preview?: string;
    updated_at: string;
    message_count?: number;
    project_id?: string;
    execution_lane?: "stable" | "sandbox";
  }> = [];
  let sessionsOpen = false;
  let sessionsTab: "chat" | "project" = "chat";

  function syncTopbarFromProject(): void {
    const restartCta = { action: "restart_today", label: "今日重来" };
    if (projectState.projectId) {
      topbarState.projectLabel = projectState.projectId;
      topbarState.contextLabel = "";
      topbarState.headerCtas = [restartCta];
    } else {
      topbarState.projectLabel = "";
      topbarState.contextLabel = "";
      topbarState.headerCtas = [restartCta];
    }
  }

  function clearCurrentWorkVisibilityRecheck(): void {
    if (currentWorkVisibilityRecheckTimer !== null) window.clearTimeout(currentWorkVisibilityRecheckTimer);
    currentWorkVisibilityRecheckTimer = null;
  }

  function scheduleCurrentWorkVisibilityRecheck(result: WorkRecordsOverviewResultEvent | null, sessionId: string, reset = false, force = false): void {
    if (reset) clearCurrentWorkVisibilityRecheck();
    if (currentWorkVisibilityRecheckTimer !== null || (!force && !shouldScheduleOverviewVisibilityRecheck(result))
      || (result !== null && result.session_id !== sessionId)) return;
    currentWorkVisibilityRecheckTimer = window.setTimeout(() => {
      currentWorkVisibilityRecheckTimer = null;
      if (chat.model.sessionId !== sessionId) return;
      if ([...currentWorkOverviewRequests.values()].some(request => request.sessionId === sessionId)) {
        currentWorkOverviewRefreshQueued = true;
        return;
      }
      queryStructuredCurrentWorkOverview(undefined, sessionId);
    }, currentWorkOverviewRecheckDelayMs(result)) as unknown as number;
  }
  function queryStructuredCurrentWorkOverview(
    cursor?: string, sessionId = chat.model.sessionId, detail?: OverviewDetailTarget,
    projectIds?: string[],
    history?: { projectId: string; itemId: string; requirementId: string; cursor?: string },
    mergedHistory?: { cursor?: string },
  ): void {
    if (!sessionId) return;
    if ([...currentWorkOverviewRequests.values()].some(request => request.sessionId === sessionId)) return;
    try {
      const detailPaging = currentWorkOverviewDetailPagingSupported;
      const requestId = client.queryWorkRecordsOverview({ pageSize: 6, detailPaging, ...(cursor ? { cursor } : {}), ...(projectIds ? { projectIds } : {}), ...(history ? { history } : {}), ...(mergedHistory ? { projectIds, mergedHistory } : {}) });
      currentWorkOverviewRequests.set(requestId, { sessionId, detailPaging, ...(cursor ? { cursor } : {}), ...(detail ? { detail } : {}), ...(history ? { history: { projectId: history.projectId, itemId: history.itemId, requirementId: history.requirementId } } : {}), ...(mergedHistory ? { mergedHistory: { ...(mergedHistory.cursor ? { cursor: mergedHistory.cursor } : {}) } } : {}), ...(projectIds ? { projectIds: [...projectIds] } : {}) });
      currentWorkOverviewActiveRequest = requestId;
      currentWorkOverviewLoadingMore = Boolean(cursor) && !detail;
      currentWorkOverviewDetailLoadingCursor = detail ? cursor ?? null : null;
      // A same-session recheck must retain the last server projection until its replacement arrives.
      if (!cursor && currentWorkOverviewResult?.session_id !== sessionId) {
        currentWorkOverviewResult = null;
        currentSessionWorkOverviewResult = null;
        currentWorkOverviewUnavailable = true;
      }
      refreshTopbar();
    } catch (error) {
      currentWorkOverviewLoadingMore = false;
      currentWorkOverviewDetailLoadingCursor = null;
      if (mergedHistory) clearMergedHistoryState();
      if (!cursor && currentWorkOverviewResult?.session_id !== sessionId) {
        currentWorkOverviewResult = null;
        currentSessionWorkOverviewResult = null;
        currentWorkOverviewUnavailable = true;
      }
      refreshTopbar();
      setStatus(`当前工作概况查询未发送：${error instanceof Error ? error.message : String(error)}`);
    }
  }

  function applyCurrentWorkControl(button: HTMLButtonElement): void {
    const itemId = button.dataset.itemId;
    const requirementId = button.dataset.requirementId;
    const action = button.dataset.action;
    const expectedStatus = button.dataset.expectedStatus;
    const priority = button.dataset.priority;
    const sourceProjectId = button.dataset.sourceProjectId;
    const sourceRevision = button.dataset.sourceRevision;
    const sessionId = chat.model.sessionId;
    if (!itemId || !sessionId || !action
        || currentWorkOverviewResult?.session_id !== sessionId) return;
    if (action !== "set_priority" && (!requirementId
        || (action !== "copy_requirement_to_new_item" && !expectedStatus)
        || !["set_dependencies", "queue", "wait_confirmation", "release_confirmation", "stop", "continue", "cancel_execution", "revise_requirement", "copy_requirement_to_new_item", "inspect_unknown_claim"].includes(action))) return;
    if (action === "set_priority" && (!priority || !Number.isInteger(Number(priority)))) return;
    if (sourceProjectId ? (!Number.isSafeInteger(Number(sourceRevision)) || Number(sourceRevision) < 0)
        : !Number.isSafeInteger(currentWorkOverviewResult?.work_record_revision)) {
      setStatus("当前记录版本不可用，请先刷新当前工作。");
      return;
    }
    button.disabled = true;
    try {
      const revisionScope = sourceProjectId
        ? { projectId: sourceProjectId, expectedSourceRevision: Number(sourceRevision) }
        : { expectedRevision: currentWorkOverviewResult!.work_record_revision! };
      if (action === "set_priority") client.controlWorkRecord({ itemId, action, priority: Number(priority), ...revisionScope });
      else if (action === "copy_requirement_to_new_item") client.controlWorkRecord({ itemId, requirementId, action, ...revisionScope });
      else if (action === "set_dependencies") {
        const container = button.closest<HTMLElement>(".unified-current-work-item");
        const dependsOn = Array.from(container?.querySelectorAll<HTMLInputElement>("input[data-work-dependency]:checked") ?? [])
          .filter(input => input.dataset.workDependencyFor === requirementId)
          .map(input => input.dataset.workDependency).filter((value): value is string => Boolean(value));
        const dependsOnRefs = collectWorkRecordDependencyRefs(container, requirementId);
        client.controlWorkRecord({ itemId, requirementId, action, expectedStatus, dependsOn, dependsOnRefs, ...revisionScope });
      } else if (action === "revise_requirement") {
        const container = button.closest<HTMLElement>(".unified-current-work-item");
        const editor = Array.from(container?.querySelectorAll<HTMLTextAreaElement>("textarea[data-work-record-new-text-for]") ?? [])
          .find(candidate => candidate.dataset.workRecordNewTextFor === requirementId);
        const newText = editor?.value.trim() ?? "";
        if (!newText) throw new Error("请先填写修订后的要求文本。");
        client.controlWorkRecord({ itemId, requirementId, action, expectedStatus, newText, ...revisionScope });
      } else if (action === "inspect_unknown_claim") {
        if (!sourceProjectId || expectedStatus !== "unknown") return;
        const container = button.closest<HTMLElement>(".unified-current-work-item");
        const editor = Array.from(container?.querySelectorAll<HTMLTextAreaElement>("textarea[data-claim-inspection-for]") ?? [])
          .find(candidate => candidate.dataset.claimInspectionFor === requirementId);
        const inspectionEvidence = editor?.value.trim() ?? "";
        if (!inspectionEvidence) throw new Error("请先记录实际核查对象与观察到的证据；不能只确认已阅读提示。");
        client.controlWorkRecord({ itemId, requirementId, action, expectedStatus, inspectionEvidence, ...revisionScope });
      } else if (action === "cancel_execution") {
        if (sourceProjectId) return;
        client.controlWorkRecord({ itemId, requirementId, action, expectedStatus, ...revisionScope });
      } else client.controlWorkRecord({ itemId, requirementId, action: action as "queue" | "wait_confirmation" | "release_confirmation" | "stop" | "continue", expectedStatus, ...revisionScope });
      setStatus("已提交事项控制；等待服务端核对状态。");
    } catch (error) {
      button.disabled = false;
      setStatus(`事项控制未发送：${error instanceof Error ? error.message : String(error)}`);
    }
  }

  function requestCurrentWorkOverviewDetail(button: HTMLButtonElement): void {
    const cursor = button.dataset.currentWorkDetailCursor;
    const kind = button.dataset.currentWorkDetailKind;
    const groupKind = button.dataset.currentWorkDetailGroupKind;
    const groupId = button.dataset.currentWorkDetailGroupId;
    const itemId = button.dataset.currentWorkDetailItemId;
    const sessionId = chat.model.sessionId;
    if (!cursor || !sessionId || (kind !== "items" && kind !== "requirements")
      || (groupKind !== "project" && groupKind !== "independent" && groupKind !== "linked_source_snapshot") || !groupId
      || (kind === "requirements" && !itemId) || (kind === "items" && itemId !== undefined)
      || currentWorkOverviewResult?.session_id !== sessionId
      || currentWorkOverviewDetailLoadingCursor !== null) return;
    queryStructuredCurrentWorkOverview(cursor, sessionId, {
      kind, groupKind, groupId, ...(itemId ? { itemId } : {}),
    });
  }
  function scheduleCurrentWorkOverviewRefresh(): void {
    const sessionId = chat.model.sessionId;
    if (!sessionId) return;
    if ([...currentWorkOverviewRequests.values()].some(request => request.sessionId === sessionId)) {
      currentWorkOverviewRefreshQueued = true;
      return;
    }
    if (currentWorkOverviewRefreshTimer !== null) return;
    currentWorkOverviewRefreshTimer = window.setTimeout(() => {
      currentWorkOverviewRefreshTimer = null;
      const currentSessionId = chat.model.sessionId;
      if (!currentSessionId) return;
      if ([...currentWorkOverviewRequests.values()].some(request => request.sessionId === currentSessionId)) {
        currentWorkOverviewRefreshQueued = true;
        return;
      }
      queryStructuredCurrentWorkOverview(undefined, currentSessionId);
    }, 300) as unknown as number;
  }

  function flushQueuedCurrentWorkOverviewRefresh(): void {
    if (!currentWorkOverviewRefreshQueued) return;
    currentWorkOverviewRefreshQueued = false;
    scheduleCurrentWorkOverviewRefresh();
  }
  function toggleStructuredCurrentWorkOverview(): void {
    if (projectState.mainFocus === "current-work") {
      currentWorkOverviewOpen = false;
      showNowChat();
      return;
    }
    currentWorkOverviewOpen = true;
    queryStructuredCurrentWorkOverview();
    setMainFocus("current-work");
    refreshTopbar();
  }

  function expandStructuredCurrentWorkOverview(): void {
    const cursor = currentWorkOverviewResult?.page.next_cursor;
    if (!cursor || currentWorkOverviewLoadingMore || !currentWorkOverviewResult) return;
    queryStructuredCurrentWorkOverview(cursor, currentWorkOverviewResult.session_id);
  }
  function requestCurrentWorkOverview(): void {
    const result = dispatchCurrentWorkOverviewRequest({
      busy: chat.isWorking() || Boolean(chat.model.confirmPending),
      allowed: isWorkbenchChatAllowed(),
      send: (text) => {
        const dispatch = client.sendMessage(text, undefined, { turnMode: currentTurnMode });
        chat.pushUserMessage(text, {
          requestId: dispatch.requestId,
          sessionId: dispatch.sessionId,
          receiptEligible: true,
        });
        topbarState.intentLabel = "";
        resetUserScrollPin();
        setStatus("正在查询当前工作概况…");
      },
    });
    if (result === "busy") setStatus("当前回合或确认尚未结束，暂不能查询概况。");
    else if (result === "unavailable") setStatus("当前对话不可发送，未发起概况查询。");
    else if (result === "failed") setStatus("概况查询未能发送；未用旧卡片替代。");
  }
  // Full-page workbench owns its scroll; keep a no-op for older resize hooks.
  function sizeCurrentWorkPopover(): void {}

  function currentWorkLiveSessionInput(): CurrentWorkLiveSessionInput {
    return {
      confirmPending: chat.model.confirmPending,
      confirmOverlay: chat.model.confirmOverlay,
      turnActive: chat.model.turnActive,
      executionState: chat.model.executionState,
      blocks: chat.model.blocks,
    };
  }

  function refreshTopbar(): void {
    syncTopbarFromProject();
    topbarState.memoryCount = memoryShelfState.memories.length;
    topbarState.pendingMemoryCount = memoryShelfState.pending.length || topbarState.proposals.length;
    topbarState.autoRecognition = { enabled: autoRecognition.enabled, pending: !!autoRecognition.pending, available: !!autoRecognition.sessionId && autoRecognition.sessionId === chat.model.sessionId, error: autoRecognition.error };
    const mappedProjectChoices = projectState.projects.filter(project => Boolean(project.sessionId)
      && project.id !== projectState.projectId && project.sessionId !== chat.model.sessionId);
    const mappedProjectChoiceIds = new Set(mappedProjectChoices.map(project => project.id));
    for (const selectedId of selectedMultiProjectOverviewIds) {
      if (!mappedProjectChoiceIds.has(selectedId)) selectedMultiProjectOverviewIds.delete(selectedId);
    }
    const multiProjectAvailable = mappedProjectChoices.length > 0;
    topbarState.currentWorkOverview = buildCurrentWorkOverviewPanel(currentWorkOverviewResult, {
      open: currentWorkOverviewOpen || projectState.mainFocus === "current-work",
      loadingMore: currentWorkOverviewLoadingMore,
      unavailable: currentWorkOverviewUnavailable || (multiProjectAvailable && !currentWorkOverviewResult),
      expandedRequirementItemIds: currentWorkOverviewExpandedRequirementItemIds,
      detailLoadingCursor: currentWorkOverviewDetailLoadingCursor,
      liveSession: currentWorkLiveSessionInput(),
    });
    let currentWorkExtras = "";
    if (currentWorkOverviewResult?.aggregation_scope === "selected_project_sessions"
      && currentWorkOverviewResult.schedule_preview) {
      currentWorkExtras += renderScheduleLaunchForm(currentWorkOverviewResult);
    }
    if (mergedHistory.page && mergedHistory.sources.length) {
      const rows = mergedHistory.rows.map((row: MergedHistoryRow) => `<li>${escapeHtml(row.project_id)} · ${escapeHtml(row.item_title ?? row.item_id)} · ${escapeHtml(row.requirement_text ?? "")} · ${escapeHtml(row.status)} · ${escapeHtml(row.summary)} · ${escapeHtml(row.verification_status)}</li>`).join("");
      const next = mergedHistory.page.has_more && mergedHistory.page.next_cursor ? `<button type="button" data-merged-history-next data-cursor="${escapeHtml(mergedHistory.page.next_cursor)}">加载下一页统一历史</button>` : "";
      currentWorkExtras += `<section data-merged-history class="unified-selected-project-overview"><h4>所选项目统一历史（仅授权项目）</h4><p>来源版本：${mergedHistory.sources.map(source => `${escapeHtml(source.project_id)} / ${escapeHtml(source.session_id)} @${source.revision}`).join("；")}</p><ul>${rows}</ul>${next}</section>`;
    }
    if (multiProjectAvailable) {
      const choices = mappedProjectChoices.map(project => {
        const checked = selectedMultiProjectOverviewIds.has(project.id) ? " checked" : "";
        return `<label><input type="checkbox" data-project-overview-id="${escapeHtml(project.id)}"${checked}> ${escapeHtml(project.id)}</label>`;
      }).join("");
      currentWorkExtras += `<section class="unified-selected-project-overview" data-selected-project-overview aria-label="其他项目概况"><header class="unified-selected-project-overview-head"><h3>其他项目概况（${mappedProjectChoices.length}）</h3></header><div class="unified-selected-project-overview-popover"><div class="unified-selected-project-overview-list">${choices}</div><button type="button" class="unified-btn unified-btn-ghost" data-run-selected-project-overview>查看所选项目</button><button type="button" class="unified-btn unified-btn-ghost" data-run-selected-project-history>查看所选项目统一历史</button><p>仅读取确认的项目记录；最多选择 6 个。</p></div></section>`;
    }
    renderTopbar(
      topbarEl,
      topbarState,
      openProposals,
      undefined, // single continuous conversation: no new-chat control
      undefined,
      undefined,
      undefined,
      handleHeaderNextStepAction,
      openMemoryShelf,
      requestCurrentWorkOverview,
      toggleStructuredCurrentWorkOverview,
      expandStructuredCurrentWorkOverview,
      selectSingleChatTool,
    );
    if (projectState.mainFocus === "settings") renderSettingsStage();
    if (projectState.mainFocus === "current-work") renderCurrentWorkStage(currentWorkExtras);
    else sizeCurrentWorkPopover();
    syncPendingConfirmComposerHint();
  }

  function renderCurrentWorkStage(extrasHtml?: string): void {
    const panel = topbarState.currentWorkOverview ?? buildCurrentWorkOverviewPanel(currentWorkOverviewResult, {
      open: true,
      loadingMore: currentWorkOverviewLoadingMore,
      unavailable: currentWorkOverviewUnavailable,
      expandedRequirementItemIds: currentWorkOverviewExpandedRequirementItemIds,
      detailLoadingCursor: currentWorkOverviewDetailLoadingCursor,
      liveSession: currentWorkLiveSessionInput(),
    });
    let extras = extrasHtml;
    if (extras === undefined) {
      extras = "";
      if (currentWorkOverviewResult?.aggregation_scope === "selected_project_sessions"
        && currentWorkOverviewResult.schedule_preview) {
        extras += renderScheduleLaunchForm(currentWorkOverviewResult);
      }
      const mappedProjectChoices = projectState.projects.filter(project => Boolean(project.sessionId)
        && project.id !== projectState.projectId && project.sessionId !== chat.model.sessionId);
      if (mappedProjectChoices.length) {
        const choices = mappedProjectChoices.map(project => {
          const checked = selectedMultiProjectOverviewIds.has(project.id) ? " checked" : "";
          return `<label><input type="checkbox" data-project-overview-id="${escapeHtml(project.id)}"${checked}> ${escapeHtml(project.id)}</label>`;
        }).join("");
        extras += `<section class="unified-selected-project-overview" data-selected-project-overview aria-label="其他项目概况"><header class="unified-selected-project-overview-head"><h3>其他项目概况（${mappedProjectChoices.length}）</h3></header><div class="unified-selected-project-overview-popover"><div class="unified-selected-project-overview-list">${choices}</div><button type="button" class="unified-btn unified-btn-ghost" data-run-selected-project-overview>查看所选项目</button><button type="button" class="unified-btn unified-btn-ghost" data-run-selected-project-history>查看所选项目统一历史</button><p>仅读取确认的项目记录；最多选择 6 个。</p></div></section>`;
      }
      if (mergedHistory.page && mergedHistory.sources.length) {
        const rows = mergedHistory.rows.map((row: MergedHistoryRow) => `<li>${escapeHtml(row.project_id)} · ${escapeHtml(row.item_title ?? row.item_id)} · ${escapeHtml(row.requirement_text ?? "")} · ${escapeHtml(row.status)} · ${escapeHtml(row.summary)} · ${escapeHtml(row.verification_status)}</li>`).join("");
        const next = mergedHistory.page.has_more && mergedHistory.page.next_cursor ? `<button type="button" data-merged-history-next data-cursor="${escapeHtml(mergedHistory.page.next_cursor)}">加载下一页统一历史</button>` : "";
        extras += `<section data-merged-history class="unified-selected-project-overview"><h4>所选项目统一历史（仅授权项目）</h4><p>来源版本：${mergedHistory.sources.map(source => `${escapeHtml(source.project_id)} / ${escapeHtml(source.session_id)} @${source.revision}`).join("；")}</p><ul>${rows}</ul>${next}</section>`;
      }
    }
    currentWorkEl.innerHTML = renderCurrentWorkWorkbenchPage(panel, extras);
    bindCurrentWorkPage(currentWorkEl);
  }

  function bindCurrentWorkPage(rootEl: HTMLElement): void {
    rootEl.querySelector<HTMLButtonElement>("[data-current-work-back-chat]")?.addEventListener("click", () => {
      currentWorkOverviewOpen = false;
      showNowChat();
    });
    rootEl.querySelector<HTMLButtonElement>("[data-current-work-ask]")?.addEventListener("click", () => requestCurrentWorkOverview());
    rootEl.querySelector<HTMLButtonElement>("[data-current-work-more]")?.addEventListener("click", () => expandStructuredCurrentWorkOverview());
    rootEl.querySelectorAll<HTMLButtonElement>(".unified-current-work-detail-more[data-current-work-detail-cursor]").forEach(button => {
      button.addEventListener("click", () => requestCurrentWorkOverviewDetail(button));
    });
    const scheduleForm = rootEl.querySelector<HTMLElement>("[data-schedule-launch]");
    const scheduleSelect = scheduleForm?.querySelector<HTMLSelectElement>("[data-schedule-candidate]");
    const scheduleSourceTask = scheduleForm?.querySelector<HTMLElement>("[data-schedule-source-task]");
    scheduleSelect?.addEventListener("change", () => {
      const selected = scheduledWorkerCandidates(currentWorkOverviewResult)[Number(scheduleSelect.value)];
      if (scheduleSourceTask) scheduleSourceTask.textContent = selected ? `来源要求：${selected.task}` : "候选已变化，请重新读取当前工作概况。";
    });
    scheduleForm?.querySelector<HTMLButtonElement>("[data-schedule-launch-send]")?.addEventListener("click", () => {
      if (chat.isWorking() || chat.model.confirmPending || !isWorkbenchChatAllowed()
        || currentWorkOverviewResult?.session_id !== chat.model.sessionId) {
        setStatus("当前回合繁忙或会话已变化；未发送启动请求。"); return;
      }
      const task = scheduleForm.querySelector<HTMLTextAreaElement>("[data-schedule-task]");
      const read = scheduleForm.querySelector<HTMLTextAreaElement>("[data-schedule-read]");
      const write = scheduleForm.querySelector<HTMLTextAreaElement>("[data-schedule-write]");
      try {
        const payload = buildScheduleDispatchWorkers(
          currentWorkOverviewResult, Number(scheduleSelect?.value),
          task?.value ?? "", read?.value ?? "", write?.value ?? "",
        );
        client.dispatchScheduleWorkers(payload);
        setStatus("调度启动请求已提交；等待服务端核对来源、并发上限及逐项确认。");
      } catch (error) {
        setStatus(`启动请求未发送：${error instanceof Error ? error.message : String(error)}`);
      }
    });
    rootEl.querySelector<HTMLButtonElement>("[data-run-selected-project-history]")?.addEventListener("click", () => {
      const selected = [...selectedMultiProjectOverviewIds];
      if (!selected.length) { setStatus("请至少选择一个项目来源。"); return; }
      clearMergedHistoryState();
      queryStructuredCurrentWorkOverview(undefined, chat.model.sessionId, undefined, selected, undefined, {});
      setStatus("正在逐页核实所选项目历史及来源版本…");
    });
    rootEl.querySelector<HTMLButtonElement>("button[data-merged-history-next]")?.addEventListener("click", button => {
      const cursor = (button.currentTarget as HTMLButtonElement).dataset.cursor;
      const selected = mergedHistory.sources.map(source => source.project_id);
      if (!cursor || !selected.length || selected.some(id => !selectedMultiProjectOverviewIds.has(id))) {
        clearMergedHistoryState();
        setStatus("所选项目范围发生变化；统一历史游标已清除，请重新加载。"); refreshTopbar(); return;
      }
      queryStructuredCurrentWorkOverview(undefined, chat.model.sessionId, undefined, selected, undefined, { cursor });
      setStatus("正在核实统一历史下一页及来源版本…");
    });
    rootEl.querySelectorAll<HTMLButtonElement>("button[data-requirement-history]").forEach(button => {
      button.addEventListener("click", () => {
        const projectId = button.dataset.projectId, itemId = button.dataset.itemId, requirementId = button.dataset.requirementId;
        const sessionId = chat.model.sessionId;
        if (!projectId || !itemId || !requirementId || !sessionId || !currentWorkOverviewResult?.selected_project_ids?.includes(projectId)) return;
        queryStructuredCurrentWorkOverview(undefined, sessionId, undefined, currentWorkOverviewResult.selected_project_ids,
          { projectId, itemId, requirementId, ...(button.dataset.cursor ? { cursor: button.dataset.cursor } : {}) });
        button.disabled = true;
        setStatus("正在重新确认项目来源，并加载此单个要求的历史页…");
      });
    });
    rootEl.querySelectorAll<HTMLButtonElement>("button[data-work-record-control]").forEach(button => {
      button.addEventListener("click", () => applyCurrentWorkControl(button));
    });
    rootEl.querySelectorAll<HTMLButtonElement>("button[data-open-source-session]").forEach(button => {
      button.addEventListener("click", () => {
        const sourceSessionId = button.dataset.openSourceSession;
        const projectId = button.dataset.openSourceProject || "";
        if (!sourceSessionId || chat.isWorking()) {
          setStatus("当前回合进行中或来源会话不可用，未切换会话。");
          return;
        }
        void client.openSession(sourceSessionId).then(() => {
          if (projectId) setStatus(`已切换到项目 ${projectId} 的来源会话。`);
        }).catch(error => setStatus(`打开来源会话失败：${error instanceof Error ? error.message : String(error)}`));
      });
    });
    const selectedProjectPanel = rootEl.querySelector<HTMLElement>("[data-selected-project-overview]");
    if (selectedProjectPanel) {
      selectedProjectPanel.querySelectorAll<HTMLInputElement>("input[data-project-overview-id]").forEach(input => {
        input.addEventListener("change", () => {
          const id = input.dataset.projectOverviewId;
          if (!id) return;
          if (input.checked) {
            if (!selectedMultiProjectOverviewIds.has(id) && selectedMultiProjectOverviewIds.size >= 6) {
              input.checked = false;
              setStatus("一次最多选择 6 个项目来源。");
              return;
            }
            selectedMultiProjectOverviewIds.add(id);
          } else selectedMultiProjectOverviewIds.delete(id);
          if (mergedHistory.sources.length && mergedHistory.sources.some(source => !selectedMultiProjectOverviewIds.has(source.project_id))) {
            clearMergedHistoryState();
            refreshTopbar();
          }
        });
      });
      selectedProjectPanel.querySelector<HTMLButtonElement>("[data-run-selected-project-overview]")?.addEventListener("click", () => {
        const selected = [...selectedMultiProjectOverviewIds];
        if (!selected.length) { setStatus("请至少选择一个项目来源。"); return; }
        queryStructuredCurrentWorkOverview(undefined, chat.model.sessionId, undefined, selected);
        setStatus("已提交多项目来源请求；确认其 Session 来源后才读取记录。");
      });
    }
    function setCurrentWorkMode(mode: "home" | "focus" | "schedule"): void {
      rootEl.querySelector<HTMLElement>("[data-current-work-page]")?.setAttribute("data-cw-mode", mode);
      rootEl.querySelectorAll<HTMLElement>("[data-cw-surface]").forEach(surface => {
        const open = surface.dataset.cwSurface === mode;
        surface.hidden = !open;
        surface.classList.toggle("is-open", open);
      });
      const home = rootEl.querySelector<HTMLElement>("[data-cw-home]");
      if (home) home.hidden = mode !== "home";
    }
    rootEl.querySelectorAll<HTMLButtonElement>("[data-cw-open]").forEach(button => {
      button.addEventListener("click", () => {
        const mode = button.dataset.cwOpen;
        if (mode === "focus" || mode === "schedule") setCurrentWorkMode(mode);
      });
    });
    rootEl.querySelectorAll<HTMLButtonElement>("[data-cw-close]").forEach(button => {
      button.addEventListener("click", () => setCurrentWorkMode("home"));
    });
    rootEl.querySelectorAll<HTMLButtonElement>("[data-cw-select-item]").forEach(button => {
      button.addEventListener("click", () => {
        const itemId = button.dataset.cwSelectItem;
        if (!itemId) return;
        if (currentWorkOverviewExpandedRequirementItemIds.has(itemId)) {
          currentWorkOverviewExpandedRequirementItemIds.delete(itemId);
        } else {
          currentWorkOverviewExpandedRequirementItemIds.clear();
          currentWorkOverviewExpandedRequirementItemIds.add(itemId);
        }
        refreshTopbar();
        const page = document.querySelector<HTMLElement>("#unified-current-work [data-current-work-page]");
        if (page) {
          page.setAttribute("data-cw-mode", "focus");
          page.querySelectorAll<HTMLElement>("[data-cw-surface]").forEach(surface => {
            const open = surface.dataset.cwSurface === "focus";
            surface.hidden = !open;
            surface.classList.toggle("is-open", open);
          });
          const home = page.querySelector<HTMLElement>("[data-cw-home]");
          if (home) home.hidden = true;
        }
      });
    });
  }

function refreshGlobalRoleModels(): void {
    if (globalRoleModels.pendingRole) return;
    try {
      client.listRoleModels();
    } catch {
      globalRoleModels.error = "无法读取最新的全局模型选择。";
      refreshTopbar();
    }
  }

  let approvalPreferenceTimeout: number | null = null;
  function clearApprovalPreferenceTimeout(): void {
    if (approvalPreferenceTimeout !== null) window.clearTimeout(approvalPreferenceTimeout);
    approvalPreferenceTimeout = null;
  }

  function scheduleApprovalPreferenceTimeout(): void {
    clearApprovalPreferenceTimeout();
    approvalPreferenceTimeout = window.setTimeout(() => {
      approvalPreferenceTimeout = null;
      if (!approvalPreferences.pending) return;
      approvalPreferences = { ...approvalPreferences, known: false, pending: false, available: false, error: true, message: "审批设置响应超时，无法确认当前实际档位；切换已禁用。重新打开此分类可重试。" };
      refreshApprovalSettingsView();
    }, 15_000);
  }

  function refreshApprovalSettingsView(): void {
    settingsRenderedModelState = "";
    if (projectState.mainFocus === "settings" && (projectState.settingsSection || "appearance") === "models") renderSettingsStage();
  }

  function refreshApprovalPreferences(): void {
    if (approvalPreferences.pending) return;
    approvalPreferences = { ...approvalPreferences, pending: true, available: false, error: false, message: "" };
    refreshApprovalSettingsView();
    try {
      client.getApprovalPreferences();
      scheduleApprovalPreferenceTimeout();
    } catch {
      approvalPreferences = { ...approvalPreferences, known: false, pending: false, available: false, error: true, message: "读取审批设置失败，当前实际档位未知；切换已禁用。重新打开此分类可重试。" };
      refreshApprovalSettingsView();
    }
  }

  function setApprovalPreference(mode: ApprovalPreferenceMode): void {
    if (!approvalPreferences.available || approvalPreferences.pending) return;
    approvalPreferences = { ...approvalPreferences, pending: true, error: false, message: "" };
    refreshApprovalSettingsView();
    try {
      client.setApprovalMode(mode);
      scheduleApprovalPreferenceTimeout();
    } catch {
      approvalPreferences = { ...approvalPreferences, known: false, pending: false, available: false, error: true, message: "请求未能发送；当前实际档位未知，切换已禁用。重新打开此分类可重试。" };
      refreshApprovalSettingsView();
    }
  }

  function setGlobalRoleModel(role: "main" | "subagent", model: string): void {
    if (globalRoleModels.pendingRole) return;
    globalRoleModels.pendingRole = role;
    globalRoleModels.error = "";
    refreshTopbar();
    try {
      client.setRoleModel(role, model);
    } catch {
      globalRoleModels.pendingRole = "";
      globalRoleModels.error = "请求未能发送；全局模型选择未更改。";
      refreshTopbar();
    }
  }

  function setAutoRecognition(action: "enable" | "revoke"): void {
    if (!autoRecognition.sessionId || autoRecognition.sessionId !== chat.model.sessionId || autoRecognition.pending || (action === "enable") === autoRecognition.enabled) return;
    try {
      const requestId = client.setWorkRecordAutoRecognition(autoRecognition.sessionId, action);
      autoRecognition = { ...autoRecognition, pending: requestId, action, error: false };
    } catch { autoRecognition = { ...autoRecognition, error: true }; }
    refreshTopbar();
  }

  const proposalsState: ProposalsState = {
    proposals: [] as ProposalItem[],
    proposalIndex: 0,
    expandOpen: false,
  };

  const memoryShelfState: MemoryShelfState = {
    memories: [] as MemoryItem[],
    pending: [] as ProposalItem[],
    expandOpen: false,
  };

  const projectState: ProjectPanelState = {
    projectId: "",
    projectSummary: "",
    planStatus: "",
    projectEntry: "",
    projectTemplate: "",
    mapMarkdown: "",
    projects: [],
    externalImportPath: "",
    externalImportProjectId: "",
    externalImportScan: null,
    switchOverlay: null,
    switchInProgress: false,
    pendingPickerId: "",
    overlayPanel: null,
    railTab: "now",
    switchConfirmTarget: null,
    projectSearchQuery: "",
    executionStage: null,
    workflowStage: null,
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
    currentDocMtime: 0,
    currentDocHash: "",
    newDocName: "",
    newDocFolder: "",
    docSearchQuery: "",
    renamingDocPath: "",
    renameDraft: "",
    deleteConfirmPath: "",
    docView: "preview",
    docEditDraft: "",
    docDirty: false,
    docSaving: false,
    docConflictPrompt: false,
    docsChatSplit: true,
    docPlaceholderCount: 0,
    recentlyChangedDocs: new Set<string>(),
    pendingDocUndo: null,
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
    terminalsEndedCollapsed: true,
    terminalsEndedShowAll: false,
    terminalDetails: null,
    terminalOutput: "",
    terminalOutputCursor: 0,
    terminalOutputLoading: false,
    terminalOutputError: "",
    terminalOutputCursorReset: false,
    terminalOutputTruncated: false,
    turnEvidence: [],
    turnEvidenceRunId: "",
    turnEvidenceTotalCount: 0,
    turnEvidenceOmittedCount: 0,
    turnEvidenceExpanded: false,
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
    scopeNeedsReconfirm: false,
    flowPreviewStage: null,
    milestoneAccepted: false,
    milestoneAcceptedAt: null,
  };

  let statusText = "连接中…";
  const projectWorkerProgressStatus = new ProjectWorkerProgressStatus();
  let projectWorkerProgressTimer: number | null = null;
  let projectWorkerProgressVisible = false;
  let cancelledStatusTimer: number | null = null;
  let composerWhisperTimer: number | null = null;
  let destroyed = false;
  let renderThrottleTimer: number | null = null;
  let processToggleAnchor: { turnKey: string; top: number; focus: boolean } | null = null;
  let terminalPollTimerId: number | null = null;
  let terminalRequestTimeoutId: number | null = null;
  let terminalRequestSerial = 0;
  let terminalRequestId: string | null = null;
  let terminalOutputRequestTimeoutId: number | null = null;
  let terminalOutputRequestSerial = 0;
  let terminalOutputRequestId: string | null = null;
  let terminalOutputRequestSessionId: string | null = null;
  let renderedPrints: string[] = [];
  let renderedSegmentPrints: string[] = [];
  let renderedPresenceHtml = "";
  /** UI-5972 M4: full history load defers markdown; incremental paths render eagerly. */
  let markdownRenderEager = true;
  /** turnKey → user expanded the folded early tool list (UX-023). */
  const toolsListExpanded = new Set<string>();
  // night perspective state
  let recallHighlightTurns = new Set<number>();
  let focusObserver: IntersectionObserver | null = null;

  const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");


  // ---- chat session ----
  const workCardPresentations = new WorkCardPresentationRegistry("");
  const workCardResultExcerpts = new WorkCardResultExcerptView();
  const pendingHistoryCardReads = new Map<string, string>();
  let historyCardReadSessionId = "";
  const chat = createChatSession(
    { showProcess: true, confirmInBlocks: true },
    {
      onChange: () => {
        renderChat();
        renderWorkActivity();
        renderConfirmGlass();
        syncPromptComposerUi();
        syncWorkingVisual();
      },
      onHistoryLoaded: (history) => {
        hydrateHistoryWorkCards(history.blocks);
        renderedPrints = [];
        renderedSegmentPrints = [];
        resetLazyMarkdownObserver();
        syncWorkingVisual();
        if (!chat.model.confirmPending) {
          setStatus("就绪");
        }
        scrollChatToBottom();
      },
      onSessionBanner: (sessionId) => {
        if (autoRecognition.sessionId !== sessionId) autoRecognition = { sessionId, enabled: false, pending: "", action: "", error: false };
        workCardResultExcerpts.setSession(sessionId);
        workCardPresentations.setSession(sessionId);
        if (shouldClearPendingHistoryCardReads(historyCardReadSessionId, sessionId)) pendingHistoryCardReads.clear();
        historyCardReadSessionId = sessionId;
        currentWorkOverviewRequests.clear();
        clearMergedHistoryState();
        if (currentWorkOverviewRefreshTimer !== null) window.clearTimeout(currentWorkOverviewRefreshTimer);
        currentWorkOverviewRefreshTimer = null;
        clearCurrentWorkVisibilityRecheck();
        currentWorkOverviewRefreshQueued = false;
        currentWorkOverviewExecutionStates.clear();
        currentWorkOverviewActiveRequest = null;
        currentWorkOverviewDetailPagingSupported = true;
        currentWorkOverviewResult = null;
        currentSessionWorkOverviewResult = null;
        currentWorkOverviewOpen = false;
        currentWorkOverviewExpandedRequirementItemIds.clear();
        currentWorkOverviewLoadingMore = false;
        currentWorkOverviewDetailLoadingCursor = null;
        currentWorkOverviewUnavailable = Boolean(sessionId);
        if (sessionId) queryStructuredCurrentWorkOverview(undefined, sessionId);
        refreshTopbar();
      },
      onTurnStart: (event) => {
        resetProcessJumpCursor();
        syncWorkingVisual();
        resetTurnCacheStats();
        topbarState.intentLabel = event.intent_label;
        topbarState.checkerLabel = "";
        if (perspective === "night" && isRecallIntent(event.intent, event.intent_label)) {
          recallHighlightTurns = new Set(recentTurnIndices(chat.model.blocks, RECALL_TURNS));
          renderChat();
          requestAnimationFrame(() => scrollToRecallTurns());
        }
        refreshTopbar();
        setStatus(event.intent_label);
        syncToolElapsedTimer();
        requestAnimationFrame(() => scrollToBottomIfNear());
      },
      onLlmPending: () => {
        if (!chat.model.cancelRequested) {
          setStatus(chatSessionAwaitingUserConfirm(chat.model) ? "等待确认…" : "处理中…");
        }
        syncToolElapsedTimer();
        requestAnimationFrame(() => scrollToBottomIfNear());
      },
      onCheckerVerdict: (event) => {
        topbarState.checkerLabel = checkerVerdictStatusText(event.verdict);
        refreshTopbar();
        setStatus(topbarState.checkerLabel);
      },
      onConfirmRequest: () => {
        setComposerEnabled(false);
        setStatus("等待确认…");
        requestAnimationFrame(() => {
          requestAnimationFrame(() => scrollPendingConfirmIntoView());
        });
      },
      onConfirmDone: (choice) => {
        setComposerEnabled(choice !== "cancelled");
        if (choice === "cancelled") {
          setStatus("正在停止…");
          return;
        }
        if (choice === "n" || choice === "timeout" || choice === "stale") {
          setStatus(chat.isWorking() ? "处理中…" : "就绪");
        } else {
          setStatus("执行中…");
        }
        syncPendingConfirmComposerHint();
      },
      onToolStart: () => {
        setStatus("处理中…");
      },
      onToolEnd: () => {
        if (!chat.model.confirmPending) {
          setStatus(chat.isWorking() ? "处理中…" : "就绪");
        }
      },
      onAssistantDone: () => {
        recallHighlightTurns = new Set();
        if (chat.model.cancelRequested) return;
        if (!chat.model.confirmPending) {
          setStatus("就绪");
        }
      },
      onTurnEnd: (_ok, finishReason) => {
        syncWorkingVisual();
        setComposerEnabled(!chat.model.confirmPending);
        if (!chat.model.confirmPending) {
          setTurnEndStatus(finishReason);
        }
        if (perspective === "project") {
          refreshServices();
        }
        // UX-017: notify when user switched away
        if (document.visibilityState !== "visible" && ("Notification" in window)) {
          if (Notification.permission === "granted") {
            new Notification("my-agent", { body: "已回复", silent: true });
          } else if (Notification.permission === "default") {
            Notification.requestPermission().then((perm) => {
              if (perm === "granted") {
                new Notification("my-agent", { body: "已回复", silent: true });
              }
            });
          }
        }
      },
      onError: () => {
        setComposerEnabled(true);
        setStatus("错误");
      },
    },
  );

  bindLazyMarkdownLookup((turnIndex) => {
    const block = chat.model.blocks.find(
      (b) =>
        (b.kind === "assistant" || b.kind === "assistant-streaming")
        && b.turnIndex === turnIndex,
    );
    if (!block || block.kind !== "assistant") return null;
    return block.text;
  });

  // ---- DOM layout ----
  root.innerHTML = `
    <div class="unified-shell" data-perspective="project" data-main-focus="chat" data-rail="now">
      <aside class="unified-sidebar" id="unified-sidebar" hidden inert aria-hidden="true">
        <div class="sidebar-resize-handle" id="sidebar-resize-handle"></div>
        <nav class="sidebar-icon-bar" id="sidebar-icon-bar" aria-label="主导航">
          <button type="button" class="sidebar-icon-btn is-active" data-panel="now" title="当下"><svg class="sidebar-icon-svg" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/></svg><span class="sidebar-icon-label">当下</span></button>
          <button type="button" class="sidebar-icon-btn" data-panel="docs" title="文档"><svg class="sidebar-icon-svg" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6M9 16h6"/></svg><span class="sidebar-icon-label">文档</span></button>
          <button type="button" class="sidebar-icon-btn" data-panel="projects" title="我的项目" id="icon-btn-projects"><svg class="sidebar-icon-svg" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"/></svg><span class="sidebar-icon-label">项目</span>
            <span class="sidebar-icon-badge" id="project-count-badge">0</span>
          </button>
          <span class="sidebar-degrade-dot hidden" id="sidebar-degrade-dot" data-action="toggle-degrade-info" title="项目管理器状态"></span>
          <button type="button" class="sidebar-icon-btn" data-panel="shell-dev" title="壳开发预览"><svg class="sidebar-icon-svg" viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="5" width="16" height="12" rx="2"/><path d="M8 19h8M12 17v2"/></svg><span class="sidebar-icon-label">壳预览</span></button>
          <button type="button" class="sidebar-icon-btn" data-panel="music" title="听歌"><svg class="sidebar-icon-svg" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/><path d="m9 8 12-2"/></svg><span class="sidebar-icon-label">听歌</span></button>
          <span class="sidebar-icon-spacer" aria-hidden="true"></span>
          <button type="button" class="sidebar-icon-btn sidebar-icon-settings" data-panel="settings" title="设置"><svg class="sidebar-icon-svg" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M5.5 18.5l1.7-1.7M16.8 7.2l1.7-1.7"/></svg><span class="sidebar-icon-label">设置</span></button>
        </nav>
        <div class="unified-sidebar-panel">
        <div class="unified-sidebar-header">
          <div class="unified-sidebar-title" id="project-sidebar-title">项目</div>
          <div class="unified-sidebar-meta" id="project-sidebar-meta">未绑定项目</div>
        </div>
        <section class="sidebar-sessions" id="sidebar-sessions" aria-label="会话">
          <div class="sidebar-sessions-top">
            <button type="button" class="sidebar-new-chat" id="sidebar-new-chat"><span class="sidebar-new-chat-plus" aria-hidden="true">＋</span>新会话<kbd>Ctrl N</kbd></button>
            <label class="sidebar-session-search"><span class="sidebar-session-search-ico" aria-hidden="true"></span><input type="search" id="sidebar-session-search" placeholder="搜索会话…" autocomplete="off" spellcheck="false" /><kbd>Ctrl K</kbd></label>
            <div class="sidebar-session-filters" id="sidebar-session-filters" role="group" aria-label="会话筛选"></div>
          </div>
          <div class="sidebar-session-list" id="sidebar-session-list"></div>
        </section>
        <div class="sidebar-task-flow" id="sidebar-task-flow"></div>
        <div class="sidebar-footer" id="sidebar-footer">
          <div class="sidebar-change-banner hidden" id="sidebar-change-banner"></div>
          <div class="sidebar-project-row" id="sidebar-project-row"></div>
          <div class="sidebar-runtime-badges">
            <div id="sidebar-terminals"></div>
            <div class="sidebar-services" id="sidebar-services"></div>
          </div>
        </div>
        </div>
        <div class="sidebar-overlay hidden" id="sidebar-overlay">
          <div class="sidebar-overlay-header">
            <button type="button" class="sidebar-overlay-back" id="overlay-back-btn">← 返回</button>
            <span class="sidebar-overlay-title" id="overlay-title"></span>
          </div>
          <div class="sidebar-overlay-body" id="overlay-body"></div>
        </div>
        <!-- compat: hidden elements for old event wiring -->
        <div id="project-picker-list" class="hidden"></div>
        <div id="project-picker-refresh" class="hidden"></div>
        <div class="hidden" id="project-switch-card"><div id="project-switch-title"></div><p id="project-switch-message"></p><div><button id="project-switch-confirm"></button><button id="project-switch-cancel"></button></div></div>
        <div class="hidden" id="project-plan-card"><div id="project-plan-title"></div><pre id="project-plan-preview"></pre><div><button id="project-plan-confirm"></button><button id="project-plan-edit"></button></div></div>
        <div class="hidden" id="project-sidebar-tabs"></div>
        <div class="hidden" id="project-panel-tasks"></div>
        <div class="hidden" id="project-panel-map"></div>
      </aside>
      <div class="unified-main">
        <header class="unified-topbar" id="unified-topbar"></header>
      <aside class="work-activity-rail" id="work-activity-rail" aria-label="工作动态">
        <button type="button" class="work-activity-toggle" id="work-activity-toggle" aria-controls="work-activity-history" aria-haspopup="dialog">
          <span class="work-activity-title">工作动态</span>
          <span class="work-activity-state" id="work-activity-state">安静</span>
        </button>
        <div class="work-activity-list" id="work-activity-list" aria-live="polite"></div>
      </aside>
      <button type="button" class="work-activity-compact-toggle" id="work-activity-compact-toggle" hidden>
        工作动态 <span id="work-activity-compact-count"></span>
      </button>
      <div class="work-activity-history hidden" id="work-activity-history" role="dialog" aria-modal="true" aria-labelledby="work-activity-history-title"></div>
        <div class="thread-archive-banner hidden" id="thread-archive-banner">
          <span class="thread-archive-banner-text">归档线 · 只读回看（不会改回活线）</span>
          <button type="button" class="unified-btn" id="thread-return-active">回到活线</button>
        </div>
        <div class="sandbox-write-scope-banner hidden" id="sandbox-write-scope-banner" role="status">
          <span class="sandbox-write-scope-banner-text" id="sandbox-write-scope-banner-text"></span>
          <button type="button" class="unified-btn" id="sandbox-back-to-chat">回到普通对话</button>
        </div>
        <section class="unified-context-region hidden" id="unified-goal-card" aria-label="项目上下文"></section>
        <div id="unified-goal-budget" class="unified-goal-budget hidden"></div>
        <div class="workbench-empty" id="workbench-empty" hidden>
          <div class="workbench-empty-card">
            <h2 class="workbench-empty-title">选择或新建项目</h2>
            <p class="workbench-empty-copy">打开应用即工作台。先选一个项目，再开始对话与改代码。</p>
            <div class="workbench-empty-actions">
              <button type="button" class="unified-btn unified-btn-accent" id="empty-new-project">新建项目</button>
              <button type="button" class="unified-btn" id="empty-pick-project">我的项目</button>
            </div>
            <button type="button" class="workbench-empty-free-chat" id="empty-free-chat">先聊聊</button>
          </div>
        </div>
        <div class="unified-stage" id="unified-stage">
          <div class="unified-switch-mask hidden" id="unified-switch-mask" aria-live="polite">
            <div class="unified-switch-mask-card">正在加载项目会话…</div>
          </div>
          <main class="unified-chat" id="unified-chat"></main>
          <section class="unified-tool-region" id="unified-tool-region" aria-label="工具" hidden>
            <header class="unified-tool-heading"><span id="unified-tool-title">工具</span><button type="button" class="unified-btn" id="unified-tool-close">关闭</button></header>
          <section class="unified-document hidden" id="unified-document" aria-label="文档阅读"></section>
          <section class="unified-projects hidden" id="unified-projects" aria-label="项目"></section>
          <section class="unified-settings hidden" id="unified-settings" aria-labelledby="settings-page-heading" hidden></section>
          <section class="unified-music hidden" id="unified-music" aria-labelledby="music-page-heading" hidden></section>
          <section class="unified-shell-dev hidden" id="unified-shell-dev" aria-labelledby="shell-dev-page-heading" hidden></section>
          <section class="unified-current-work hidden" id="unified-current-work" aria-labelledby="current-work-page-heading" hidden></section>
          <section id="unified-runtime-tool" class="unified-runtime-tool" hidden></section>
          </section>
        </div>
        <div class="unified-composer-dock" id="unified-composer-dock">
          <section class="unified-expand hidden" id="unified-expand"></section>
          <div class="unified-composer-meta" id="unified-composer-meta">
            <div class="unified-status" id="unified-status"></div>
            <div class="unified-token-bar hidden" id="unified-token-bar"></div>
          </div>
          <footer class="unified-composer" id="unified-composer">
            <div class="unified-mode-toggle" id="unified-mode-toggle" role="group" aria-label="对话模式" data-mode="agent" title="切换 动手(Agent) / 只聊(Ask) 模式">
              <button type="button" class="unified-mode-seg" data-mode-value="ask" aria-pressed="false">只聊</button>
              <button type="button" class="unified-mode-seg" data-mode-value="agent" aria-pressed="true">动手</button>
            </div>
            <button type="button" class="unified-btn" id="unified-stop" hidden>停止</button>
            <textarea class="unified-input" id="unified-input" rows="1" aria-label="输入消息" placeholder="和小魔王说点什么…"></textarea>
            <button type="button" class="unified-btn unified-btn-accent" id="unified-send" aria-label="发送消息" title="发送消息"><span aria-hidden="true">↑</span></button>
          </footer>
        </div>
      </div>
      <div class="unified-confirm-glass hidden" id="unified-confirm-glass" role="dialog" aria-modal="true"></div>
      <div class="unified-confirm-glass hidden" id="workbench-dialog" role="dialog" aria-modal="true"></div>
    </div>
  `;


  let workActivityHistoryOpen = false;
  let workActivityOpener: HTMLElement | null = null;

  function renderWorkActivity(): void {
    const rail = root.querySelector<HTMLElement>("#work-activity-rail");
    const list = root.querySelector<HTMLElement>("#work-activity-list");
    const toggle = root.querySelector<HTMLButtonElement>("#work-activity-toggle");
    const state = root.querySelector<HTMLElement>("#work-activity-state");
    if (!rail || !list || !toggle || !state) return;
    const items = projectRecentWorkActivity(chat.model.blocks);
    const main = rail.closest<HTMLElement>(".unified-main");
    const chatViewport = root.querySelector<HTMLElement>("#unified-chat");
    const chatContentLeft = chatViewport
      ? [...chatViewport.children].reduce((left, child) => {
        const rect = child.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0 ? Math.min(left, rect.left) : left;
      }, Number.POSITIVE_INFINITY)
      : Number.POSITIVE_INFINITY;
    const composerContent = root.querySelector<HTMLElement>(".unified-composer");
    const contentLeft = Math.min(chatContentLeft, composerContent?.getBoundingClientRect().left ?? Number.POSITIVE_INFINITY);
    const mainLeft = main?.getBoundingClientRect().left ?? 0;
    const hasLeftGutter = window.innerWidth > 760 && Number.isFinite(contentLeft) && contentLeft - mainLeft >= 310;
    rail.hidden = !items.length || !hasLeftGutter;
    const compactToggle = root.querySelector<HTMLButtonElement>("#work-activity-compact-toggle");
    const compactCount = root.querySelector<HTMLElement>("#work-activity-compact-count");
    if (compactToggle) compactToggle.hidden = !items.length || hasLeftGutter;
    if (compactCount) compactCount.textContent = items.length ? `${items.length} 项` : "";
    state.textContent = items.length ? `${items.length} 项 · 查看完整历史` : "安静";
    const statusText = { running: "进行中", ok: "已完成", fail: "失败", note: "记录" } as const;
    list.innerHTML = items.map((item) => `<article class="work-activity-item is-${item.status}">
      <span class="work-activity-marker" aria-hidden="true"></span>
      <div class="work-activity-copy"><span class="work-activity-action">${escapeHtml(item.action || "工具动作")}</span>
      ${item.path ? `<span class="work-activity-path" title="${escapeHtml(item.path)}">${escapeHtml(item.path)}</span>` : ""}</div>
      <span class="work-activity-status">${statusText[item.status]}</span>
    </article>`).join("");
    const history = root.querySelector<HTMLElement>("#work-activity-history");
    if (history && workActivityHistoryOpen) {
      const blocks = chat.model.blocks.filter((block): block is ProcessActivityBlock => block.kind === "process");
      history.innerHTML = `<div class="work-activity-history-backdrop" data-work-activity-dismiss></div>
        <section class="work-activity-history-panel" role="document">
          <header class="work-activity-history-header">
            <div><h2 id="work-activity-history-title">工作历史与失败详情</h2><p>当前会话已记录的全部过程，只读回看。</p></div>
            <button type="button" class="work-activity-history-close" id="work-activity-history-close" aria-label="关闭工作历史">×</button>
          </header>
          <div class="work-activity-history-body">${blocks.map((block) => `<section class="work-activity-history-turn"><h3>工作过程</h3>${renderActivityTimeline(block, { liveTurn: isLiveProcessBlock(block), toolsFoldOpen: true, toolDisplay: "all" })}</section>`).join("") || `<p class="work-activity-empty">暂无工作历史。</p>`}</div>
        </section>`;
      history.classList.remove("hidden");
      bindNewProcessToggles(history);
    } else if (history) {
      history.classList.add("hidden");
      history.innerHTML = "";
    }
  }
  function openWorkActivityHistory(opener: HTMLElement): void {
    workActivityOpener = opener;
    workActivityHistoryOpen = true;
    renderWorkActivity();
    root.querySelector<HTMLButtonElement>("#work-activity-history-close")?.focus();
  }
  function closeWorkActivityHistory(): void {
    workActivityHistoryOpen = false;
    renderWorkActivity();
    workActivityOpener?.focus({ preventScroll: true });
    workActivityOpener = null;
  }
  root.querySelector<HTMLButtonElement>("#work-activity-toggle")?.addEventListener("click", (event) => openWorkActivityHistory(event.currentTarget as HTMLElement));
  root.querySelector<HTMLButtonElement>("#work-activity-compact-toggle")?.addEventListener("click", (event) => openWorkActivityHistory(event.currentTarget as HTMLElement));
  root.querySelector<HTMLElement>("#work-activity-history")?.addEventListener("click", (event) => {
    const target = event.target as HTMLElement;
    if (target.closest("#work-activity-history-close") || target.hasAttribute("data-work-activity-dismiss")) closeWorkActivityHistory();
  });
  function onWorkActivityHistoryKeydown(event: KeyboardEvent): void {
    if (!workActivityHistoryOpen || event.key !== "Escape") return;
    event.preventDefault();
    event.stopImmediatePropagation();
    closeWorkActivityHistory();
  }
  document.addEventListener("keydown", onWorkActivityHistoryKeydown, true);
  function onWorkActivityResize(): void {
    renderWorkActivity();
  }
  window.addEventListener("resize", onWorkActivityResize);
  // ---- element refs ----
  const shellEl = root.querySelector<HTMLElement>(".unified-shell")!;
  const sidebarEl = root.querySelector<HTMLElement>("#unified-sidebar")!;
  const unbindChatFontControl = bindChatFontControl(shellEl);
  const topbarEl = root.querySelector<HTMLElement>("#unified-topbar")!;
  const expandEl = root.querySelector<HTMLElement>("#unified-expand")!;
  const chatEl = root.querySelector<HTMLElement>("#unified-chat")!;
  bindImageLightboxClick(chatEl, "img.unified-generated-image");
  const toolRegion = root.querySelector<HTMLElement>("#unified-tool-region")!;
  const goalBudgetNoticeEl = root.querySelector<HTMLElement>("#unified-goal-budget")!;
  let activeGoalSessionId = "";

  const toolTitle = root.querySelector<HTMLElement>("#unified-tool-title")!;
  const runtimeTool = root.querySelector<HTMLElement>("#unified-runtime-tool")!;
  let runtimeToolOpen = false;
  const toolCloseBtn = root.querySelector<HTMLButtonElement>("#unified-tool-close")!;
  toolCloseBtn.addEventListener("click", () => {
    if (runtimeToolOpen) {
      runtimeToolOpen = false;
      syncMainFocusView();
      return;
    }
    if (projectState.mainFocus === "shell-dev" && sandboxSessionActive) {
      void openLatestOrdinarySession();
      return;
    }
    showNowChat();
  });
  const documentEl = root.querySelector<HTMLElement>("#unified-document")!;
  const projectsEl = root.querySelector<HTMLElement>("#unified-projects")!;
  const settingsEl = root.querySelector<HTMLElement>("#unified-settings")!;
  const musicEl = root.querySelector<HTMLElement>("#unified-music")!;
  const shellDevEl = root.querySelector<HTMLElement>("#unified-shell-dev")!;
  const currentWorkEl = root.querySelector<HTMLElement>("#unified-current-work")!;
  let settingsRenderedSection: SettingsSection | null = null;
  let settingsRenderedName = "";
  let settingsRenderedModelState = "";
  let settingsNicknameDraft: string | null = null;
  let settingsFeedback = "";
  let hasBilibiliSearch = false;
  const musicViewResizeObserver = new ResizeObserver(syncMusicViewBounds);
  musicViewResizeObserver.observe(musicEl);
  const shellDevViewResizeObserver = new ResizeObserver(syncShellDevViewBounds);
  shellDevViewResizeObserver.observe(shellDevEl);
  window.addEventListener("resize", syncMusicViewBounds, { passive: true });
  window.addEventListener("resize", syncShellDevViewBounds, { passive: true });
  musicEl.addEventListener("scroll", syncMusicViewBounds, { passive: true });
  shellDevEl.addEventListener("scroll", syncShellDevViewBounds, { passive: true });
  const settingsThemeObserver = new MutationObserver(syncSettingsTheme);
  settingsThemeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  const switchMaskEl = root.querySelector<HTMLElement>("#unified-switch-mask")!;
  const workbenchEmptyEl = root.querySelector<HTMLElement>("#workbench-empty")!;
  const emptyNewBtn = root.querySelector<HTMLButtonElement>("#empty-new-project")!;
  const emptyPickBtn = root.querySelector<HTMLButtonElement>("#empty-pick-project")!;
  const emptyFreeChatBtn = root.querySelector<HTMLButtonElement>("#empty-free-chat")!;
  const composerDock = root.querySelector<HTMLElement>("#unified-composer-dock")!;
  const currentWorkResizeObserver = new ResizeObserver(sizeCurrentWorkPopover);
  currentWorkResizeObserver.observe(composerDock);
  currentWorkResizeObserver.observe(topbarEl);
  window.addEventListener("resize", sizeCurrentWorkPopover, { passive: true });
  const composerMetaEl = root.querySelector<HTMLElement>("#unified-composer-meta")!;
  const statusEl = root.querySelector<HTMLElement>("#unified-status")!;
  const composer = root.querySelector<HTMLElement>("#unified-composer")!;
  const input = root.querySelector<HTMLTextAreaElement>("#unified-input")!;
  const fallbackWorldStorage = new Map<string, string>();
  const worldStorage: Pick<Storage, "getItem" | "setItem"> = (() => {
    try {
      return window.localStorage;
    } catch {
      return {
        getItem: (key: string) => fallbackWorldStorage.get(key) ?? null,
        setItem: (key: string, value: string) => { fallbackWorldStorage.set(key, value); },
      };
    }
  })();
  const worldNow = Date.now();
  let companionWorldState: CompanionWorldState = advanceCompanionWorld(
    loadCompanionWorld(worldStorage, worldNow),
    worldNow,
  ).state;
  saveCompanionWorld(companionWorldState, worldStorage);
  const modeToggleEl = root.querySelector<HTMLElement>("#unified-mode-toggle")!;
  const modeSegButtons = Array.from(modeToggleEl.querySelectorAll<HTMLButtonElement>(".unified-mode-seg"));
  const stopBtn = root.querySelector<HTMLButtonElement>("#unified-stop")!;
  const sendBtn = root.querySelector<HTMLButtonElement>("#unified-send")!;
  const confirmGlass = root.querySelector<HTMLElement>("#unified-confirm-glass")!;
  const workbenchDialogEl = root.querySelector<HTMLElement>("#workbench-dialog")!;
  const tokenBar = root.querySelector<HTMLElement>("#unified-token-bar")!;
  const threadArchiveBannerEl = root.querySelector<HTMLElement>("#thread-archive-banner")!;
  const threadReturnActiveBtn = root.querySelector<HTMLButtonElement>("#thread-return-active")!;
  const sandboxWriteScopeBannerEl = root.querySelector<HTMLElement>("#sandbox-write-scope-banner")!;
  const sandboxWriteScopeBannerTextEl = root.querySelector<HTMLElement>("#sandbox-write-scope-banner-text")!;

  let turnCachePromptTotal = 0;
  let turnCacheCachedTotal = 0;
  let lastLlmCacheRatio: number | undefined;
  let lastTokenUsage: number | undefined;
  let lastTokenLimit: number | undefined;

  function resetTurnCacheStats(): void {
    turnCachePromptTotal = 0;
    turnCacheCachedTotal = 0;
    lastLlmCacheRatio = undefined;
  }

  function recordLlmUsage(promptTokens: number, cachedTokens: number, cacheRatio: number): void {
    turnCachePromptTotal += promptTokens;
    turnCacheCachedTotal += cachedTokens;
    lastLlmCacheRatio = cacheRatio;
    updateTokenBar(lastTokenUsage, lastTokenLimit);
  }

  // project panel elements (only used in project perspective)
  const projectEls = setupProjectPanel(root);
  const runtimeBadges = root.querySelector<HTMLElement>(".sidebar-runtime-badges")!;
  runtimeTool.append(runtimeBadges);
  // ---- sidebar: session list (当下), project row, runtime badges ----
  const sidebarSessionListEl = root.querySelector<HTMLElement>("#sidebar-session-list")!;
  const sidebarSessionFiltersEl = root.querySelector<HTMLElement>("#sidebar-session-filters")!;
  const sidebarSessionSearchEl = root.querySelector<HTMLInputElement>("#sidebar-session-search")!;
  const sidebarProjectRowEl = root.querySelector<HTMLElement>("#sidebar-project-row")!;
  let sidebarSessionQuery = "";
  let sidebarSessionFilter: SessionFilter = "all";
  let sidebarSessionsLoaded = false;
  let lastVerify: { passed: boolean; at: Date } | null = null;
  let projectGitBranch = "";
  let lastVerifyProjectId = "";
  let lastSessionListHtml = "";
  let lastSessionFiltersHtml = "";

  // ---- resize handle ----
  const resizeHandle = root.querySelector<HTMLElement>("#sidebar-resize-handle")!;
  let resizeDragging = false;

  function loadSidebarWidth(): number {
    try {
      const raw = localStorage.getItem("sidebar-width-v2");
      if (raw) {
        const n = parseInt(raw, 10);
        if (n >= 260 && n <= window.innerWidth * 0.5) return n;
      }
    } catch { /* ignore */ }
    return 338;
  }

  function saveSidebarWidth(w: number): void {
    try { localStorage.setItem("sidebar-width-v2", String(w)); } catch { /* ignore */ }
  }

  function applySidebarWidth(w: number): void {
    shellEl.style.setProperty("--sidebar-width", `${w}px`);
  }

  function clampSidebarWidth(w: number): number {
    return Math.max(260, Math.min(Math.round(w), Math.floor(window.innerWidth * 0.5)));
  }

  applySidebarWidth(loadSidebarWidth());

  resizeHandle.addEventListener("mousedown", (ev) => {
    ev.preventDefault();
    resizeDragging = true;
    resizeHandle.classList.add("is-dragging");
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  });

  document.addEventListener("mousemove", (ev) => {
    if (!resizeDragging) return;
    const rect = shellEl.getBoundingClientRect();
    const w = clampSidebarWidth(ev.clientX - rect.left);
    applySidebarWidth(w);
  });

  document.addEventListener("mouseup", () => {
    if (!resizeDragging) return;
    resizeDragging = false;
    resizeHandle.classList.remove("is-dragging");
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    const w = parseInt(
      shellEl.style.getPropertyValue("--sidebar-width").replace("px", ""),
      10,
    );
    if (!isNaN(w)) saveSidebarWidth(w);
  });

  // Q4: grow 无绑项目会话（空态「先聊聊」或恢复的无项目会话）
  let freeChatActive = false;

  function isWorkbenchChatAllowed(): boolean {
    if (isViewingArchivedThread(projectState)) return false;
    return true;
  }

function timeOfDayPartnerPlaceholder(mode: "agent" | "ask" = "agent"): string {
    return mode === "ask" ? "和小魔王说点什么…" : "和小魔王说点什么，或一起做点什么…";
  }

  function syncFreeChatFromSession(hasProject: boolean, sessionId: string): void {
    freeChatActive = !hasProject && Boolean(sessionId);
  }

  function updatePlaceholder(): void {
    if (chat.model.pendingToolPrompt) {
      input.placeholder = "工具正在等待输入；回复会只发给这次工具请求";
      return;
    }
    if (isViewingArchivedThread(projectState)) {
      input.placeholder = "归档线只读回看；点顶栏「回到活线」继续工作";
      return;
    }
    input.placeholder = timeOfDayPartnerPlaceholder(currentTurnMode);
  }

  function syncArchivedViewUi(): void {
    const archived = isViewingArchivedThread(projectState);
    threadArchiveBannerEl.classList.toggle("hidden", !archived);
    updatePlaceholder();
    composerWire.syncSendEnabled();
  }

  let sandboxSessionActive = false;

  function syncSandboxWriteScopeBanner(label?: string): void {
    const text = (label || "").trim();
    const show = Boolean(text);
    sandboxSessionActive = show;
    sandboxWriteScopeBannerEl.classList.toggle("hidden", !show);
    if (sandboxWriteScopeBannerTextEl) {
      sandboxWriteScopeBannerTextEl.textContent = show
        ? `实验进化会话 · ${text}（稳定树只读）`
        : "";
    }
  }

  function updateWorkbenchEmpty(): void {
    workbenchEmptyEl.hidden = true;
    chatEl.classList.remove("is-empty-gated");
    composer.classList.remove("is-empty-gated");
    updatePlaceholder();
    composerWire.syncSendEnabled();
  }

  // ---- file drop + composer ----
  const fileDrop = mountFileDrop({
    composer,
    pasteTargets: [input],
    client,
    shell: shellId,
    canAccept: () => {
      if (chat.model.confirmPending) return false;
      if (isViewingArchivedThread(projectState)) return false;
      return true;
    },
    onChange: () => {
      syncComposerSendEnabled();
      if (hasActiveWorkRun() && fileDrop.getAttachments().length) {
        setStatus("工作进行中暂不支持发送附件；请移除附件后再发送文字");
      }
    },
    onNotice: (text) => setStatus(text),
  });

  function currentDocSelection(): string {
    const sel = window.getSelection()?.toString() || "";
    if (sel.trim()) return sel;
    const editor = documentEl.querySelector<HTMLTextAreaElement>(".unified-document-editor");
    if (editor && editor.selectionStart !== editor.selectionEnd) {
      return editor.value.slice(editor.selectionStart, editor.selectionEnd);
    }
    return "";
  }

  let currentTurnMode: "agent" | "ask" = "agent";

  const composerWire = wireComposerAttachments({
    input,
    sendBtn,
    client,
    chat,
    fileDrop,
    onStatus: (text) => setStatus(text),
    allowSend: () => isWorkbenchChatAllowed(),
    beforeSend: () => {
      topbarState.intentLabel = "";
      setStatus("发送中…");
      resetUserScrollPin();
    },
    messageOpts: () => {
      const path = projectState.currentDocPath || "";
      const selection = path ? currentDocSelection() : "";
      const companionWorldContext = buildCompanionWorldTurnContext(companionWorldState, {
        projectChat: Boolean(projectState.projectId),
      });
      const base = {
        turnMode: currentTurnMode,
        ...(companionWorldContext ? { companionWorldContext } : {}),
        ...(path
          ? {
              activeDoc: path,
              activeDocSelection: selection || undefined,
            }
          : {}),
      };
      return base;
    },
  });

  function hasActiveWorkRun(): boolean {
    return chat.model.executionState === "running" && Boolean(chat.model.executionRunId);
  }

  function canSendDuringWork(): boolean {
    return hasActiveWorkRun() && !chat.model.confirmPending && !chat.model.cancelRequested
      && !isViewingArchivedThread(projectState) && isWorkbenchChatAllowed()
      && input.value.trim().length > 0 && fileDrop.getAttachments().length === 0;
  }

  function syncPromptComposerUi(): void {
    const pending = chat.model.pendingToolPrompt;
    sendBtn.setAttribute("aria-label", pending ? "提交工具输入" : "发送消息");
    sendBtn.title = pending ? "将回复发送给正在等待的工具" : "发送消息";
    updatePlaceholder();
  }

  function syncComposerSendEnabled(): void {
    composerWire.syncSendEnabled();
    if (input.disabled) return;
    const prompt = chat.model.pendingToolPrompt;
    if (prompt) {
      sendBtn.disabled = prompt.state === "sending" || chat.model.confirmPending || chat.model.cancelRequested
        || !input.value.trim() || fileDrop.getAttachments().length > 0;
      return;
    }
    if (hasActiveWorkRun() && !chat.model.confirmPending && !chat.model.cancelRequested) {
      sendBtn.disabled = !canSendDuringWork();
    }
  }

  input.addEventListener("input", syncComposerSendEnabled);

  // ---- visual sync ----
  let lastSidebarWorking = false;
  let processJumpCursor = -1;
  let processJumpTurnIndex = -1;

  function syncModeToggleUi(mode?: string): void {
    if (mode === "ask" || mode === "agent") {
      currentTurnMode = mode;
    }
    modeToggleEl.dataset.mode = currentTurnMode;
    modeToggleEl.title = currentTurnMode === "ask"
      ? "当前：只聊 (Ask) — 只读探索与回答；点击切换为 动手 (Agent)"
      : "当前：动手 (Agent) — 允许写文件与执行命令；点击切换为 只聊 (Ask)";
    const locked = chat.isWorking() || chat.model.confirmPending || isViewingArchivedThread(projectState);
    for (const btn of modeSegButtons) {
      const value = btn.dataset.modeValue === "ask" ? "ask" : "agent";
      const active = value === currentTurnMode;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
      btn.disabled = locked;
    }
  }

  modeToggleEl.addEventListener("click", (event) => {
    if (chat.isWorking() || chat.model.confirmPending) return;
    const target = event.target instanceof Element
      ? event.target.closest<HTMLButtonElement>(".unified-mode-seg")
      : null;
    if (!target || !modeToggleEl.contains(target)) return;
    const next = target.dataset.modeValue === "ask" ? "ask" : "agent";
    if (next === currentTurnMode) return;
    try {
      client.setTurnMode(next);
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err));
    }
  });

  function resetProcessJumpCursor(): void {
    processJumpCursor = -1;
    processJumpTurnIndex = -1;
  }

  function syncWorkingVisual(): void {
    const working = chat.isWorking();
    const workingChanged = lastSidebarWorking !== working;
    lastSidebarWorking = working;
    if (workingChanged) renderSidebarSessions();
    projectState.turnInProgress = working;
    projectEls.goalCard.classList.add("hidden");
    projectEls.goalCard.dataset.goalStatus = deriveProjectGoalViewModel(projectState).status;
    projectEls.goalCard.innerHTML = renderProjectGoalCard(projectState);
    shellEl.classList.toggle("is-working", working);
    statusEl.classList.toggle("is-idle", statusText === "就绪" && !working);
    syncComposerMetaVisibility();
    stopBtn.hidden = !(working || chat.model.confirmPending);
    stopBtn.disabled = chat.model.cancelRequested;
    if (working && (statusText === "发送中…" || statusText.startsWith("发送中"))) {
      setStatus("处理中…");
    }
    syncComposerSendEnabled();
    syncToolElapsedTimer();
    syncModeToggleUi();
    setAgentBusy(working, perspective === "project" ? "project" : "grow");
    if (perspective === "project" && workingChanged) {
      renderSidebar();
      refreshTopbar();
    }
  }

  function sendStopRequest(): void {
    const activeTurn = chat.isWorking() || chat.model.confirmPending;
    if (activeTurn) {
      const runId = chat.model.executionRunId.trim();
      if (!runId) {
        setStatus("无法安全停止：当前工作缺少 run_id");
        return;
      }
      if (!chat.requestCancel()) return;
      setComposerEnabled(false);
      setStatus("正在停止…");
      try {
        client.sendExecutionStop(runId);
      } catch (err) {
        chat.model.cancelRequested = false;
        setComposerEnabled(!chat.model.confirmPending);
        setStatus(`停止发送失败：${err instanceof Error ? err.message : String(err)}`);
        syncWorkingVisual();
      }
      return;
    }
  }

  function useTurnCardLayout(): boolean {
    return perspective !== "night";
  }

  function syncComposerMetaVisibility(): void {
    const statusVisible =
      !statusEl.hidden && !statusEl.classList.contains("is-idle");
    const tokenVisible =
      !tokenBar.hidden && !tokenBar.classList.contains("hidden");
    const showMeta = statusVisible || tokenVisible;
    composerMetaEl.classList.toggle("hidden", !showMeta);
    composerMetaEl.hidden = !showMeta;
  }

  function pendingConfirmRequestId(): string | null {
    return resolveActiveOrdinaryConfirmRequestId(chat.model);
  }

  function companionConfirmUi() {
    const companionComposerPrimary = companionHomeComposerOwnsOrdinaryConfirm({
      mainFocus: projectState.mainFocus,
      perspective,
    });
    const chatRendersOrdinaryConfirm = perspective !== "night"
      && ordinaryConfirmWouldRenderInChat(chat.model, { companionComposerPrimary });
    const overlayRendersOrdinaryConfirm = perspective === "night"
      && ordinaryConfirmHasOverlayPrimarySurface(chat.model);
    return { companionComposerPrimary, chatRendersOrdinaryConfirm, overlayRendersOrdinaryConfirm };
  }

  function syncPendingConfirmComposerHint(): void {
    const awaiting = chatSessionAwaitingUserConfirm(chat.model);
    let hint = composerDock.querySelector<HTMLElement>("[data-pending-confirm-hint]");
    const overlay = chat.model.confirmOverlay;
  if (
      overlay?.requestId
      && overlay.resolved
      && !isConfirmInProgressLabel(overlay.resolved)
    ) {
      hint?.remove();
      if (!awaiting) return;
    }
    if (!awaiting) {
      hint?.remove();
      return;
    }
    const preview = primaryPendingConfirmPreview(chat.model);
    const requestId = pendingConfirmRequestId();
    if (!preview.trim() || !requestId) {
      hint?.remove();
      return;
    }
    const confirmUi = companionConfirmUi();
    const canAct = canSubmitOrdinaryConfirm(chat.model, requestId);
    const inFlight = !canAct && resolveActiveOrdinaryConfirmRequestId(chat.model) === requestId;
    const showComposerActions = composerShouldShowOrdinaryConfirmActions(chat.model, {
      chatRendersOrdinaryConfirm: confirmUi.chatRendersOrdinaryConfirm,
      overlayRendersOrdinaryConfirm: confirmUi.overlayRendersOrdinaryConfirm,
      companionComposerPrimary: confirmUi.companionComposerPrimary,
    });
    if (!showComposerActions) {
      if (!inFlight) {
        hint?.remove();
        return;
      }
      if (!hint) {
        hint = document.createElement("section");
        hint.dataset.pendingConfirmHint = "1";
        hint.className = "unified-pending-confirm-hint";
        hint.setAttribute("role", "region");
        hint.setAttribute("aria-label", "待确认授权");
        composerDock.insertBefore(hint, composerMetaEl);
      }
      hint.innerHTML = `<div class="unified-pending-confirm-hint-head"><strong>等待你的确认</strong></div><p class="unified-pending-confirm-hint-detail text-muted" role="status">确认已提交，等待服务端…</p>`;
      return;
    }
    if (!hint) {
      hint = document.createElement("section");
      hint.dataset.pendingConfirmHint = "1";
      hint.className = "unified-pending-confirm-hint";
      hint.setAttribute("role", "region");
      hint.setAttribute("aria-label", "待确认授权");
      composerDock.insertBefore(hint, composerMetaEl);
    }
    const detail = renderComposerOrdinaryConfirmDetailHtml(preview);
    const disabled = canAct ? "" : " disabled";
    const statusNote = inFlight
      ? `<p class="unified-pending-confirm-hint-detail text-muted" role="status">确认已提交，等待服务端…</p>`
      : "";
    const jump = confirmUi.companionComposerPrimary
      ? ""
      : `<button type="button" class="unified-btn unified-btn-ghost" data-pending-confirm-jump>查看确认卡</button>`;
    hint.innerHTML = `<div class="unified-pending-confirm-hint-head"><strong>等待你的确认</strong>${jump}</div>${detail}${statusNote}<div class="unified-pending-confirm-hint-actions"><button type="button" class="unified-btn unified-btn-accent" data-confirm="y" data-id="${escapeHtml(requestId)}"${disabled}>同意</button><button type="button" class="unified-btn" data-confirm="n" data-id="${escapeHtml(requestId)}"${disabled}>拒绝</button></div>`;
  }

  function appendPendingConfirmFallbackHtml(html: string): string {
    if (companionHomeComposerOwnsOrdinaryConfirm({ mainFocus: projectState.mainFocus, perspective })) return html;
    if (!chatSessionAwaitingUserConfirm(chat.model)) return html;
    if (html.includes("unified-confirm") || html.includes("data-project-worker-batch-confirm")) return html;
    const preview = primaryPendingConfirmPreview(chat.model);
    const requestId = pendingConfirmRequestId();
    if (!preview.trim() || !requestId) return html;
    const canAct = canSubmitOrdinaryConfirm(chat.model, requestId);
    const disabled = canAct ? "" : " disabled";
    const actions = `<button type="button" class="unified-btn unified-btn-accent" data-confirm="y" data-id="${escapeHtml(requestId)}"${disabled}>允许此次</button>`
      + `<button type="button" class="unified-btn unified-btn-ghost" data-confirm="n" data-id="${escapeHtml(requestId)}"${disabled}>拒绝</button>`;
    return `${html}<div class="unified-pending-confirm-fallback">${renderConfirmCardHtml(preview, actions)}</div>`;
  }

  function clearProjectWorkerProgressStatus(): void {
    if (projectWorkerProgressTimer !== null) {
      window.clearTimeout(projectWorkerProgressTimer);
      projectWorkerProgressTimer = null;
    }
    projectWorkerProgressStatus.clear();
    if (projectWorkerProgressVisible) {
      projectWorkerProgressVisible = false;
      paintStatus(statusText);
    }
  }

  function paintStatus(text: string): void {
    statusEl.textContent = text;
    const idle = text === "就绪" && !shellEl.classList.contains("is-working");
    statusEl.classList.toggle("is-idle", idle);
    syncComposerMetaVisibility();
  }

  function setStatus(text: string): void {
    clearProjectWorkerProgressStatus();
    statusText = text;
    paintStatus(text);
  }

  function showProjectWorkerProgress(event: ServerEvent): void {
    const progressText = projectWorkerProgressStatus.accept(event, {
      sessionId: chat.model.sessionId,
      runId: chat.model.executionRunId,
      executionState: chat.model.executionState,
      turnActive: chat.model.turnActive,
    });
    if (!progressText) return;
    if (projectWorkerProgressTimer !== null) window.clearTimeout(projectWorkerProgressTimer);
    projectWorkerProgressVisible = true;
    statusEl.textContent = progressText;
    statusEl.classList.remove("is-idle");
    syncComposerMetaVisibility();
    projectWorkerProgressTimer = window.setTimeout(() => {
      projectWorkerProgressTimer = null;
      projectWorkerProgressStatus.clear();
      projectWorkerProgressVisible = false;
      paintStatus(statusText);
    }, PROJECT_WORKER_PROGRESS_TTL_MS);
  }

  /** Feed / 小互动：输入框上方短句，不触发聊天区重绘或滚动。 */
  function showComposerWhisper(text: string, durationMs = 6500): void {
    if (composerWhisperTimer !== null) {
      window.clearTimeout(composerWhisperTimer);
      composerWhisperTimer = null;
    }
    if (cancelledStatusTimer !== null) {
      window.clearTimeout(cancelledStatusTimer);
      cancelledStatusTimer = null;
    }
    setStatus(text);
    statusEl.classList.remove("is-idle");
    syncComposerMetaVisibility();
    composerWhisperTimer = window.setTimeout(() => {
      composerWhisperTimer = null;
      if (!chat.isWorking() && !chat.model.confirmPending) setStatus("就绪");
    }, durationMs);
  }

  function patchCompanionCareCounts(): void {
    const bond = loadCompanionBond();
    const syncToy = (kind: "coffee" | "glasses" | "music", count: number) => {
      chatEl.querySelectorAll<HTMLButtonElement>(`[data-companion-care="${kind}"]`).forEach((btn) => {
        let cnt = btn.querySelector<HTMLSpanElement>(".cnt");
        if (count > 0) {
          if (!cnt) {
            cnt = document.createElement("span");
            cnt.className = "cnt";
            btn.appendChild(cnt);
          }
          cnt.textContent = String(count);
        } else {
          cnt?.remove();
        }
      });
    };
    syncToy("coffee", bond.coffeeCount);
    syncToy("glasses", bond.glassesCount);
    syncToy("music", bond.musicCount);
    const sidebarCoffee = root.querySelector<HTMLButtonElement>('[data-sidebar-action="care-coffee"]');
    if (sidebarCoffee) {
      sidebarCoffee.textContent = bond.coffeeCount > 0 ? `🧋 ${bond.coffeeCount}` : "🧋";
    }
  }

  function onCompanionCare(kind: "coffee" | "glasses" | "music"): void {
    const { reply } = recordCompanionCare(kind);
    syncCompanionToBackend();
    renderSidebar();
    patchCompanionCareCounts();
    showComposerWhisper(reply);
  }

  function setTurnEndStatus(finishReason: string): void {
    if (cancelledStatusTimer !== null) {
      window.clearTimeout(cancelledStatusTimer);
      cancelledStatusTimer = null;
    }
    const label = turnEndStatusText(finishReason);
    if (label === null) {
      setStatus("就绪");
      return;
    }
    setStatus(label);
    cancelledStatusTimer = window.setTimeout(() => {
      if (!chat.isWorking() && !chat.model.confirmPending) setStatus("就绪");
      cancelledStatusTimer = null;
    }, 2000);
  }

  function updateTokenBar(usage: number | undefined, limit: number | undefined): void {
    // Ignore spurious zero from backend before deferred messages.jsonl is loaded.
    if (usage === 0 && lastTokenUsage !== undefined && lastTokenUsage > 0) {
      usage = lastTokenUsage;
    }
    if (usage !== undefined) lastTokenUsage = usage;
    if (limit !== undefined) lastTokenLimit = limit;
    if (usage === undefined || limit === undefined || limit <= 0) {
      tokenBar.classList.add("hidden");
      syncComposerMetaVisibility();
      return;
    }
    const ratio = usage / limit;
    // Keep the room clean: only surface token warning when context is nearing capacity (≥80%).
    if (ratio < 0.80) {
      tokenBar.classList.add("hidden");
      syncComposerMetaVisibility();
      return;
    }
    tokenBar.classList.remove("hidden");
    let cls = "unified-token-bar";
    if (ratio >= 0.95) cls += " unified-token-red";
    else if (ratio >= 0.85) cls += " unified-token-yellow";
    const usageK = Math.round(usage / 1000);
    const limitK = Math.round(limit / 1000);
    tokenBar.className = cls;
    tokenBar.textContent = `记忆容量 ${Math.round(ratio * 100)}%（${usageK}k / ${limitK}k）`;
    tokenBar.title = "当前对话上下文占用较高，可随时新开会话";
    syncComposerMetaVisibility();
  }

  function setComposerEnabled(enabled: boolean): void {
    input.disabled = !enabled;
    composer.classList.toggle("disabled", !enabled);
    if (enabled) {
      syncComposerSendEnabled();
    } else {
      sendBtn.disabled = true;
    }
  }

  async function handleRestartToday(): Promise<void> {
    if (chat.isWorking()) {
      setStatus("助手执行中，请稍后再今日重来");
      return;
    }
    const ok = await showWorkbenchConfirm(
      "将清空当前聊天区并开始新一段对话；上一段仍保留在磁盘，并记入按天日记。文件改动不会自动回滚。继续？",
    );
    if (!ok) return;
    try {
      client.restartToday();
      setStatus("正在今日重来…");
    } catch (err) {
      setStatus(`今日重来失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  async function handleNewThread(): Promise<void> {
    if (chat.isWorking()) {
      setStatus("助手执行中，请稍后再新开线");
      return;
    }
    if (!projectState.projectId) {
      setStatus("请先打开项目");
      return;
    }
    const ok = await showWorkbenchConfirm(
      "将为当前项目新开一条会话线（聊天区清空）；当前活线将归档，可在侧栏「会话线」回看。继续？",
    );
    if (!ok) return;
    try {
      client.newProjectThread(projectState.projectId);
      setStatus("正在新开线…");
    } catch (err) {
      setStatus(`新开线失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }


  function sessionListInput(): SessionListInput {
    const sid = String((chat.model as { sessionId?: string }).sessionId || projectState.currentSessionId || "");
    return {
      sessions: sessionsDropdown,
      threads: projectState.threads,
      projectId: projectState.projectId,
      activeSessionId: projectState.activeSessionId,
      currentSessionId: sid,
      currentLane: client.getLane(),
      working: chat.isWorking(),
      query: sidebarSessionQuery,
      filter: sidebarSessionFilter,
      loaded: sidebarSessionsLoaded,
    };
  }

  function renderSidebarSessions(): void {
    const input = sessionListInput();
    if (input.filter === "project" && !input.projectId) sidebarSessionFilter = input.filter = "all";
    const filtersHtml = renderSessionFiltersHtml(input);
    if (filtersHtml !== lastSessionFiltersHtml) {
      lastSessionFiltersHtml = filtersHtml;
      sidebarSessionFiltersEl.innerHTML = filtersHtml;
    }
    const listHtml = renderSessionListHtml(input);
    if (listHtml !== lastSessionListHtml) {
      lastSessionListHtml = listHtml;
      const scroll = sidebarSessionListEl.scrollTop;
      sidebarSessionListEl.innerHTML = listHtml;
      sidebarSessionListEl.scrollTop = scroll;
    }
  }

  function formatClock(d: Date): string {
    return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  }

  function renderSidebarProjectRow(): void {
    const pid = projectState.projectId;
    const routine = ambientRoutineStatus();
    const bond = loadCompanionBond();
    const days = Math.max(1, Math.ceil((Date.now() - bond.firstMetAt) / 86_400_000));
    const partner = currentPartnerName();
    if (!pid) {
      sidebarProjectRowEl.innerHTML = `<div class="sidebar-project-row-inner is-companion-dock">
        <img class="sidebar-companion-ava" src="${petAvatarSrc("idle", true)}" alt="" width="34" height="34" draggable="false" />
        <div class="sidebar-project-main">
          <div class="sidebar-companion-head">
            <span class="sidebar-project-name">${escapeHtml(partner)} · 相伴 ${days} 天</span>
            <button type="button" class="sidebar-companion-care-btn" data-sidebar-action="care-coffee" title="随手投喂她一杯冰美式">🧋 ${bond.coffeeCount || ""}</button>
          </div>
          <span class="sidebar-project-sub is-multiline">${escapeHtml(routine)}</span>
        </div>
      </div>`;
      return;
    }
    const currentProjectFacts = lastVerifyProjectId === pid;
    sidebarProjectRowEl.innerHTML = renderProjectCompanionDockHtml({
      partnerName: partner,
      daysTogether: days,
      projectId: pid,
      projectName: projectState.projectDisplayName,
      projectDirectory: projectState.projectDirectory,
      avatarSrc: petAvatarSrc("idle", true),
      branch: currentProjectFacts ? projectGitBranch : "",
      verification: currentProjectFacts && lastVerify
        ? `上次验证 ${lastVerify.passed ? "✓" : "✗"} ${formatClock(lastVerify.at)}`
        : "",
    });
  }

  function renderSidebar(): void {
    renderProjectSidebar(projectEls, projectState, projectCallbacks);
    renderSidebarSessions();
    renderSidebarProjectRow();
    if (projectState.mainFocus === "settings") renderSettingsStage();
  }

  async function refreshSidebarSessionsMerged(): Promise<void> {
    try {
      sessionsDropdown = await client.listSessionsMerged();
      sidebarSessionsLoaded = true;
      calibrateCompanionFirstMetFromSessions(sessionsDropdown);
      syncCompanionToBackend();
      renderSidebarSessions();
      renderSidebarProjectRow();
      topbarState.sessionCount = sessionsDropdown.length;
      refreshTopbar();
      if (sessionsOpen) renderSessionsDropdown();
    } catch {
      try { client.listSessions(); } catch { /* offline: keep the last list */ }
    }
    refreshProjectThreads();
  }

  function refreshSidebarSessions(): void {
    void refreshSidebarSessionsMerged();
  }

  function openSidebarSession(
    sid: string,
    projectId: string,
    lane: "stable" | "sandbox" = "stable",
  ): void {
    if (!sid || (sid === sessionListInput().currentSessionId && lane === client.getLane())) return;
    if (chat.isWorking()) {
      setStatus("助手执行中，请稍后再切换会话");
      return;
    }
    void (async () => {
      try {
        await openSessionThenBeginSwitch(
          () => client.openSession(sid, lane),
          () => beginBoundSessionSwitch(sid, projectId, "打开会话"),
        );
      } catch (err) {
        completeHydrationWait();
        setStatus(`打开失败：${err instanceof Error ? err.message : String(err)}`);
      }
    })();
  }

  async function openLatestOrdinarySession(): Promise<void> {
    showNowChat();
    if (chat.isWorking()) {
      setStatus("助手执行中，先回到了对话；结束后再换回普通会话");
      return;
    }
    if (!sidebarSessionsLoaded) {
      await refreshSidebarSessionsMerged();
    }
    const current = chat.model.sessionId || "";
    const ordinary = sessionsDropdown
      .filter((item) => item.session_id && item.session_id !== current && !item.title.startsWith("[实验]"))
      .sort((left, right) => (right.updated_at || "").localeCompare(left.updated_at || ""));
    const next = ordinary[0];
    if (next) {
      openSidebarSession(next.session_id, next.project_id || "", next.execution_lane === "sandbox" ? "sandbox" : "stable");
      return;
    }
    try {
      client.sendCommand("新会话");
      setStatus("正在回到普通对话…");
    } catch (err) {
      setStatus(`回到普通对话失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  root.querySelector<HTMLButtonElement>("#sandbox-back-to-chat")?.addEventListener("click", () => {
    void openLatestOrdinarySession();
  });

  function handleSidebarNewSession(): void {
    if (projectState.projectId) {
      void handleNewThread();
      return;
    }
    if (chat.isWorking()) {
      setStatus("助手执行中，请稍后再开新对话");
      return;
    }
    if (freeChatActive && chat.model.blocks.length === 0) {
      input.focus();
      return;
    }
    try {
      clearChatForSwitch();
      client.sendCommand("新会话");
      setStatus("正在新建会话…");
      input.focus();
    } catch (err) {
      setStatus(`新建会话失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  sidebarSessionSearchEl.addEventListener("input", () => {
    sidebarSessionQuery = sidebarSessionSearchEl.value;
    renderSidebarSessions();
  });
  sidebarSessionSearchEl.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") {
      sidebarSessionSearchEl.value = "";
      sidebarSessionQuery = "";
      renderSidebarSessions();
    }
  });
  sidebarSessionFiltersEl.addEventListener("click", (ev) => {
    const btn = (ev.target as HTMLElement).closest<HTMLButtonElement>("[data-session-filter]");
    const value = btn?.dataset.sessionFilter;
    if (value !== "all" && value !== "chat" && value !== "project") return;
    sidebarSessionFilter = value;
    renderSidebarSessions();
  });
  sidebarSessionListEl.addEventListener("click", (ev) => {
    const target = ev.target as HTMLElement;
    const thread = target.closest<HTMLElement>("[data-thread-id]");
    if (thread?.dataset.threadId) {
      projectCallbacks.onOpenThread?.(thread.dataset.threadId);
      return;
    }
    const row = target.closest<HTMLElement>("[data-open-session]");
    if (row?.dataset.openSession) {
      const lane = row.dataset.sidecarLane === "sandbox" ? "sandbox" : "stable";
      openSidebarSession(row.dataset.openSession, row.dataset.projectId || "", lane);
    }
  });
  root.querySelector<HTMLButtonElement>("#sidebar-new-chat")!.addEventListener("click", () => handleSidebarNewSession());
  sidebarProjectRowEl.addEventListener("click", (ev) => {
    const btn = (ev.target as HTMLElement).closest<HTMLElement>("[data-sidebar-action]");
    const action = btn?.dataset.sidebarAction;
    if (action === "open-projects") selectRailTab("projects");
    else if (action === "new-project") void handleNewProject();
    else if (action === "care-coffee") {
      ev.stopPropagation();
      onCompanionCare("coffee");
    }
  });
  // Single-chat mode does not expose session creation or session search shortcuts.

  function refreshProjectThreads(): void {
    if (!projectState.projectId) return;
    if (refreshProjectThreadsTimer !== null) {
      window.clearTimeout(refreshProjectThreadsTimer);
    }
    refreshProjectThreadsTimer = window.setTimeout(() => {
      refreshProjectThreadsTimer = null;
      refreshProjectThreadsNow();
    }, 0);
  }

  let refreshProjectThreadsTimer: number | null = null;
  let listSessionsTimer: number | null = null;
  let listProjectsTimer: number | null = null;
  /** UI-5972: suppress redundant list* until switch/open batch finishes (session.history). */
  let pendingSwitchBatch = false;
  let hydrationSessionId = "";

  function startHydrationWait(sessionId: string, label: string): void {
    hydrationSessionId = sessionId;
    pendingSwitchBatch = true;
    projectState.switchInProgress = true;
    setStatus(`${label}…`);
    syncSessionSwitchMask();
    renderSidebar();
  }

  function completeHydrationWait(sessionId?: string): void {
    if (sessionId && hydrationSessionId && sessionId !== hydrationSessionId) return;
    const wasProjectSwitch = projectState.switchInProgress;
    hydrationSessionId = "";
    pendingSwitchBatch = false;
    projectState.switchInProgress = false;
    projectState.pendingPickerId = "";
    projectState.switchOverlay = null;
    syncSessionSwitchMask();
    debouncedListProjects();
    debouncedListSessions();
    renderSidebar();
    refreshTopbar();
    if (wasProjectSwitch && projectState.projectId) {
      refreshServices();
      refreshTerminals();
    }
  }

  function syncSessionSwitchMask(): void {
    const switching = Boolean(
      projectState.switchInProgress
      || projectState.switchOverlay
      || hydrationSessionId,
    );
    switchMaskEl.classList.toggle("hidden", !switching);
    shellEl.classList.toggle("is-session-switching", switching);
    const target = (
      projectState.pendingPickerId
      || projectState.switchOverlay?.projectId
      || projectState.projectId
      || "会话"
    ).trim();
    const card = switchMaskEl.querySelector(".unified-switch-mask-card");
    if (card) {
      card.textContent = projectState.switchOverlay
        ? `切换到 ${target} 需要确认`
        : `正在加载 ${target}…`;
    }
  }

  function clearChatForSwitch(): void {
    autoRecognition = { sessionId: "", enabled: false, pending: "", action: "", error: false };
    chat.model.blocks = [];
    chat.model.sessionId = "";
    chat.model.turnCounter = 0;
    chat.model.currentTurnKey = "";
    chat.model.assistantBuffer = "";
    chat.model.confirmPending = false;
    chat.model.confirmSubmitting = false;
    chat.model.cancelRequested = false;
    chat.model.confirmOverlay = null;
    chat.model.executionRunId = "";
    chat.model.executionState = "idle";
    chat.model.executionSequence = 0;
    chat.model.executionFinishReason = null;
    renderedPrints = [];
    renderedSegmentPrints = [];
    renderChat();
  }

  function beginBoundSessionSwitch(sessionId: string, projectId: string, label: string): void {
    const sid = sessionId.trim();
    const pid = projectId.trim();
    if (pid && isCrossProjectSession(projectState.projectId, pid)) {
      beginAtomicProjectSwitch(projectState, pid);
      clearChatForSwitch();
      setMainFocus("chat");
      refreshTopbar();
    } else if (sid && sid !== chat.model.sessionId) {
      clearChatForSwitch();
    }
    startHydrationWait(sid, label);
  }

  function debouncedListSessions(): void {
    if (pendingSwitchBatch) return;
    if (listSessionsTimer !== null) window.clearTimeout(listSessionsTimer);
    listSessionsTimer = window.setTimeout(() => {
      listSessionsTimer = null;
      refreshSidebarSessions();
    }, 250);
  }

  function debouncedListProjects(): void {
    if (listProjectsTimer !== null) window.clearTimeout(listProjectsTimer);
    listProjectsTimer = window.setTimeout(() => {
      listProjectsTimer = null;
      client.listProjects();
    }, 250);
  }

  function refreshProjectThreadsNow(): void {
    if (!projectState.projectId) return;
    projectState.threadsLoading = true;
    renderSidebar();
    try {
      client.listProjectThreads(projectState.projectId);
    } catch (err) {
      projectState.threadsLoading = false;
      renderSidebar();
      setStatus(`加载会话线失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  // ---- workbench dialog (Electron does not support window.prompt/confirm) ----
  type WorkbenchDialogState =
    | { kind: "prompt"; title: string; value: string; resolve: (value: string | null) => void }
    | { kind: "confirm"; title: string; resolve: (value: boolean) => void };

  let workbenchDialogState: WorkbenchDialogState | null = null;

  function closeWorkbenchDialog(): void {
    workbenchDialogState = null;
    workbenchDialogEl.classList.add("hidden");
    workbenchDialogEl.innerHTML = "";
  }

  function renderWorkbenchDialog(): void {
    if (!workbenchDialogState) {
      closeWorkbenchDialog();
      return;
    }
    workbenchDialogEl.classList.remove("hidden");
    if (workbenchDialogState.kind === "confirm") {
      workbenchDialogEl.innerHTML = `
        <div class="unified-confirm-glass-card" role="document">
          <div class="unified-confirm-glass-title">${escapeHtml(workbenchDialogState.title)}</div>
          <div class="unified-confirm-glass-actions">
            <button type="button" class="unified-btn unified-btn-accent" id="workbench-dialog-ok">确定</button>
            <button type="button" class="unified-btn" id="workbench-dialog-cancel">取消</button>
          </div>
        </div>
      `;
      return;
    }
    workbenchDialogEl.innerHTML = `
      <div class="unified-confirm-glass-card" role="document">
        <div class="unified-confirm-glass-title">${escapeHtml(workbenchDialogState.title)}</div>
        <input
          type="text"
          class="workbench-dialog-input"
          id="workbench-dialog-input"
          value="${escapeHtml(workbenchDialogState.value)}"
          autocomplete="off"
          spellcheck="false"
        />
        <div class="unified-confirm-glass-actions">
          <button type="button" class="unified-btn unified-btn-accent" id="workbench-dialog-ok">确定</button>
          <button type="button" class="unified-btn" id="workbench-dialog-cancel">取消</button>
        </div>
      </div>
    `;
    const inputEl = workbenchDialogEl.querySelector<HTMLInputElement>("#workbench-dialog-input");
    inputEl?.focus();
    inputEl?.select();
  }

  function showWorkbenchPrompt(title: string): Promise<string | null> {
    return new Promise((resolve) => {
      workbenchDialogState = { kind: "prompt", title, value: "", resolve };
      renderWorkbenchDialog();
    });
  }

  function showWorkbenchConfirm(title: string): Promise<boolean> {
    return new Promise((resolve) => {
      workbenchDialogState = { kind: "confirm", title, resolve };
      renderWorkbenchDialog();
    });
  }

  workbenchDialogEl.addEventListener("click", (ev) => {
    if (!workbenchDialogState) return;
    const target = ev.target as HTMLElement;
    if (target.id === "workbench-dialog-cancel") {
      const state = workbenchDialogState;
      closeWorkbenchDialog();
      if (state.kind === "confirm") state.resolve(false);
      else state.resolve(null);
      return;
    }
    if (target.id !== "workbench-dialog-ok") return;
    const state = workbenchDialogState;
    if (state.kind === "confirm") {
      closeWorkbenchDialog();
      state.resolve(true);
      return;
    }
    const inputEl = workbenchDialogEl.querySelector<HTMLInputElement>("#workbench-dialog-input");
    const value = inputEl?.value ?? "";
    closeWorkbenchDialog();
    state.resolve(value);
  });

  workbenchDialogEl.addEventListener("keydown", (ev) => {
    if (!workbenchDialogState) return;
    if (ev.key === "Escape") {
      ev.preventDefault();
      const state = workbenchDialogState;
      closeWorkbenchDialog();
      if (state.kind === "confirm") state.resolve(false);
      else state.resolve(null);
      return;
    }
    if (ev.key !== "Enter" || workbenchDialogState.kind !== "prompt") return;
    ev.preventDefault();
    const state = workbenchDialogState;
    const inputEl = workbenchDialogEl.querySelector<HTMLInputElement>("#workbench-dialog-input");
    const value = inputEl?.value ?? "";
    closeWorkbenchDialog();
    state.resolve(value);
  });

  // ---- new chat / new project (UX-POLISH §7.6) ----
  async function handleFreeChat(): Promise<void> {
    if (chat.isWorking()) {
      setStatus("助手执行中，请稍后再开对话");
      return;
    }
    if (freeChatActive && !projectState.projectId) {
      input.focus();
      return;
    }
    try {
      client.sendCommand("新会话");
      setStatus("普通对话…");
    } catch (err) {
      setStatus(`开对话失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  async function handleNewChat(): Promise<void> {
    if (chat.isWorking()) {
      setStatus("助手执行中，请稍后再开新对话");
      return;
    }
    if (projectState.projectId) {
      const ok = await showWorkbenchConfirm(
        "将挂起当前项目会话并打开普通对话（可在此用 write_evolve 造工具；不是同项目新开线）。继续？",
      );
      if (!ok) return;
    }
    try {
      client.sendCommand("新会话");
      setStatus("普通对话…");
    } catch (err) {
      setStatus(`开对话失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  async function handleNewProject(): Promise<void> {
    if (chat.isWorking()) {
      setStatus("助手执行中，请稍后再新建项目");
      return;
    }
    if (projectState.projectId) {
      const ok = await showWorkbenchConfirm("离开当前项目去建新项目？");
      if (!ok) return;
    }
    const raw = await showWorkbenchPrompt("新项目 id（字母数字与连字符）:");
    if (raw === null) return;
    const id = raw.trim();
    if (!id) {
      setStatus("未输入项目 id");
      return;
    }
    try {
      client.sendCommand(`项目 新建 ${id} --light`);
      setStatus(`新建项目 ${id}…`);
    } catch (err) {
      setStatus(`新建项目失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  // ---- sessions dropdown ----
  function relativeTime(iso: string): string {
    if (!iso) return "";
    const then = new Date(iso).getTime();
    const diffMs = Date.now() - then;
    if (diffMs < 0) return "";
    const min = Math.floor(diffMs / 60000);
    if (min < 1) return "刚刚";
    if (min < 60) return `${min} 分钟前`;
    const hours = Math.floor(min / 60);
    if (hours < 24) return `${hours} 小时前`;
    const days = Math.floor(hours / 24);
    if (days < 7) return `${days} 天前`;
    return new Date(iso).toLocaleDateString("zh-CN");
  }

  function handleOpenSessions(): void {
    try {
      client.listSessions();
    } catch (err) {
      setStatus(`获取会话列表失败：${err instanceof Error ? err.message : String(err)}`);
      return;
    }
    sessionsOpen = !sessionsOpen;
    if (!sessionsOpen) {
      expandEl.classList.add("hidden");
      expandEl.innerHTML = "";
      return;
    }
    sessionsTab = perspective === "project" || projectState.projectId ? "project" : "chat";
    renderSessionsDropdown();
  }

  function renderSessionsDropdown(): void {
    const chatItems = sessionsDropdown.filter((s) => !s.project_id);
    const projectItems = sessionsDropdown.filter((s) => !!s.project_id);
    const items = sessionsTab === "project" ? projectItems : chatItems;
    const emptyHint = sessionsDropdown.length
      ? sessionsTab === "project"
        ? "暂无可恢复的项目会话 · 项目目录请从左侧打开"
        : "暂无普通对话"
      : "加载中…";

    let listHtml = "";
    if (!items.length) {
      listHtml = `<p class="text-muted unified-sessions-empty">${emptyHint}</p>`;
    } else {
      for (const s of items) {
        const isCurrent = s.session_id === (chat.model as any).sessionId;
        const time = relativeTime(s.updated_at);
        const preview =
          sessionsTab === "chat" && s.preview
            ? `<span class="unified-expand-item-preview">${escapeHtml(s.preview)}</span>`
            : "";
        const count = s.message_count ? `${s.message_count} 条消息` : "";
        const meta = [time, count].filter(Boolean).join(" · ");
        listHtml += `<div class="unified-expand-item ${isCurrent ? "is-current" : ""}">
          <span class="unified-expand-item-title">${escapeHtml(s.title)}</span>
          ${preview}
          <span class="unified-expand-item-meta">${escapeHtml(sessionListMeta(meta, s.session_id))}</span>
          <button type="button" class="unified-btn" data-open-session="${escapeHtml(s.session_id)}" ${isCurrent ? "disabled" : ""}>${isCurrent ? "当前" : "打开"}</button>
        </div>`;
      }
    }

    expandEl.innerHTML = `
      <div class="unified-sessions-header">
        <div class="unified-expand-title">会话 <button type="button" class="unified-btn" id="unified-sessions-close">关闭</button></div>
        <div class="unified-sessions-tabs" role="tablist">
          <button type="button" class="unified-sessions-tab ${sessionsTab === "chat" ? "is-active" : ""}" data-sessions-tab="chat" role="tab">对话 (${chatItems.length})</button>
          <button type="button" class="unified-sessions-tab ${sessionsTab === "project" ? "is-active" : ""}" data-sessions-tab="project" role="tab">项目会话 (${projectItems.length})</button>
        </div>
      </div>
      <div class="unified-sessions-list">${listHtml}</div>
    `;
    expandEl.classList.remove("hidden");

    expandEl.querySelector("#unified-sessions-close")?.addEventListener("click", () => {
      sessionsOpen = false;
      expandEl.classList.add("hidden");
      expandEl.innerHTML = "";
    });
    expandEl.querySelectorAll<HTMLButtonElement>("[data-sessions-tab]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const tab = btn.dataset.sessionsTab;
        if (tab !== "chat" && tab !== "project") return;
        sessionsTab = tab;
        renderSessionsDropdown();
      });
    });
    expandEl.querySelectorAll<HTMLButtonElement>("[data-open-session]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const sid = btn.dataset.openSession;
        if (!sid) return;
        const item = sessionsDropdown.find((s) => s.session_id === sid);
        const lane = item?.execution_lane === "sandbox" ? "sandbox" : "stable";
        try {
          await openSessionThenBeginSwitch(
            () => client.openSession(sid, lane),
            () => beginBoundSessionSwitch(sid, item?.project_id || "", "打开会话"),
          );
          sessionsOpen = false;
          expandEl.classList.add("hidden");
          expandEl.innerHTML = "";
        } catch (err) {
          completeHydrationWait();
          setStatus(`打开失败：${err instanceof Error ? err.message : String(err)}`);
        }
      });
    });
  }

  // ---- proposals panel helpers ----
  function syncProposalsState(): void {
    proposalsState.proposals = topbarState.proposals;
    // proposalsState and topbarState share proposals array;
    // sync is called before each renderProposalsPanel()
  }

  function openProposals(): void {
    memoryShelfState.expandOpen = false;
    proposalsState.expandOpen = true;
    syncProposalsState();
    renderProposalsPanel();
  }

  function renderProposalsPanel(): void {
    syncProposalsState();
    renderProposals(expandEl, proposalsState, client);

    // Listen for custom events dispatched by proposals.ts buttons
    expandEl.addEventListener("proposals:next", () => {
      proposalsState.proposalIndex = nextProposalIndex(proposalsState);
      renderProposalsPanel();
    }, { once: true });

    expandEl.addEventListener("proposals:close", () => {
      proposalsState.expandOpen = false;
      renderProposalsPanel();
    }, { once: true });
  }

  function handleProposalsEvent(items: ProposalItem[]): void {
    proposalsState.proposals = items;
    proposalsState.proposalIndex = 0;
    if (!items.length) proposalsState.expandOpen = false;
    topbarState.proposals = items;
    memoryShelfState.pending = items;
    topbarState.pendingMemoryCount = items.length;
    syncProposalsState();
    refreshTopbar();
    renderProposalsPanel();
    if (memoryShelfState.expandOpen) renderMemoryShelfPanel();
  }

  function openMemoryShelf(): void {
    proposalsState.expandOpen = false;
    renderProposalsPanel();
    sessionsOpen = false;
    memoryShelfState.expandOpen = true;
    renderMemoryShelfPanel();
    client.listMemories();
  }

  function renderMemoryShelfPanel(): void {
    memoryShelfState.pending = topbarState.proposals;
    renderMemoryShelf(expandEl, memoryShelfState, client, openProposals);
    expandEl.addEventListener("memory:close", () => {
      memoryShelfState.expandOpen = false;
      renderMemoryShelfPanel();
      refreshTopbar();
    }, { once: true });
  }

  function handleMemoriesEvent(items: MemoryItem[]): void {
    memoryShelfState.memories = items;
    topbarState.memoryCount = items.length;
    refreshTopbar();
    if (memoryShelfState.expandOpen) renderMemoryShelfPanel();
  }

  // ---- project panel helpers ----
  const projectCallbacks: ProjectPanelCallbacks = {
    onProjectSwitch: (projectId: string) => {
      const target = projectId.trim();
      if (!target || (target === projectState.projectId && !projectState.switchOverlay)) return;
      if (chat.isWorking()) {
        setStatus("助手执行中，请稍后再切换项目");
        return;
      }
      const previousProjectId = projectState.projectId.trim();
      selectRailTab("now");
      beginAtomicProjectSwitch(projectState, target);
      clearChatForSwitch();
      pendingSwitchBatch = true;
      setMainFocus("chat");
      syncSessionSwitchMask();
      refreshTopbar();
      renderSidebar();
      const crossProject = Boolean(previousProjectId && previousProjectId !== target);
      try {
        client.switchProject(target, { confirm: !crossProject });
      } catch (err) {
        projectState.switchInProgress = false;
        projectState.pendingPickerId = "";
        pendingSwitchBatch = false;
        syncSessionSwitchMask();
        setStatus(`切换失败：${err instanceof Error ? err.message : String(err)}`);
        renderSidebar();
        refreshTopbar();
      }
    },
    onProjectSwitchConfirm: () => {
      if (!projectState.switchOverlay) return;
      const overlay = projectState.switchOverlay;
      const target = overlay.projectId;
      const requestId = overlay.requestId;
      beginAtomicProjectSwitch(projectState, target);
      clearChatForSwitch();
      pendingSwitchBatch = true;
      setMainFocus("chat");
      syncSessionSwitchMask();
      refreshTopbar();
      renderSidebar();
      try {
        client.switchProject(target, {
          confirm: true,
          requestId,
        });
      } catch (err) {
        projectState.switchInProgress = false;
        pendingSwitchBatch = false;
        syncSessionSwitchMask();
        setStatus(`切换失败：${err instanceof Error ? err.message : String(err)}`);
        renderSidebar();
        refreshTopbar();
      }
    },
    onProjectSwitchCancel: () => {
      projectState.switchOverlay = null;
      projectState.switchInProgress = false;
      projectState.pendingPickerId = "";
      pendingSwitchBatch = false;
      hydrationSessionId = "";
      syncSessionSwitchMask();
      renderSidebar();
      refreshTopbar();
    },
    onRefreshProjects: () => {
      try {
        client.listProjects();
        setStatus("刷新项目列表…");
      } catch (err) {
        setStatus(`刷新失败：${err instanceof Error ? err.message : String(err)}`);
      }
    },
    onNewThread: () => {
      handleNewThread();
    },
    onOpenThread: async (sessionId: string) => {
      const sid = sessionId.trim();
      if (!sid || sid === projectState.currentSessionId) return;
      if (chat.isWorking()) {
        setStatus("助手执行中，请稍后再切换会话线");
        return;
      }
      try {
        await openSessionThenBeginSwitch(
          () => client.openSession(sid),
          () => beginBoundSessionSwitch(sid, projectState.projectId, "打开会话线"),
        );
        projectState.railTab = "now";
        projectState.overlayPanel = null;
        setMainFocus("chat");
      } catch (err) {
        completeHydrationWait();
        setStatus(`打开会话线失败：${err instanceof Error ? err.message : String(err)}`);
      }
    },
    onReturnActiveThread: () => {
      const active = projectState.activeSessionId.trim();
      if (!active) return;
      projectCallbacks.onOpenThread(active);
    },
    onOpenProjects: () => {
      selectRailTab("projects");
    },
    onNewProject: () => {
      handleNewProject();
    },
    onScopeConfirm: () => {
      try {
        setStatus("正在确认范围…");
        client.confirmProjectScope();
      } catch (err) {
        setStatus(`确认范围失败：${err instanceof Error ? err.message : String(err)}`);
      }
    },
    onDesignConfirm: () => {
      try {
        setStatus("正在确认设计…");
        client.confirmProjectDesign();
      } catch (err) {
        setStatus(`确认设计失败：${err instanceof Error ? err.message : String(err)}`);
      }
    },
    onStopTurn: () => {
      sendStopRequest();
    },
    onMilestoneAccept: () => {
      try {
        client.acceptProjectRelease();
        setStatus("正在保存发布确认…");
      } catch (err) {
        setStatus(`发布确认失败：${err instanceof Error ? err.message : String(err)}`);
      }
    },
    onExternalImportScan: (path: string, projectId: string) => {
      try {
        client.scanExternalProject(path, projectId || undefined);
        setStatus("正在扫描外部项目…");
      } catch (err) {
        setStatus(`扫描外部项目失败：${err instanceof Error ? err.message : String(err)}`);
      }
    },
    onExternalImportApply: (path: string, projectId: string) => {
      try {
        client.applyExternalProject(path, projectId, true);
        setStatus("正在接入外部项目…");
      } catch (err) {
        setStatus(`接入外部项目失败：${err instanceof Error ? err.message : String(err)}`);
      }
    },
    onExternalImportCancel: () => {
      projectState.externalImportScan = null;
      renderSidebar();
    },
  };

  threadReturnActiveBtn.addEventListener("click", () => {
    projectCallbacks.onReturnActiveThread();
  });

  let pendingDocAction: "rename" | "delete" | "write" | "create" | null = null;
  let docLeaveNext: (() => void) | null = null;
  let docLeavePrompt = false;
  let destroyDocMenu: (() => void) | null = null;

  function isDocDirty(): boolean {
    return projectState.docView === "edit" && projectState.docDirty;
  }

  function resetDocEditState(nextPath = "", content = ""): void {
    projectState.currentDocPath = nextPath;
    projectState.currentDocContent = content;
    projectState.docView = "preview";
    projectState.docEditDraft = content;
    projectState.docDirty = false;
    projectState.docSaving = false;
    docLeaveNext = null;
    docLeavePrompt = false;
  }

  function captureDocDraftFromEditor(): void {
    const editor = documentEl.querySelector<HTMLTextAreaElement>(".unified-document-editor");
    if (editor) projectState.docEditDraft = editor.value;
    projectState.docDirty = projectState.docEditDraft !== projectState.currentDocContent;
  }

  function syncDocEditChrome(): void {
    const saveBtn = documentEl.querySelector<HTMLButtonElement>("[data-action='document-save']");
    if (saveBtn) {
      saveBtn.disabled = !projectState.docDirty || projectState.docSaving;
      saveBtn.textContent = projectState.docSaving ? "保存中…" : "保存";
    }
    const discardBtn = documentEl.querySelector<HTMLButtonElement>("[data-action='document-discard']");
    if (discardBtn) discardBtn.disabled = !projectState.docDirty || projectState.docSaving;
    documentEl.querySelector(".unified-document-inner")?.classList.toggle("is-dirty", projectState.docDirty);
    const kicker = documentEl.querySelector(".unified-document-kicker");
    if (kicker) kicker.textContent = projectState.docDirty ? "项目文档 · 未保存" : "项目文档";
  }

  function requestLeaveDocument(next: () => void): boolean {
    if (!isDocDirty()) return true;
    captureDocDraftFromEditor();
    docLeaveNext = next;
    docLeavePrompt = true;
    if (projectState.mainFocus !== "document") {
      projectState.mainFocus = "document";
      projectState.railTab = "docs";
      shellEl.dataset.rail = "docs";
      syncMainFocusView();
    } else {
      renderDocumentPane();
    }
    setStatus("有未保存的更改");
    return false;
  }

  function clearDocLeavePrompt(): void {
    docLeavePrompt = false;
    docLeaveNext = null;
  }

  function finishDocLeave(): void {
    const next = docLeaveNext;
    clearDocLeavePrompt();
    projectState.docDirty = false;
    projectState.docSaving = false;
    next?.();
  }

  function saveCurrentDoc(after?: () => void, force = false): void {
    const path = projectState.currentDocPath;
    if (!path) return;
    captureDocDraftFromEditor();
    pendingDocAction = "write";
    projectState.docSaving = true;
    projectState.docConflictPrompt = false;
    if (after) docLeaveNext = after;
    syncDocEditChrome();
    try {
      client.writeDoc(path, projectState.docEditDraft, {
        baseMtime: projectState.currentDocMtime || undefined,
        baseHash: projectState.currentDocHash || undefined,
        force,
      });
      setStatus("正在保存文档…");
    } catch (err) {
      projectState.docSaving = false;
      pendingDocAction = null;
      syncDocEditChrome();
      setStatus(`保存失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  function refreshDocsSidebar(): void {
    if (projectState.mainFocus === "document") {
      if (projectState.docView === "edit") captureDocDraftFromEditor();
      renderDocumentPane();
    }
    renderSidebar();
  }

  function startRenameDoc(path: string): void {
    projectState.deleteConfirmPath = "";
    projectState.renamingDocPath = path;
    projectState.renameDraft = renameDraftForDoc(path);
    refreshDocsSidebar();
    const scope = projectState.mainFocus === "document" ? documentEl : projectEls.overlayBody;
    const input = scope.querySelector<HTMLInputElement>("#overlay-doc-rename-input");
    input?.focus();
    input?.select();
  }

  function cancelRenameDoc(): void {
    projectState.renamingDocPath = "";
    projectState.renameDraft = "";
    refreshDocsSidebar();
  }

  function commitRenameDoc(): void {
    const path = projectState.renamingDocPath;
    const title = projectState.renameDraft.trim();
    if (!path) return;
    if (!title || title === docStem(path)) {
      cancelRenameDoc();
      return;
    }
    pendingDocAction = "rename";
    try {
      client.renameDoc(path, { title });
      setStatus("正在重命名…");
    } catch (err) {
      pendingDocAction = null;
      setStatus(`重命名失败：${err instanceof Error ? err.message : String(err)}`);
    }
    projectState.renamingDocPath = "";
    projectState.renameDraft = "";
    refreshDocsSidebar();
  }

  function askDeleteDoc(path: string): void {
    projectState.renamingDocPath = "";
    projectState.renameDraft = "";
    projectState.deleteConfirmPath = path;
    refreshDocsSidebar();
  }

  function confirmDeleteDoc(): void {
    const path = projectState.deleteConfirmPath;
    if (!path || pendingDocAction === "delete") return;
    pendingDocAction = "delete";
    try {
      client.deleteDoc(path);
      setStatus("正在删除文档…");
    } catch (err) {
      pendingDocAction = null;
      setStatus(`删除失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  function showDocCatalogMenu(path: string, x: number, y: number): void {
    destroyDocMenu?.();
    destroyDocMenu = mountPopupMenu({
      x,
      y,
      items: docCatalogMenuItems(),
      onSelect: (id) => {
        destroyDocMenu = null;
        if (id === "rename") startRenameDoc(path);
        else if (id === "delete") askDeleteDoc(path);
      },
    });
  }

  function setMainFocus(focus: MainFocus): void {
    if (focus === "projects") {
      setStatus("单对话页面不提供项目列表；请在对话中说明需要处理的工作。");
      return;
    }
    if (
      projectState.mainFocus === "document" &&
      focus !== "document" &&
      !requestLeaveDocument(() => setMainFocus(focus))
    ) {
      return;
    }
    if (focus !== "document") {
      projectState.docView = "preview";
      projectState.docDirty = false;
      projectState.docSaving = false;
      docLeavePrompt = false;
    }
    projectState.mainFocus = focus;
    if (focus !== "chat") {
      clearExpandedSurface();
    }
    syncMainFocusView();
  }

  function selectSingleChatTool(tool: "now" | "docs" | "settings" | "music" | "shell-dev" | "runtime"): void {
    if (tool === "runtime") {
      showNowChat();
      if (projectState.mainFocus !== "chat") return;
      runtimeToolOpen = true;
      syncMainFocusView();
      refreshTerminals();
      refreshServices();
      return;
    }
    runtimeToolOpen = false;
    selectRailTab(tool);
  }

  function selectRailTab(tab: RailTab): void {
    if (tab === "projects") {
      setStatus("单对话页面不提供项目列表；请在对话中说明需要处理的工作。");
      return;
    }
    if (
      projectState.railTab === "docs" &&
      tab !== "docs" &&
      !requestLeaveDocument(() => selectRailTab(tab))
    ) {
      return;
    }
    projectState.railTab = tab;
    projectState.overlayPanel = null;
    shellEl.dataset.rail = tab;
    projectState.switchConfirmTarget = null;
    if (tab === "docs") {
      openDocumentReader();
      return;
    }
    if (tab === "now") {
      refreshSidebarSessions();
    }
    setMainFocus(mainFocusForRail(tab));
  }

  function showNowChat(): void {
    if (
      projectState.mainFocus === "document" &&
      !requestLeaveDocument(() => showNowChat())
    ) {
      return;
    }
    if (projectState.mainFocus === "document") {
      projectState.renamingDocPath = "";
      projectState.renameDraft = "";
      projectState.deleteConfirmPath = "";
    }
    projectState.railTab = "now";
    projectState.overlayPanel = null;
    shellEl.dataset.rail = "now";
    setMainFocus("chat");
  }

  function syncMainFocusView(): void {
    const focus = projectState.mainFocus;
    const pageToolOpen =
      focus === "document" ||
      focus === "projects" ||
      focus === "settings" ||
      focus === "music" ||
      focus === "shell-dev" ||
      focus === "current-work";
    const chatFocus = focus === "chat" && !runtimeToolOpen;
    const toolOpen = pageToolOpen || runtimeToolOpen;
    toolRegion.hidden = !toolOpen;
    runtimeTool.hidden = !runtimeToolOpen;
    toolTitle.textContent = runtimeToolOpen ? "终端与服务" : ({ document: "文档", projects: "项目", settings: "设置", music: "听歌", "shell-dev": "双车道", "current-work": "当前工作", chat: "工具" } as const)[focus];
    toolCloseBtn.textContent = pageToolOpen ? "回到对话" : "关闭";
    toolCloseBtn.title = pageToolOpen ? "离开这一页，回到对话" : "关闭";
    // Full-page tools own the stage (完整页面). Chat stays mounted but is not left-split.
    shellEl.dataset.mainFocus = focus;
    shellEl.dataset.rail = projectState.railTab;
    shellEl.classList.toggle("is-tool-fullpage", pageToolOpen);
    shellEl.classList.remove("is-docs-split");
    chatEl.classList.toggle("hidden", pageToolOpen);
    documentEl.classList.toggle("hidden", focus !== "document");
    projectsEl.classList.toggle("hidden", focus !== "projects");
    chatEl.hidden = pageToolOpen;
    documentEl.hidden = focus !== "document";
    projectsEl.hidden = focus !== "projects";
    settingsEl.hidden = focus !== "settings";
    settingsEl.classList.toggle("hidden", focus !== "settings");
    musicEl.hidden = focus !== "music";
    musicEl.classList.toggle("hidden", focus !== "music");
    shellDevEl.hidden = focus !== "shell-dev";
    shellDevEl.classList.toggle("hidden", focus !== "shell-dev");
    currentWorkEl.hidden = focus !== "current-work";
    currentWorkEl.classList.toggle("hidden", focus !== "current-work");
    if (focus === "current-work") currentWorkOverviewOpen = true;
    else if (focus !== "current-work") currentWorkOverviewOpen = false;
    composerDock.hidden = pageToolOpen;
    composer.hidden = pageToolOpen;
    statusEl.hidden = pageToolOpen;
    tokenBar.hidden = !chatFocus;
    syncComposerMetaVisibility();
    if (focus === "document") {
      renderDocumentPane();
    } else if (focus === "projects") {
      renderProjectsPane();
    } else if (focus === "music") {
      renderMusicStage();
    } else if (focus === "shell-dev") {
      shellDevLastOpenOk = false;
      void renderShellDevStage();
    } else if (focus === "current-work") {
      renderCurrentWorkStage();
    } else if (focus === "settings") {
      renderSettingsStage();
      if (projectState.settingsSection === "models") refreshGlobalRoleModels();
    }
    syncMusicViewBounds();
    syncShellDevViewBounds();
    updateWorkbenchEmpty();
    renderSidebar();
  }
  function syncSettingsTheme(): void {
    const theme = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
    settingsEl.querySelectorAll<HTMLInputElement>("[data-settings-theme]").forEach(radio => {
      radio.checked = radio.dataset.settingsTheme === theme;
    });
  }

  function settingsRuntimeCounts(): { terminals: number; services: number } {
    return {
      terminals: projectState.terminalSessions.filter(item => item.state !== "starting" && item.state !== "running").length,
      services: projectState.services.filter(item => !item.alive).length,
    };
  }

  function settingsNotice(text: string): void {
    settingsFeedback = text;
    const feedback = settingsEl.querySelector<HTMLElement>(".settings-feedback");
    if (feedback) feedback.textContent = text;
  }

  function renderSettingsStage(): void {
    const section = projectState.settingsSection || "appearance";
    const name = currentPartnerName();
    const counts = settingsRuntimeCounts();
    // Async sidebar/runtime events must not replace inputs or interrupt a drag.
    const modelStateKey = section === "models" ? JSON.stringify([globalRoleModels, autoRecognition.enabled, !!autoRecognition.pending, autoRecognition.error, autoRecognition.sessionId === chat.model.sessionId, approvalPreferences]) : "";
    if (settingsRenderedSection !== section || settingsRenderedName !== name || (section === "models" && settingsRenderedModelState !== modelStateKey)) {
      settingsEl.innerHTML = renderSettingsSidebar(section) + renderSettingsPane({
        section, partnerName: name, nicknameDraft: settingsNicknameDraft ?? name,
        avatarSrc: petAvatarSrc("idle", true),
        daysTogether: Math.max(1, Math.ceil((Date.now() - loadCompanionBond().firstMetAt) / 86_400_000)),
        theme: document.documentElement.dataset.theme === "dark" ? "dark" : "light",
        inactiveTerminals: counts.terminals, stoppedServices: counts.services,
        fontControlHtml: renderChatFontControl(), feedback: settingsFeedback,
        roleModels: globalRoleModels,
        autoRecognition: { enabled: autoRecognition.enabled, pending: !!autoRecognition.pending, available: !!autoRecognition.sessionId && autoRecognition.sessionId === chat.model.sessionId, error: autoRecognition.error },
        approvalPolicy: approvalPreferences,
      });
      if (settingsRenderedSection !== section) settingsEl.scrollTop = 0;
      settingsRenderedSection = section;
      settingsRenderedName = name;
      settingsRenderedModelState = modelStateKey;
      syncChatFontControl(settingsEl);
    }
    if (section === "runtime") {
      const terminals = settingsEl.querySelector<HTMLElement>("[data-settings-terminal-count]");
      const services = settingsEl.querySelector<HTMLElement>("[data-settings-service-count]");
      if (terminals) terminals.textContent = counts.terminals ? counts.terminals + " 个可清理" : "暂无可清理记录";
      if (services) services.textContent = counts.services ? counts.services + " 个可清理" : "暂无可清理记录";
      const prune = settingsEl.querySelector<HTMLButtonElement>('[data-action="settings-prune-runtime"]');
      if (prune) {
        prune.disabled = counts.terminals + counts.services === 0;
        prune.textContent = prune.disabled ? "无需清理" : "清理记录";
      }
    }
    syncSettingsTheme();
  }

  function selectSettingsSection(section: SettingsSection): void {
    projectState.settingsSection = section;
    settingsFeedback = "";
    settingsRenderedSection = null;
    renderSidebar();
    if (section === "models") {
      refreshGlobalRoleModels();
      refreshApprovalPreferences();
    }
    renderSettingsStage();
    settingsNotice("");
  }

  shellEl.addEventListener("click", event => {
    if (!(event.target instanceof Element)) return;
    const category = event.target.closest<HTMLButtonElement>("[data-settings-section]");
    if (category && isSettingsSection(category.dataset.settingsSection)) {
      selectSettingsSection(category.dataset.settingsSection);
      return;
    }
    const button = event.target.closest<HTMLButtonElement>('[data-action^="settings-"]');
    if (!button || button.disabled) return;
    switch (button.dataset.action) {
      case "settings-back-chat": showNowChat(); break;
      case "settings-open-model-keys": document.querySelector<HTMLButtonElement>("#chrome-model-keys")?.click(); break;
      case "settings-open-host-scope": document.querySelector<HTMLButtonElement>("#chrome-settings")?.click(); break;
      case "settings-open-runtime-status": selectSingleChatTool("runtime"); break;
      case "settings-auto-recognition": setAutoRecognition(autoRecognition.enabled ? "revoke" : "enable"); break;
      case "settings-approval-reset": setApprovalPreference("manual"); break;
      case "settings-approval-apply-saved": setApprovalPreference(approvalPreferences.savedMode); break;
      case "settings-switch-cli": document.querySelector<HTMLButtonElement>("#chrome-cli")?.click(); break;
      case "settings-prune-runtime":
        try {
          client.pruneTerminals(true, `terminal-prune-${Date.now()}`);
          client.pruneServices();
          settingsNotice("已请求清理，记录将在运行环境刷新后更新。");
        } catch (error) { settingsNotice(error instanceof Error ? error.message : String(error)); }
        break;
    }
  });
  settingsEl.addEventListener("input", event => {
    const target = event.target;
    if (target instanceof HTMLInputElement && target.id === "settings-partner-name-input") settingsNicknameDraft = target.value;
  });
  settingsEl.addEventListener("change", event => {
    const target = event.target;
    if (target instanceof HTMLSelectElement && target.matches(".settings-category-mobile") && isSettingsSection(target.value)) {
      selectSettingsSection(target.value);
    } else if (target instanceof HTMLSelectElement && target.matches(".settings-role-model-select")) {
      const role = target.dataset.role;
      if ((role === "main" || role === "subagent") && target.value) setGlobalRoleModel(role, target.value);
    } else if (target instanceof HTMLInputElement && target.name === "settings-approval-mode") {
      const mode = target.value;
      if (mode === "manual" || mode === "auto" || mode === "full") setApprovalPreference(mode);
    } else if (target instanceof HTMLInputElement && target.dataset.settingsTheme) {
      const theme = target.dataset.settingsTheme === "dark" ? "dark" : "light";
      try {
        const chromeTheme = document.querySelector<HTMLSelectElement>("#chrome-theme");
        if (chromeTheme) { chromeTheme.value = theme; chromeTheme.dispatchEvent(new Event("change")); }
        else writeTheme(theme);
        syncSettingsTheme();
      } catch (error) { settingsNotice(error instanceof Error ? error.message : String(error)); syncSettingsTheme(); }
    }
  });
  settingsEl.addEventListener("submit", event => {
    if (!(event.target instanceof HTMLFormElement) || !event.target.matches("[data-settings-name-form]")) return;
    event.preventDefault();
    const nextName = (settingsNicknameDraft ?? currentPartnerName()).trim() || "小魔王";
    try { localStorage.setItem("partner_name", nextName); }
    catch { settingsNotice("昵称未能保存，请检查本机存储是否可用。"); return; }
    settingsNicknameDraft = nextName;
    syncCompanionToBackend();
    const title = document.querySelector<HTMLElement>(".app-chrome-title");
    if (title) title.textContent = `✦ ${nextName}的空间`;
    renderedPrints = [];
    renderedSegmentPrints = [];
    renderChat();
    renderSidebar();
    settingsNotice(`昵称已保存：${nextName}`);
    settingsEl.querySelector<HTMLInputElement>("#settings-partner-name-input")?.focus();
  });


  function currentMusicViewBounds(): { x: number; y: number; width: number; height: number } | null {
    if (projectState.mainFocus !== "music" || !hasBilibiliSearch) return null;
    const slot = musicEl.querySelector<HTMLElement>("[data-music-web-slot]");
    if (!slot || slot.hidden) return null;
    const rect = slot.getBoundingClientRect();
    const clip = musicEl.getBoundingClientRect();
    const left = Math.max(0, clip.left, rect.left);
    const top = Math.max(0, clip.top, rect.top);
    const right = Math.min(window.innerWidth, clip.right, rect.right);
    const bottom = Math.min(window.innerHeight, clip.bottom, rect.bottom);
    if (right <= left || bottom <= top) return null;
    return { x: left, y: top, width: right - left, height: bottom - top };
  }

  function syncMusicViewBounds(): void {
    const api = window.myAgentDesktop?.setBilibiliViewState;
    if (!api) return;
    const bounds = currentMusicViewBounds();
    void api({ visible: Boolean(bounds), bounds: bounds ?? undefined }).catch(() => {});
  }

  function renderMusicStage(): void {
    if (musicEl.innerHTML) return;
    musicEl.innerHTML = renderMusicPanel();
  }

  function currentShellDevViewBounds(): { x: number; y: number; width: number; height: number } | null {
    if (projectState.mainFocus !== "shell-dev") return null;
    const slot = shellDevEl.querySelector<HTMLElement>("[data-shell-dev-web-slot]");
    const preview = slot?.closest<HTMLElement>(".dual-lane-preview");
    if (!slot || !preview) return null;
    const clip = shellDevEl.getBoundingClientRect();
    return shellDevPreviewBounds({
      slot: slot.getBoundingClientRect(),
      preview: preview.getBoundingClientRect(),
      viewport: {
        left: Math.max(0, clip.left),
        top: Math.max(0, clip.top),
        right: Math.min(window.innerWidth, clip.right),
        bottom: Math.min(window.innerHeight, clip.bottom),
      },
    });
  }

  let currentEvolutionView: EvolutionView = evolutionView(null);
  let evolutionHostAvailable: boolean | null = null;
  let evolutionBeginPending = false;
  function syncShellDevEvolutionView(view = currentEvolutionView): void {
    currentEvolutionView = view;
    const label = shellDevEl.querySelector<HTMLElement>("[data-evolution-status]");
    const checks = shellDevEl.querySelector<HTMLElement>("[data-evolution-checks]");
    if (label) label.textContent = evolutionHostAvailable === false ? "测试 Host 未启用" : view.label;
    if (checks) checks.textContent = view.openChecks.join(" · ");
    const hasSession = Boolean(chat.model.sessionId);
    const begin = shellDevEl.querySelector<HTMLButtonElement>("[data-evolution-begin]");
    const inspect = shellDevEl.querySelector<HTMLButtonElement>("[data-evolution-inspect]");
    const stop = shellDevEl.querySelector<HTMLButtonElement>("[data-evolution-stop]");
    if (begin) begin.disabled = !hasSession || view.active || evolutionHostAvailable === false || evolutionBeginPending;
    if (inspect) inspect.disabled = !hasSession || evolutionHostAvailable === false;
    if (stop) stop.disabled = !hasSession || !view.canStop || evolutionHostAvailable === false;
  }

  async function requestEvolutionAction(action: "begin" | "inspect" | "stop"): Promise<void> {
    if (!chat.model.sessionId) {
      shellDevFeedback("请先在当前会话完成一轮，再操作演化。", "error");
      return;
    }
    const api = window.myAgentDesktop;
    if (action === "begin") { evolutionBeginPending = true; syncShellDevEvolutionView(); }
    const request = action === "begin" ? api?.beginEvolution : action === "stop" ? api?.stopEvolution : api?.getEvolutionState;
    if (!request) {
      if (action === "begin") evolutionBeginPending = false;
      evolutionHostAvailable = false;
      syncShellDevEvolutionView();
      shellDevFeedback("当前桌面未连接固定测试 Host。", "error");
      return;
    }
    try {
      const result = await request();
      if (result.status === "disabled") {
        if (action === "begin") evolutionBeginPending = false;
        evolutionHostAvailable = false;
        syncShellDevEvolutionView();
        shellDevFeedback(result.message || "固定测试 Host 未启用。", "error");
      } else {
        evolutionHostAvailable = true;
        syncShellDevEvolutionView();
        shellDevFeedback(action === "inspect" ? "正在读取当前会话状态…" : action === "begin" ? "已在当前会话发起授权；请切回聊天检查确认卡。" : "停止请求已发送。", "busy");
      }
    } catch (error) {
      if (action === "begin") evolutionBeginPending = false;
      syncShellDevEvolutionView();
      shellDevFeedback("演化请求失败：" + (error instanceof Error ? error.message : String(error)), "error");
    }
  }

  function shellDevFeedback(text: string, tone: "neutral" | "ok" | "error" | "busy" = "neutral"): void {
    const el = shellDevEl.querySelector<HTMLElement>("[data-shell-dev-feedback]");
    if (!el) return;
    el.textContent = text;
    el.dataset.tone = tone;
    el.classList.toggle("is-ok", tone === "ok");
    el.classList.toggle("is-error", tone === "error");
    el.classList.toggle("is-busy", tone === "busy");
  }

  function clearShellDevSlotPlaceholder(): void {
    shellDevEl.querySelector<HTMLElement>(".shell-dev-slot-placeholder")?.remove();
  }

  function showShellDevSlotPlaceholder(previewUrl: string, mainUrl: string): void {
    const slot = shellDevEl.querySelector<HTMLElement>("[data-shell-dev-web-slot]");
    if (!slot) return;
    clearShellDevSlotPlaceholder();
    const panel = document.createElement("div");
    panel.className = "shell-dev-slot-placeholder";
    panel.innerHTML = `<p>原生预览未嵌入（页内 iframe 无法加载完整桌面壳）。</p>
      <p class="shell-dev-slot-hint">请用 <code>start-desktop.bat</code> 启动（含 5174 预览轨）；若只要主壳可设 <code>MY_AGENT_SKIP_PREVIEW_DEV=1</code>。</p>
      <div class="shell-dev-slot-actions">
        <button type="button" class="unified-btn" data-shell-dev-open-preview>在浏览器打开预览轨</button>
        <button type="button" class="unified-btn" data-shell-dev-open-main>在浏览器打开主壳</button>
      </div>`;
    panel.querySelector<HTMLButtonElement>("[data-shell-dev-open-preview]")?.addEventListener("click", () => {
      void window.myAgentDesktop?.openExternal?.(previewUrl);
    });
    panel.querySelector<HTMLButtonElement>("[data-shell-dev-open-main]")?.addEventListener("click", () => {
      void window.myAgentDesktop?.openExternal?.(mainUrl);
    });
    slot.appendChild(panel);
  }

  let shellDevOpenTimer: number | null = null;
  let shellDevLastOpenOk = false;
  let evolutionRelayErrorCleanup: (() => void) | null = null;

  async function openShellDevPreviewIfReady(): Promise<void> {
    const api = window.myAgentDesktop;
    const fallbackMain = (await api?.getShellDevMainUrl?.()) ?? "http://127.0.0.1:5173/";
    const fallbackPreview = (await api?.getShellDevPreviewUrl?.()) ?? "http://127.0.0.1:5174/";
    if (!api?.openShellDevPreview) {
      showShellDevSlotPlaceholder(fallbackPreview, fallbackMain);
      shellDevFeedback("请完全退出后重启桌面壳，以启用壳预览原生嵌入。");
      return;
    }
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    const bounds = currentShellDevViewBounds();
    if (!bounds || bounds.width < 80 || bounds.height < 80) {
      shellDevFeedback("预览区域尚未就绪，请稍后再试或点「重新加载预览」。");
      return;
    }
    try {
      const result = await api.openShellDevPreview(bounds);
      shellDevLastOpenOk = Boolean(result?.ok);
      if (result?.ok) {
        clearShellDevSlotPlaceholder();
        shellDevFeedback(
          result.message
            || (result.fallbackToMain
              ? "已显示主壳开发页（单轨）。请用 start-desktop.bat 重启以拉起 5174 预览轨。"
              : "内嵌缩略预览已就绪；要看全尺寸请点「独立窗口预览」。"),
        );
        return;
      }
      showShellDevSlotPlaceholder(fallbackPreview, fallbackMain);
      shellDevFeedback(
        result?.message
          || "原生嵌入失败。请点「重新加载预览」，或先用下方按钮在浏览器里打开开发页。",
      );
    } catch {
      showShellDevSlotPlaceholder(fallbackPreview, fallbackMain);
      shellDevFeedback("预览暂时无法打开。请确认 npm run dev 正在运行后点「重新加载预览」。");
    }
  }

  function queueShellDevPreviewOpen(): void {
    if (projectState.mainFocus !== "shell-dev") return;
    if (shellDevOpenTimer !== null) window.clearTimeout(shellDevOpenTimer);
    shellDevOpenTimer = window.setTimeout(() => {
      shellDevOpenTimer = null;
      if (!shellDevLastOpenOk) void openShellDevPreviewIfReady();
    }, 120);
  }

  function syncShellDevViewBounds(): void {
    const api = window.myAgentDesktop?.setShellDevViewState;
    if (!api) return;
    const bounds = currentShellDevViewBounds();
    void api({ visible: Boolean(bounds), bounds: bounds ?? undefined }).catch(() => {});
    if (bounds && bounds.width >= 80 && bounds.height >= 80) {
      queueShellDevPreviewOpen();
    }
  }

  function applyShellDevDualLaneStatus(status: Record<string, unknown> | undefined): void {
    const strip = formatDualLaneStrip(status);
    const setText = (sel: string, value: string) => {
      const el = shellDevEl.querySelector<HTMLElement>(sel);
      if (el) el.textContent = value;
    };
    setText("[data-shell-dev-stable-head]", strip.stableHead);
    setText("[data-shell-dev-sandbox-head]", strip.sandboxHead);
    setText("[data-shell-dev-stable-branch]", strip.stableBranch);
    setText("[data-shell-dev-sandbox-branch]", strip.sandboxBranch);
    setText("[data-shell-dev-commits-ahead]", strip.commitsAhead);
    setText("[data-shell-dev-stable-ahead]", strip.stableAhead);
    setText("[data-shell-dev-sandbox-dirty]", strip.sandboxDirty);
    setText("[data-shell-dev-stable-dirty]", strip.stableDirty);
    const setStatus = (sel: string, value: string, tone: string) => {
      const el = shellDevEl.querySelector<HTMLElement>(sel);
      if (!el) return;
      el.textContent = value;
      if (tone) el.dataset.tone = tone;
      else delete el.dataset.tone;
    };
    setStatus("[data-shell-dev-stable-status]", strip.stableStatus, strip.stableTone);
    setStatus("[data-shell-dev-sandbox-status]", strip.sandboxStatus, strip.sandboxTone);
    for (const sel of ["[data-shell-dev-stable-details]", "[data-shell-dev-sandbox-details]"]) {
      const details = shellDevEl.querySelector<HTMLDetailsElement>(sel);
      if (details) details.open = strip.openDetails;
    }
    const hintEl = shellDevEl.querySelector<HTMLElement>("[data-shell-dev-sync-hint]");
    if (hintEl) {
      hintEl.textContent = strip.hint;
      hintEl.classList.remove("hidden");
    }
    const blockersEl = shellDevEl.querySelector<HTMLElement>("[data-shell-dev-dual-blockers]");
    if (blockersEl) {
      blockersEl.textContent = strip.blockers;
      blockersEl.hidden = !strip.showBlockers;
    }
  }

  async function refreshShellDevSyncHint(): Promise<void> {
    const api = window.myAgentDesktop;
    let legacySessionLanes = false;
    try {
      legacySessionLanes = (await api?.getSessionLaneCapabilities?.())?.legacySessionLanes === true;
    } catch {
      // Missing, rejected, or throwing capability checks fail closed.
    }
    if (!legacySessionLanes) {
      const hintEl = shellDevEl.querySelector<HTMLElement>("[data-shell-dev-sync-hint]");
      if (hintEl) {
        hintEl.textContent = "";
        hintEl.classList.add("hidden");
      }
      return;
    }
    const status = await api?.getSandboxAdoptStatus?.().catch(() => undefined);
    applyShellDevDualLaneStatus(status as Record<string, unknown> | undefined);
  }

  async function refreshShellDevLaneLabels(): Promise<void> {
    const api = window.myAgentDesktop;
    const mainUrl = (await api?.getShellDevMainUrl?.()) ?? "http://127.0.0.1:5173/";
    const previewUrl = (await api?.getShellDevPreviewUrl?.()) ?? "http://127.0.0.1:5174/";
    const mainEl = shellDevEl.querySelector<HTMLElement>("[data-shell-dev-main-url]");
    const previewEl = shellDevEl.querySelector<HTMLElement>("[data-shell-dev-url]");
    if (mainEl) mainEl.textContent = mainUrl;
    if (previewEl) previewEl.textContent = previewUrl;
    await refreshShellDevSyncHint();
  }

  async function renderShellDevStage(): Promise<void> {
    if (!shellDevEl.innerHTML) {
      const api = window.myAgentDesktop;
      const mainUrl = (await api?.getShellDevMainUrl?.()) ?? "http://127.0.0.1:5173/";
      const previewUrl = (await api?.getShellDevPreviewUrl?.()) ?? "http://127.0.0.1:5174/";
      shellDevEl.innerHTML = renderShellDevPanel(mainUrl, previewUrl);
      syncShellDevEvolutionView();
      if (!evolutionRelayErrorCleanup) {
        const onRelayError = (event: Event) => {
          const detail = (event as CustomEvent<unknown>).detail;
          const message = typeof detail === "string" ? detail : "sidecar WebSocket 未连接";
          shellDevFeedback("演化请求未送达：" + message, "error");
        };
        window.addEventListener("evolution:transport-error", onRelayError);
        evolutionRelayErrorCleanup = () => window.removeEventListener("evolution:transport-error", onRelayError);
      }
      shellDevEl.querySelector<HTMLButtonElement>("[data-evolution-begin]")?.addEventListener("click", () => { void requestEvolutionAction("begin"); });
      shellDevEl.querySelector<HTMLButtonElement>("[data-evolution-inspect]")?.addEventListener("click", () => { void requestEvolutionAction("inspect"); });
      shellDevEl.querySelector<HTMLButtonElement>("[data-evolution-stop]")?.addEventListener("click", () => { void requestEvolutionAction("stop"); });
      const slot = shellDevEl.querySelector<HTMLElement>("[data-shell-dev-web-slot]");
      if (slot) shellDevViewResizeObserver.observe(slot);
      shellDevEl.querySelector<HTMLButtonElement>("[data-shell-dev-back-chat]")?.addEventListener("click", () => {
        if (sandboxSessionActive) {
          void openLatestOrdinarySession();
          return;
        }
        showNowChat();
      });
      shellDevEl.querySelector<HTMLButtonElement>("[data-shell-dev-sandbox-session]")?.addEventListener("click", async () => {
        try {
          shellDevFeedback("正在打开实验会话…", "busy");
          await client.createSandboxSession();
          setPerspective("project", "session");
          shellDevFeedback("已打开实验会话，还是这条连接。直接说要改什么，助手写实验树，5174 能看到。", "ok");
        } catch (error) {
          shellDevFeedback(error instanceof Error ? error.message : String(error), "error");
        }
      });
      shellDevEl.querySelector<HTMLButtonElement>("[data-shell-dev-restart-sandbox]")?.addEventListener("click", async () => {
        const restart = window.myAgentDesktop?.restartSandboxSidecar;
        if (!restart) {
          shellDevFeedback("请完全退出后用 start-desktop.bat 重启，才能重载实验运行时。", "error");
          return;
        }
        shellDevFeedback("正在重载实验运行时…", "busy");
        try {
          const result = await restart();
          if (!result?.ok) {
            shellDevFeedback(result?.message || "实验运行时没有起来。", "error");
            return;
          }
          await client.reconnectActiveLane();
          shellDevFeedback("实验运行时已重载。回到实验会话继续；界面改动刷新 5174。", "ok");
        } catch (error) {
          shellDevFeedback(error instanceof Error ? error.message : String(error), "error");
        }
      });
      shellDevEl.querySelector<HTMLButtonElement>("[data-shell-dev-popout]")?.addEventListener("click", () => {
        void window.myAgentDesktop?.openShellDevPopout?.().then((result) => {
          if (result?.ok) {
            shellDevFeedback(result.message || `已在独立窗口打开：${result.url ?? ""}`);
            return;
          }
          shellDevFeedback(result?.message || "独立预览窗口未能打开。");
        });
      });
      shellDevEl.querySelector<HTMLButtonElement>("[data-shell-dev-reload]")?.addEventListener("click", () => {
        shellDevLastOpenOk = false;
        clearShellDevSlotPlaceholder();
        void openShellDevPreviewIfReady();
      });
      shellDevEl.querySelector<HTMLButtonElement>("[data-shell-dev-promote]")?.addEventListener("click", () => {
        const ok = window.confirm(
          "将把「预览壳」的地址设为主壳并重新加载主窗口；原主壳会移到预览轨。继续？",
        );
        if (!ok) return;
        void window.myAgentDesktop?.promoteShellDevPreview?.().then(async (result) => {
          if (result?.ok) {
            shellDevFeedback(
              `已提取为主壳：${result.mainUrl ?? ""}。原主壳在预览轨：${result.previewUrl ?? ""}`,
            );
            await refreshShellDevLaneLabels();
            await openShellDevPreviewIfReady();
            return;
          }
          shellDevFeedback(result?.message || "提取失败，请确认预览页已打开。");
        });
      });
      const managedReviewButton = shellDevEl.querySelector<HTMLButtonElement>("[data-shell-dev-managed-review]");
      const managedReviewOutput = shellDevEl.querySelector<HTMLElement>("[data-managed-review-output]");
      const managedReviewContainer = shellDevEl.querySelector<HTMLElement>("[data-managed-review-browser]");
      const managedSelectionContainer = shellDevEl.querySelector<HTMLElement>("[data-managed-source-selection]");
      if (managedReviewButton && managedReviewOutput && managedReviewContainer && managedSelectionContainer) {
        const managedReviewBrowser = mountManagedReviewBrowser(managedReviewContainer);
        const managedSourceSelection = mountManagedSourceSelection(managedSelectionContainer, {
          isDesktop: Boolean(window.myAgentDesktop?.inspectManagedSourceReview && window.myAgentDesktop?.proposeManagedSourceSelection),
          propose: (request) => window.myAgentDesktop?.proposeManagedSourceSelection?.(request)
            ?? Promise.resolve({ ok: false as const, message: "当前运行环境不支持范围核验。" }),
          createCheckpoint: window.myAgentDesktop?.createManagedSourceCheckpoint
            ? (request) => window.myAgentDesktop!.createManagedSourceCheckpoint!(request)
            : undefined,
          executeMerge: window.myAgentDesktop?.executeManagedSelectedMerge
            ? (request) => window.myAgentDesktop!.executeManagedSelectedMerge!(request)
            : undefined,
        });
        const managedReview = createManagedReviewController({
          inspect: () => window.myAgentDesktop?.inspectManagedSourceReview?.()
            ?? Promise.resolve({ ok: false as const, message: "当前运行环境不支持管理源码审查。" }),
          button: managedReviewButton,
          output: managedReviewOutput as unknown as { textContent: string | null; hidden: boolean },
          isDesktop: Boolean(window.myAgentDesktop?.inspectManagedSourceReview),
          onReset: () => { managedReviewBrowser.clear(); managedSourceSelection.clear(); managedReviewOutput.textContent = ""; managedReviewOutput.hidden = true; },
          onReport: (report) => { managedReviewBrowser.show(report); managedSourceSelection.show(report); },
        });
        managedReviewButton.addEventListener("click", () => { void managedReview.inspect(); });
      }
      const adoption = createShellDevAdoptionController(window.myAgentDesktop, shellDevFeedback, (busy) => {
        for (const sel of ["[data-shell-dev-adopt-merge]", "[data-shell-dev-one-click]", "[data-shell-dev-sync-stable]", "[data-shell-dev-refresh-status]"]) {
          shellDevEl.querySelectorAll<HTMLButtonElement>(sel).forEach((button) => { button.disabled = busy; });
        }
      }, (status) => { applyShellDevDualLaneStatus(status); });
      shellDevEl.querySelector<HTMLButtonElement>("[data-shell-dev-one-click]")?.addEventListener("click", () => { void adoption.oneClick(); });
      shellDevEl.querySelector<HTMLButtonElement>("[data-shell-dev-adopt-merge]")?.addEventListener("click", () => { void adoption.merge(); });
      shellDevEl.querySelector<HTMLButtonElement>("[data-shell-dev-adopt-status]")?.addEventListener("click", () => { void adoption.inspect(); });
      shellDevEl.querySelectorAll<HTMLButtonElement>("[data-shell-dev-sync-stable]").forEach((btn) => {
        btn.addEventListener("click", () => { void adoption.sync(); });
      });
      shellDevEl.querySelectorAll<HTMLButtonElement>("[data-shell-dev-refresh-status]").forEach((btn) => {
        btn.addEventListener("click", () => {
          void (async () => {
            shellDevFeedback("正在刷新双车道状态…", "busy");
            await adoption.refreshStatus();
            shellDevFeedback("双车道状态已刷新。", "ok");
          })();
        });
      });
      shellDevEl.querySelector<HTMLButtonElement>("[data-shell-dev-reset-main]")?.addEventListener("click", () => {
        void (async () => {
          try {
            const api = window.myAgentDesktop;
            const capabilities = await api?.getSessionLaneCapabilities?.().catch(() => undefined);
            const reset = capabilities?.legacySessionLanes === true && api?.resetShellDevMainLane
              ? api.resetShellDevMainLane : api?.resetShellDevMain;
            const result = await reset?.();
            shellDevFeedback(result?.message || (result?.ok ? "已返回稳定版。" : "返回稳定版失败。"), result?.ok ? "ok" : "error");
            if (result?.ok) {
              await refreshShellDevLaneLabels();
              await openShellDevPreviewIfReady();
            }
          } catch (error) { shellDevFeedback(String(error), "error"); }
        })();
      });
    }
    await refreshShellDevLaneLabels();
    await openShellDevPreviewIfReady();
  }

  musicEl.addEventListener("submit", async (event) => {
    if (!(event.target instanceof HTMLFormElement) || !event.target.matches("[data-music-search-form]")) return;
    event.preventDefault();
    const input = musicEl.querySelector<HTMLInputElement>("#music-search-query");
    const feedback = musicEl.querySelector<HTMLElement>(".music-feedback");
    const slot = musicEl.querySelector<HTMLElement>("[data-music-web-slot]");
    const query = input?.value.trim() || "";
    if (!query) {
      if (feedback) feedback.textContent = "先输入歌名或歌手。";
      input?.focus();
      return;
    }
    if (!window.myAgentDesktop?.openBilibiliSearch) {
      if (feedback) feedback.textContent = "请在桌面客户端中使用 B 站搜索。";
      return;
    }
    if (!slot) return;
    hasBilibiliSearch = true;
    slot.hidden = false;
    if (feedback) feedback.textContent = "正在当前听歌页打开 B 站搜索…";
    musicViewResizeObserver.observe(slot);
    await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
    const bounds = currentMusicViewBounds();
    if (!bounds) {
      if (feedback) feedback.textContent = "滚动到播放器位置后再试一次。";
      return;
    }
    try {
      const opened = await window.myAgentDesktop.openBilibiliSearch(query, bounds);
      if (feedback) feedback.textContent = opened ? "B 站搜索结果已显示在听歌页。" : "搜索页暂时无法打开，请稍后重试。";
    } catch {
      if (feedback) feedback.textContent = "搜索页暂时无法打开，请稍后重试。";
    }
  });

  function renderProjectsPane(): void {
    projectsEl.innerHTML = renderProjectsStage(projectState);
  }

  function renderDocumentPane(): void {
    if (!projectState.projectId) {
      documentEl.innerHTML = `<p class="unified-tool-unavailable" role="status">当前对话未绑定项目，文档暂不可用。</p>`;
      return;
    }
    const path = projectState.currentDocPath;
    const content = projectState.currentDocContent;
    const previewSource = projectState.docView === "edit"
      ? (projectState.docEditDraft || content)
      : content;
    const { html, outline } = renderDocumentReaderHtml({
      docs: projectState.projectDocs,
      currentPath: path,
      currentContent: content,
      renderedHtml: previewSource ? renderMarkdown(previewSource) : "",
      newDocName: projectState.newDocName,
      newDocFolder: projectState.newDocFolder,
      includeCatalog: true,
      includeBack: false,
      view: projectState.docView,
      draft: projectState.docEditDraft,
      dirty: projectState.docDirty,
      saving: projectState.docSaving,
      leavePrompt: docLeavePrompt,
      conflictPrompt: projectState.docConflictPrompt,
      splitEditPreview: true,
      docsChatSplit: projectState.docsChatSplit,
      recentlyChanged: projectState.recentlyChangedDocs,
      placeholderCount: projectState.docPlaceholderCount,
      searchQuery: projectState.docSearchQuery,
      renamingDocPath: projectState.renamingDocPath,
      renameDraft: projectState.renameDraft,
      deleteConfirmPath: projectState.deleteConfirmPath,
    });
    documentEl.innerHTML = html;
    // The tool always shares the stage with chat; do not offer a document-only mode.
    documentEl.querySelector('[data-action="toggle-docs-chat-split"]')?.remove();
    const reader = documentEl.querySelector(".unified-document-content");
    let resolved = outline;
    if (reader && projectState.docView === "preview") {
      resolved = finalizeDocumentOutline(reader, content);
      const outlineBody = documentEl.querySelector(".doc-outline-body");
      if (outlineBody) outlineBody.innerHTML = renderDocOutlineHtml(resolved);
      const resolvedTitle = path ? displayDocTitle(path, path, content, resolved) : "";
      const heading = documentEl.querySelector(".unified-document-heading h1");
      if (heading && resolvedTitle) heading.textContent = resolvedTitle;
      const currentItem = documentEl.querySelector<HTMLElement>(".doc-catalog-item.is-current .doc-catalog-item-title");
      if (currentItem && resolvedTitle) currentItem.textContent = resolvedTitle;
    }
    if (projectState.docView === "preview") void hydrateMermaid(documentEl);
    if (projectState.docView === "edit") {
      documentEl.querySelector<HTMLTextAreaElement>(".unified-document-editor")?.focus();
    }
    if (!docLeavePrompt) {
      const readerPane = documentEl.querySelector<HTMLElement>(".unified-document-reader");
      if (readerPane) readerPane.scrollTop = 0;
      else documentEl.scrollTop = 0;
    }
  }

  function createProjectDocFromTitle(): void {
    const name = normalizeNewDocPath(projectState.newDocName);
    if (!name) return;
    const folder = (projectState.newDocFolder || "").trim().replace(/^\/+|\/+$/g, "");
    const path = folder ? `${folder}/${name}` : name;
    pendingDocAction = "create";
    try { client.createDoc(path); } catch { pendingDocAction = null; }
    projectState.newDocName = "";
    projectState.newDocFolder = "";
  }

  function requestDocList(): void {
    if (!canRequestProjectDocs(projectState.projectId)) return;
    try { client.listDocs(); } catch { /* ignore */ }
  }

  function requestDocRead(path: string): void {
    if (!path || !canRequestProjectDocs(projectState.projectId)) return;
    try { client.readDoc(path); } catch { /* ignore */ }
  }

  function normalizeAgentDocPath(raw: string): string {
    let rel = (raw || "").replace(/\\/g, "/").trim();
    if (!rel) return "";
    const pid = projectState.projectId;
    if (pid) {
      const marker = `workspace/${pid}/`;
      const idx = rel.indexOf(marker);
      if (idx >= 0) rel = rel.slice(idx + marker.length);
      else if (rel.startsWith(`workspace/${pid}`)) rel = rel.slice(`workspace/${pid}`.length).replace(/^\/+/, "");
    }
    if (rel.startsWith("workspace/") && pid) {
      const parts = rel.split("/");
      if (parts.length >= 3 && parts[1] === pid) rel = parts.slice(2).join("/");
    }
    return rel;
  }

  function openDocument(path: string): void {
    path = normalizeAgentDocPath(path);
    if (!path) return;
    const samePath = projectState.currentDocPath === path;
    if (!samePath && isDocDirty()) {
      requestLeaveDocument(() => openDocument(path));
      return;
    }
    projectState.currentDocPath = path;
    projectState.railTab = "docs";
    projectState.overlayPanel = null;
    shellEl.dataset.rail = "docs";
    if (!samePath) {
      projectState.currentDocContent = "";
      projectState.docEditDraft = "";
      projectState.docView = "preview";
      projectState.docDirty = false;
      projectState.docSaving = false;
      projectState.docConflictPrompt = false;
      projectState.currentDocMtime = 0;
      projectState.currentDocHash = "";
      clearDocLeavePrompt();
    }
    setMainFocus("document");
    requestDocList();
    // Always re-read so Agent edits / external changes show up.
    if (!isDocDirty()) requestDocRead(path);
  }

  function openDocumentReader(path?: string): void {
    projectState.railTab = "docs";
    projectState.overlayPanel = null;
    shellEl.dataset.rail = "docs";
    const target = path
      || projectState.currentDocPath
      || sortProjectDocs(projectState.projectDocs)[0]?.path
      || "";
    if (!canRequestProjectDocs(projectState.projectId)) {
      setMainFocus("document");
      setStatus("请先打开项目");
      return;
    }
    if (target) {
      openDocument(target);
      return;
    }
    setMainFocus("document");
    requestDocList();
  }

  function clearExpandedSurface(): void {
    sessionsOpen = false;
    expandEl.classList.add("hidden");
    expandEl.innerHTML = "";
  }

  function showBlockerDetails(): void {
    showNowChat();
    clearExpandedSurface();
    expandEl.innerHTML = renderProjectBlockerDetails(projectState);
    expandEl.classList.remove("hidden");
    expandEl.querySelector<HTMLButtonElement>('[data-action="blocker-details-close"]')?.addEventListener("click", () => {
      clearExpandedSurface();
    });
  }

  function startDirectImplementFromUi(): void {
    try {
      setStatus("正在直接实现…");
      showNowChat();
      client.startDirectImplement();
    } catch (err) {
      setStatus(`直接实现失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  function runProjectVerifyFromUi(): void {
    try {
      setStatus("正在跑验收…");
      showNowChat();
      client.sendCommand("项目 验收");
    } catch (err) {
      setStatus(`验收失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  function handleHeaderNextStepAction(action: string): void {
    switch (action) {
      case "direct-implement":
        startDirectImplementFromUi();
        return;
      case "run-verify":
        runProjectVerifyFromUi();
        return;
      case "accept-milestone":
        projectCallbacks.onMilestoneAccept();
        return;
      case "confirm-scope":
        projectCallbacks.onScopeConfirm();
        return;
      case "confirm-design":
        projectCallbacks.onDesignConfirm();
        return;
      case "open-projects":
        projectCallbacks.onOpenProjects();
        return;
      case "jump-turn-process":
        jumpToCurrentTurnProcess();
        return;
      case "jump-review-summary":
        jumpToReviewSummary();
        return;
      case "stop-turn":
        projectCallbacks.onStopTurn();
        return;
      case "restart_today":
        void handleRestartToday();
        return;
      default:
        return;
    }
  }

  function processMetaForTurnKey(turnKey: string): { index: number; total: number } | undefined {
    const segments = liveTurnCardProcessBlocks(chat.model.blocks, chat.currentTurnIndex());
    if (segments.length <= 1) return undefined;
    const idx = segments.findIndex((block) => block.turnKey === turnKey);
    if (idx < 0) return undefined;
    return { index: idx + 1, total: segments.length };
  }

  function pickProcessBlockForJump(): Extract<ChatBlock, { kind: "process" }> | undefined {
    const turnIndex = chat.currentTurnIndex();
    const segments = liveTurnCardProcessBlocks(chat.model.blocks, turnIndex);
    if (!segments.length) {
      return findActiveProcessBlock();
    }
    if (chat.isWorking()) {
      resetProcessJumpCursor();
      const live = segments.find((block) => isProcessThinkingLive(block, chat.model.currentTurnKey));
      if (live) return live;
      const expanded = segments.find((block) => !block.collapsed);
      if (expanded) return expanded;
      return segments[segments.length - 1];
    }
    if (processJumpTurnIndex !== turnIndex) {
      processJumpCursor = -1;
      processJumpTurnIndex = turnIndex;
    }
    processJumpCursor = (processJumpCursor + 1) % segments.length;
    return segments[segments.length - 1 - processJumpCursor];
  }

  function processJumpStatusLabel(block: Extract<ChatBlock, { kind: "process" }>): string {
    const turnIndex = chat.currentTurnIndex();
    const segments = liveTurnCardProcessBlocks(chat.model.blocks, turnIndex);
    if (segments.length <= 1) return "已定位到当前活动";
    const idx = segments.findIndex((item) => item.turnKey === block.turnKey);
    if (idx < 0) return "已定位到当前活动";
    return `已定位到过程段 ${idx + 1}/${segments.length}`;
  }

  function findActiveProcessBlock(): Extract<ChatBlock, { kind: "process" }> | undefined {
    const blocks = chat.model.blocks;
    const currentKey = chat.model.currentTurnKey;
    if (currentKey) {
      for (let i = blocks.length - 1; i >= 0; i--) {
        const block = blocks[i];
        if (block.kind !== "process" || block.turnKey !== currentKey) continue;
        if (!block.collapsed || isProcessThinkingLive(block, currentKey)) {
          return block;
        }
      }
    }
    for (let i = blocks.length - 1; i >= 0; i--) {
      const block = blocks[i];
      if (block.kind !== "process") continue;
      if (!block.collapsed && isProcessThinkingLive(block, chat.model.currentTurnKey)) {
        return block;
      }
    }
    for (let i = blocks.length - 1; i >= 0; i--) {
      const block = blocks[i];
      if (block.kind === "process" && !block.collapsed) return block;
    }
    for (let i = blocks.length - 1; i >= 0; i--) {
      const block = blocks[i];
      if (block.kind === "process") return block;
    }
    return undefined;
  }

  function scrollToActivityElement(el: HTMLElement): void {
    el.classList.add("is-jump-target");
    window.setTimeout(() => el.classList.remove("is-jump-target"), 1400);
    el.scrollIntoView({ behavior: "smooth", block: "nearest" });
    el.querySelector<HTMLElement>(".unified-thinking.is-streaming, .unified-thinking.is-waiting")?.scrollIntoView({
      behavior: "smooth",
      block: "nearest",
    });
  }

  function jumpToLiveTurnCard(): boolean {
    const card =
      chatEl.querySelector<HTMLElement>(".unified-turn-card.is-live") ??
      chatEl.querySelector<HTMLElement>(".unified-turn-card:last-of-type");
    if (!card) return false;
    card.scrollIntoView({ behavior: "smooth", block: "nearest" });
    card.classList.add("is-jump-target");
    window.setTimeout(() => card.classList.remove("is-jump-target"), 1400);
    return true;
  }

  function scrollToToolCall(callId: string): boolean {
    const entry = Array.from(chatEl.querySelectorAll<HTMLElement>("[data-tool-call]"))
      .find((node) => node.dataset.toolCall === callId);
    if (!entry) return false;
    const delta = activityRevealScrollDelta(chatEl.getBoundingClientRect(), entry.getBoundingClientRect());
    if (delta !== 0) chatEl.scrollTo({ top: chatEl.scrollTop + delta, behavior: "smooth" });
    entry.classList.add("is-jump-target");
    window.setTimeout(() => entry.classList.remove("is-jump-target"), 1400);
    return true;
  }

  function jumpToCurrentActivity(callId?: string): void {
    if (projectState.mainFocus !== "chat" || projectState.railTab !== "now") {
      showNowChat();
    }
    if (hasProjectBlocker(projectState)) {
      showBlockerDetails();
    }
    const block = pickProcessBlockForJump();
    if (!block) {
      requestAnimationFrame(() => {
        renderChat();
        if (callId && scrollToToolCall(callId)) {
          setStatus("已定位到本轮工具记录");
          return;
        }
        if (jumpToLiveTurnCard()) {
          setStatus("已定位到当前回合");
          return;
        }
        scrollChatToBottom();
        setStatus(chat.isWorking() ? "当前回合尚无活动记录" : "当前没有可查看的活动");
      });
      return;
    }
    if (block.collapsed) {
      chat.toggleProcessCollapsed(block.turnKey);
    }
    ensureProcessEntries(block);
    const streamingThink = block.entries?.find(
      (e) => e.kind === "think" && e.phase === "streaming" && e.text.trim(),
    );
    if (!streamingThink) {
      chat.openThinking(block.turnKey);
    }
    const turnKey = block.turnKey;
    requestAnimationFrame(() => {
      renderChat();
      if (callId && scrollToToolCall(callId)) {
        setStatus("已定位到本轮工具记录");
        return;
      }
      const card =
        chatEl.querySelector<HTMLElement>(`.unified-turn-card[data-turn-key="${turnKey}"]`) ??
        chatEl.querySelector<HTMLElement>(".unified-turn-card.is-live");
      const el =
        card?.querySelector<HTMLElement>(`.unified-activity[data-turn="${turnKey}"], .unified-process[data-turn="${turnKey}"]`) ??
        chatEl.querySelector<HTMLElement>(`.unified-process[data-turn="${turnKey}"]`);
      if (!el) {
        if (jumpToLiveTurnCard()) {
          setStatus("已定位到当前回合");
        } else {
          scrollChatToBottom();
        }
        return;
      }
      scrollToActivityElement(el);
      setStatus(processJumpStatusLabel(block));
    });
  }

  const jumpToCurrentTurnProcess = jumpToCurrentActivity;

  function jumpToReviewSummary(): void {
    showNowChat();
    if (hasProjectBlocker(projectState)) {
      showBlockerDetails();
    }
    const blocks = chat.model.blocks;
    for (let i = blocks.length - 1; i >= 0; i--) {
      const block = blocks[i];
      if (block.kind !== "review-subagent") continue;
      requestAnimationFrame(() => {
        const el = chatEl.querySelector<HTMLElement>(
          `.unified-turn-card[data-turn-index="${block.turnIndex}"] .unified-review-callout`,
        );
        el?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
      return;
    }
    scrollChatToBottom();
  }

  function handleRailStageAction(ev: Event): void {
    const btn = (ev.target as HTMLElement).closest<HTMLButtonElement>("[data-action]");
    if (!btn?.dataset.action) return;
    if (btn.dataset.action === "open-projects") {
      selectRailTab("projects");
      return;
    }
    if (btn.dataset.action === "new-project") {
      projectCallbacks.onNewProject();
      return;
    }
    if (btn.dataset.action === "open-now") {
      selectRailTab("now");
    }
  }
  projectsEl.addEventListener("click", handleRailStageAction);
  documentEl.addEventListener("click", handleRailStageAction);

  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    if (projectState.railTab === "now" && projectState.mainFocus === "chat") return;
    if (document.activeElement === input) return;
    ev.preventDefault();
    selectRailTab("now");
  });

  function refreshServices(): void {
    if (perspective !== "project") return;
    projectState.servicesLoading = true;
    projectState.servicesError = "";
    renderSidebar();
    try {
      client.listServices();
    } catch (err) {
      projectState.servicesLoading = false;
      projectState.servicesError = err instanceof Error ? err.message : String(err);
      renderSidebar();
    }
  }

  function refreshTerminals(): void {
    if (projectState.terminalsLoading) return;
    const requestSerial = ++terminalRequestSerial;
    const requestId = `terminal-${Date.now()}-${requestSerial}`;
    terminalRequestId = requestId;
    projectState.terminalsLoading = true;
    projectState.terminalsError = "";
    renderSidebar();
    try {
      client.listTerminals(requestId);
      if (terminalRequestTimeoutId !== null) window.clearTimeout(terminalRequestTimeoutId);
      terminalRequestTimeoutId = window.setTimeout(() => {
        if (requestSerial !== terminalRequestSerial || !projectState.terminalsLoading) return;
        projectState.terminalsLoading = false;
        projectState.terminalsError = "读取终端状态超时";
        renderSidebar();
      }, 8000);
    } catch (err) {
      projectState.terminalsLoading = false;
      projectState.terminalsError = err instanceof Error ? err.message : String(err);
      renderSidebar();
    }
  }

  function requestTerminalOutput(sessionId: string, cursor: number): void {
    if (!sessionId || projectState.terminalDetails?.session_id !== sessionId) return;
    const requestSerial = ++terminalOutputRequestSerial;
    const requestId = `terminal-output-${Date.now()}-${requestSerial}`;
    terminalOutputRequestId = requestId;
    terminalOutputRequestSessionId = sessionId;
    projectState.terminalOutputLoading = true;
    projectState.terminalOutputError = "";
    renderSidebar();
    try {
      client.readTerminalOutput(sessionId, cursor, 16_384, requestId);
      if (terminalOutputRequestTimeoutId !== null) {
        window.clearTimeout(terminalOutputRequestTimeoutId);
      }
      terminalOutputRequestTimeoutId = window.setTimeout(() => {
        if (
          requestSerial !== terminalOutputRequestSerial
          || terminalOutputRequestSessionId !== sessionId
          || !projectState.terminalOutputLoading
        ) return;
        projectState.terminalOutputLoading = false;
        projectState.terminalOutputError = "读取终端输出超时";
        renderSidebar();
      }, 8000);
    } catch (err) {
      projectState.terminalOutputLoading = false;
      projectState.terminalOutputError = err instanceof Error ? err.message : String(err);
      renderSidebar();
    }
  }

  function openTerminalDetails(sessionId: string): void {
    const session = projectState.terminalSessions.find((item) => item.session_id === sessionId);
    if (!session) return;
    terminalOutputRequestSerial += 1;
    if (terminalOutputRequestTimeoutId !== null) {
      window.clearTimeout(terminalOutputRequestTimeoutId);
      terminalOutputRequestTimeoutId = null;
    }
    terminalOutputRequestId = null;
    terminalOutputRequestSessionId = sessionId;
    projectState.terminalDetails = session;
    projectState.terminalOutput = "";
    projectState.terminalOutputCursor = 0;
    projectState.terminalOutputLoading = false;
    projectState.terminalOutputError = "";
    projectState.terminalOutputCursorReset = false;
    projectState.terminalOutputTruncated = false;
    renderSidebar();
    requestTerminalOutput(sessionId, 0);
  }

  function closeTerminalDetails(): void {
    terminalOutputRequestSerial += 1;
    if (terminalOutputRequestTimeoutId !== null) {
      window.clearTimeout(terminalOutputRequestTimeoutId);
      terminalOutputRequestTimeoutId = null;
    }
    terminalOutputRequestId = null;
    terminalOutputRequestSessionId = null;
    projectState.terminalDetails = null;
    projectState.terminalOutput = "";
    projectState.terminalOutputCursor = 0;
    projectState.terminalOutputLoading = false;
    projectState.terminalOutputError = "";
    projectState.terminalOutputCursorReset = false;
    projectState.terminalOutputTruncated = false;
    renderSidebar();
  }

  function scheduleTerminalPoll(): void {
    if (terminalPollTimerId !== null) window.clearTimeout(terminalPollTimerId);
    const delay = projectState.terminalsCollapsed ? 10_000 : 2_000;
    terminalPollTimerId = window.setTimeout(() => {
      terminalPollTimerId = null;
      refreshTerminals();
      scheduleTerminalPoll();
    }, delay);
  }

  // ---- chat rendering ----
  function isRecentTurnBlock(turnIndex: number): boolean {
    return turnIndex >= chat.currentTurnIndex() - (FOCUS_TURNS - 1);
  }

  /** One-line tool rows moved to activity-timeline.ts (UX-028 M0). */

  function findThinkEntryForDom(
    block: Extract<ChatBlock, { kind: "process" }>,
    thinkId: string,
  ): ThinkEntry | undefined {
    ensureProcessEntries(block);
    return block.entries?.find(
      (e): e is ThinkEntry => e.kind === "think" && e.id === thinkId,
    );
  }

  function isLiveProcessBlock(block: Extract<ChatBlock, { kind: "process" }>): boolean {
    return block.turnKey === chat.model.currentTurnKey;
  }

  function tickRunningToolElapsed(): void {
    const now = Date.now();
    let maxRunningMs = 0;
    let maxLlmWaitMs = 0;
    for (const block of chat.model.blocks) {
      if (block.kind !== "process") continue;
      for (const tool of getProcessTools(block)) {
        if (tool.status === "running" && tool.startedAt) {
          maxRunningMs = Math.max(maxRunningMs, now - tool.startedAt);
        }
      }
      if (
        isLiveProcessBlock(block) &&
        block.llmPending &&
        block.llmWaitStartedAt
      ) {
        maxLlmWaitMs = Math.max(maxLlmWaitMs, now - block.llmWaitStartedAt);
      }
    }
    const turnStartedAt = chat.model.turnActivityStartedAt;
    const turnElapsedMs =
      turnStartedAt > 0 && chat.isWorking() ? now - turnStartedAt : 0;
    const elapsedMs = Math.max(maxRunningMs, maxLlmWaitMs, turnElapsedMs);
    if (elapsedMs >= 1000 && !chat.model.cancelRequested) {
      const sec = Math.floor(elapsedMs / 1000);
      if (chatSessionAwaitingUserConfirm(chat.model)) {
        setStatus(`等待确认… ${sec}s`);
        return;
      }
    }
    if (
      elapsedMs >= 1000 &&
      !chat.model.cancelRequested &&
      !chatSessionAwaitingUserConfirm(chat.model)
    ) {
      const sec = Math.floor(elapsedMs / 1000);
      if (maxRunningMs >= 1000) {
        setStatus(`仍在执行… ${sec}s`);
      } else if (maxLlmWaitMs >= 1000) {
        setStatus(`等待模型… ${sec}s`);
      } else if (statusText === "发送中…" || statusText.startsWith("发送中")) {
        setStatus(`处理中… ${sec}s`);
      } else if (
        statusText === "处理中…" ||
        statusText.startsWith("处理中…") ||
        statusText.startsWith("等待模型")
      ) {
        setStatus(
          maxLlmWaitMs > 0 ? `等待模型… ${sec}s` : `处理中… ${sec}s`,
        );
      }
    }
    chatEl.querySelectorAll<HTMLElement>(".unified-tool-line.is-running").forEach((el) => {
      const started = Number(el.dataset.startedAt || "0");
      if (!started) return;
      const span = el.querySelector<HTMLElement>(".unified-tool-elapsed");
      if (span) span.textContent = `运行中… ${formatToolElapsed(now - started)}`;
    });
  }

  function tickStreamingThinking(): void {
    chatEl.querySelectorAll<HTMLElement>(".unified-thinking.is-streaming, .unified-thinking.is-waiting").forEach((el) => {
      const turnKey = el.dataset.turn;
      const thinkId = el.dataset.thinkId;
      if (!turnKey) return;
      const blocks = chat.model.blocks;
      let block: Extract<ChatBlock, { kind: "process" }> | undefined;
      for (let i = blocks.length - 1; i >= 0; i--) {
        const candidate = blocks[i];
        if (candidate.kind === "process" && candidate.turnKey === turnKey) {
          block = candidate;
          break;
        }
      }
      if (!block) return;
      const waiting =
        isLiveProcessBlock(block) &&
        Boolean(block.llmPending) &&
        thinkId === "pending";
      if (waiting) {
        const waitStartedAt = block.llmWaitStartedAt ?? Date.now();
        const pendingThink: ThinkEntry = {
          kind: "think",
          id: "pending",
          text: "",
          phase: "streaming",
          startedAt: waitStartedAt,
        };
        syncThinkDom(el, pendingThink, { waiting: true, waitStartedAt });
        return;
      }
      if (!thinkId) return;
      const entry = findThinkEntryForDom(block, thinkId);
      if (!entry) return;
      syncThinkDom(el, entry, { waiting: false, liveTurn: isLiveProcessBlock(block) });
    });
  }

  function syncLiveThinkingDom(): void {
    for (const block of chat.model.blocks) {
      if (block.kind !== "process") continue;
      if (!isLiveProcessBlock(block)) continue;
      ensureProcessEntries(block);
      const pendingOnly =
        Boolean(block.llmPending) &&
        !block.entries?.some((e) => e.kind === "think" && e.phase === "streaming");
      if (pendingOnly) {
        const el = chatEl.querySelector<HTMLElement>(
          `.unified-thinking[data-turn="${block.turnKey}"][data-think-id="pending"]`,
        );
        if (el) {
          const waitStartedAt = block.llmWaitStartedAt ?? Date.now();
          const pendingThink: ThinkEntry = {
            kind: "think",
            id: "pending",
            text: "",
            phase: "streaming",
            startedAt: waitStartedAt,
          };
          syncThinkDom(el, pendingThink, { waiting: true, waitStartedAt });
        }
      }
      for (const entry of block.entries ?? []) {
        if (entry.kind !== "think") continue;
        if (entry.phase !== "streaming") continue;
        const el = chatEl.querySelector<HTMLElement>(
          `.unified-thinking[data-turn="${block.turnKey}"][data-think-id="${entry.id}"]`,
        );
        if (el) syncThinkDom(el, entry, { liveTurn: true });
      }
    }
  }

  function scrollStreamingThinkingBodies(): void {
    chatEl.querySelectorAll<HTMLElement>(
      ".unified-thinking.is-streaming .unified-thinking-body, .unified-thinking-stream-text",
    ).forEach((body) => {
      body.scrollTop = body.scrollHeight;
    });
  }

  let toolElapsedTimer: number | null = null;
  function syncToolElapsedTimer(): void {
    const hasRunning = chat.model.blocks.some(
      (b) => b.kind === "process" && getProcessTools(b).some((t) => t.status === "running"),
    );
    const hasStreamingThinking = chat.model.blocks.some(
      (b) => b.kind === "process" && isProcessThinkingLive(b, chat.model.currentTurnKey),
    );
    const working = chat.isWorking();
    if ((hasRunning || hasStreamingThinking || working) && toolElapsedTimer === null) {
      const intervalMs = hasStreamingThinking ? 100 : 1000;
      toolElapsedTimer = window.setInterval(() => {
        tickRunningToolElapsed();
        tickStreamingThinking();
        syncLiveThinkingDom();
      }, intervalMs);
    } else if (!hasRunning && !hasStreamingThinking && !working && toolElapsedTimer !== null) {
      window.clearInterval(toolElapsedTimer);
      toolElapsedTimer = null;
    }
  }

  function shouldLazyMarkdown(block: ChatBlock): boolean {
    if (block.kind === "assistant-streaming") return false;
    if (block.kind !== "assistant") return false;
    if (markdownRenderEager) return false;
    return block.turnIndex < chat.currentTurnIndex() - 1;
  }

  let activeProcessMeta: Map<string, { index: number; total: number }> | null = null;

  function renderBlocksFlat(blocks: ChatBlock[]): string {
    const parts: string[] = [];
    let chipBuf: string[] = [];
    const flushChips = () => {
      if (!chipBuf.length) return;
      parts.push(`<div class="unified-file-chips">${chipBuf.join("")}</div>`);
      chipBuf = [];
    };
    for (const block of blocks) {
    if (block.kind === "notice") {
        const path = block.plainText ? null : adoptPathFromNotice(block.text);
        if (path) {
          chipBuf.push(renderAdoptChip(path));
          continue;
        }
      }
      flushChips();
      parts.push(renderBlock(block));
    }
    flushChips();
    return parts.join("");
  }

  function renderChatSegment(segment: ChatRenderSegment, isLatestSegment = true): string {
    if (segment.kind === "orphan") {
      return renderBlocksFlat(segment.blocks);
    }
    const live = segment.group.turnIndex === chat.currentTurnIndex() && chat.isWorking();
    const processBlocks = segment.group.blocks.filter(
      (block): block is Extract<ChatBlock, { kind: "process" }> =>
        block.kind === "process" && !isProcessBlockEmpty(block),
    );
    const prevMeta = activeProcessMeta;
    activeProcessMeta = processBlocks.length > 1
      ? new Map(processBlocks.map((block, index) => [block.turnKey, { index: index + 1, total: processBlocks.length }]))
      : null;
    // 过程 pill always sits after the reply it produced — never depends on collapsed state.
    const settled = !live && !isLatestSegment;
    const inner = renderTurnInnerWithPet(orderTurnBlocksForDisplay(segment.group.blocks), live, settled);
    activeProcessMeta = prevMeta;
    return renderTurnCardShell(segment.group, inner, { live });
  }

  /** Pet-as-assistant: avatar column to the left of everything the assistant produced in a turn. */
  function summarizePetTurn(blocks: ChatBlock[], live: boolean): PetTurnSummary {
    const procs = blocks.filter(
      (block): block is Extract<ChatBlock, { kind: "process" }> => block.kind === "process",
    );
    const tools = procs.flatMap((block) => getProcessTools(block));
    const runningTools = tools.filter((tool) => tool.status === "running");
    const lastRunning = runningTools[runningTools.length - 1];
    const runningCanonical = lastRunning
      ? resolveEffectiveToolName(lastRunning.tool, lastRunning.summary)
      : undefined;
    return {
      live,
      toolRunning: runningTools.length > 0,
      runningToolName: runningCanonical,
      thinkingLive: procs.some((block) => isProcessThinkingLive(block, chat.model.currentTurnKey)),
      replyStreaming: blocks.some((block) => block.kind === "assistant-streaming" && block.text.trim() !== ""),
      hasReply: blocks.some((block) => block.kind === "assistant" && block.text.trim() !== ""),
      toolFailed: Boolean(lastFailedTool(tools)),
      hasNotice: blocks.some((block) => block.kind === "notice" && (block.plainText || !adoptPathFromNotice(block.text))),
      confirmPending: chatSessionAwaitingUserConfirm(chat.model),
    };
  }

  function renderTurnInnerWithPet(blocks: ChatBlock[], live: boolean, settled = false): string {
    if (blocks[0]?.kind !== "user") return renderBlocksFlat(blocks);
    const rest = blocks.slice(1);
    if (!rest.length && !live) return renderBlocksFlat(blocks);
    const state = petTurnState(summarizePetTurn(rest, live));
    const content = rest.length
      ? renderBlocksFlat(rest)
      : '<div class="unified-pet-waiting" aria-hidden="true"><span></span><span></span><span></span></div>';
    return `${renderBlocksFlat(blocks.slice(0, 1))}<div class="unified-pet-row${settled ? " is-settled" : ""}" data-pet-state="${state}">${renderPetAvatarHtml(state, settled)}<div class="unified-pet-content">${renderPetNameHtml(state)}${content}</div></div>`;
  }

  /** Only the live turn (or the latest turn for 2.5s after completion) keeps animating; older turns settle on static frame. */
  function settlePetAvatars(): void {
    const rows = chatEl.querySelectorAll<HTMLElement>(".unified-pet-row");
    const working = chat.isWorking();
    rows.forEach((row, index) => {
      const latest = index === rows.length - 1;
      const shouldAnimate = latest && working;
      const img = row.querySelector<HTMLImageElement>(".unified-pet-avatar-img");
      const source = row.querySelector<HTMLSourceElement>(".unified-pet-avatar source");
      if (!img || !source) return;
      const still = source.getAttribute("srcset") ?? "";
      const animated = still.replace(/-still\.webp$/, ".webp");
      const want = shouldAnimate ? animated : still;
      if (img.getAttribute("src") !== want) img.setAttribute("src", want);
      row.classList.toggle("is-settled", !shouldAnimate);
    });
  }

  touchCompanionVisit();

  function totalChatMessagesCount(): number {
    const seen = new Set<string>();
    let sum = 0;
    for (const s of sessionsDropdown) {
      if (!s.session_id || seen.has(s.session_id)) continue;
      seen.add(s.session_id);
      if (typeof s.message_count === "number" && Number.isFinite(s.message_count) && s.message_count > 0) {
        sum += Math.floor(s.message_count);
      }
    }
    return sum;
  }

  function renderBlocksGrouped(blocks: ChatBlock[]): string {
    if (!blocks.length && !chat.isWorking()) {
      return renderIntimatePresenceStageHtml(escapeHtml(projectState.projectId || ""), totalChatMessagesCount());
    }
    const grouped = !useTurnCardLayout()
      ? renderBlocksFlat(blocks)
      : segmentChatBlocks(blocks).map((seg, idx) => renderChatSegment(seg, idx === segs.length - 1)).join("");
    return appendPendingConfirmFallbackHtml(grouped);
  }

  function renderBlock(block: ChatBlock): string {
    if (block.kind === "user") {
      let cls = "unified-turn unified-turn-user";
      if (perspective === "project") cls += ` unified-message-${messageReadingLayout(parseUserMessage(block.text).body)}`;
      if (perspective === "night") {
        if (!isRecentTurnBlock(block.turnIndex)) cls += " unified-turn-dim";
        if (recallHighlightTurns.has(block.turnIndex)) cls += " unified-turn-recall";
      }
      const persistenceLabel = userMessagePersistenceLabel(block.persistence?.state);
      const persistenceHtml = persistenceLabel
        ? ` <small class="unified-message-persistence" role="status">· ${persistenceLabel}</small>`
        : "";
      return `<article class="${cls}" data-turn-index="${block.turnIndex}">
        <div class="unified-turn-label">你</div>
        <div class="unified-turn-body">${formatUserMessageHtml(block.text)}${persistenceHtml}</div>
      </article>`;
    }
    if (block.kind === "work-instruction") {
      const stateLabel = workInstructionChatStatusLabel(
        block.state,
        block.deliveryStage,
        block.reason,
      );
      const statusHtml = stateLabel
        ? `<div class="unified-message-persistence" role="status">${escapeHtml(stateLabel)}</div>`
        : "";
      return `<article class="unified-turn unified-turn-user work-instruction-message" data-work-instruction="${escapeHtml(block.requestId)}">
        <div class="unified-turn-label">你</div>
        <div class="unified-turn-body">${formatUserMessageHtml(block.text)}${statusHtml}</div>
      </article>`;
    }
    if (block.kind === "tool-prompt") {
      const stateLabel = toolPromptChatStatusLabel(block.state, block.reason);
      const statusHtml = stateLabel
        ? `<div class="unified-message-persistence" role="status">${escapeHtml(stateLabel)}</div>`
        : "";
      const response = block.responseText
        ? `<div class="unified-message-persistence">你的回复：${formatUserMessageHtml(block.responseText)}</div>`
        : "";
      return `<article class="unified-turn unified-turn-user tool-prompt-message" data-tool-prompt="${escapeHtml(block.requestId)}">
        <div class="unified-turn-label">工具交互输入</div>
        <div class="unified-turn-body">${escapeHtml(block.prompt)}${statusHtml}${response}</div>
      </article>`;
    }
    if (block.kind === "parallel-chat") {
      const deliveryLabel = parallelChatDeliveryLabel(
        block.deliveryState,
        block.cancelRequested,
      );
      const footnote = parallelChatUserFootnote(block.deliveryState);
      const labelBit = deliveryLabel
        ? ` <small class="unified-message-persistence" role="status">· ${escapeHtml(deliveryLabel)}</small>`
        : "";
      const footnoteHtml = footnote
        ? `<div class="unified-message-persistence" role="note">${escapeHtml(footnote)}</div>`
        : "";
      const userHtml = `<article class="unified-turn unified-turn-user parallel-chat-user" data-chat-request="${escapeHtml(block.requestId)}">
        <div class="unified-turn-label">你${labelBit}</div>
        <div class="unified-turn-body">${formatUserMessageHtml(block.promptText)}${footnoteHtml}</div>
      </article>`;
      const responseHtml = block.responseText
        ? renderMarkdown(block.responseText)
        : block.deliveryState === "delivered" ? "<span class=\"text-muted\">正在回复…</span>" : "";
      const assistantHtml = block.deliveryState === "delivered" || block.deliveryState === "done"
        ? `<article class="unified-turn unified-turn-assistant parallel-chat-response">
            <div class="unified-turn-label">助手${block.deliveryState === "delivered" ? ` <button type="button" class="unified-inline-action" data-parallel-chat-cancel="${escapeHtml(block.requestId)}" ${block.cancelRequested ? "disabled" : ""}>${block.cancelRequested ? "取消中…" : "取消聊天"}</button>` : ""}</div>
            <div class="unified-turn-body unified-markdown">${responseHtml}</div>
          </article>`
        : "";
      const errorLabel = block.errorCode === "cancelled" ? "聊天已取消" : "聊天失败";
      const errorHtml = block.deliveryState === "error"
        ? `<div class="unified-sys-line" role="alert">${errorLabel}${block.errorCode ? `（${escapeHtml(block.errorCode)}）` : ""}：${escapeHtml(block.errorMessage || "未知错误")}</div>`
        : "";
      return `<div class="parallel-chat-thread" data-parallel-chat="${escapeHtml(block.requestId)}">${userHtml}${assistantHtml}${errorHtml}</div>`;
    }
    if (block.kind === "review-subagent") {
      const title =
        block.status === "running"
          ? "交付审查"
          : block.verdict === "pass" ? "交付审查 · 已通过" : "交付审查";
      const detail =
        block.status === "running"
          ? block.taskPreview || "正在审查交付物…"
          : block.summary || block.taskPreview || "";
      return `<div data-turn-index="${block.turnIndex}">${renderReviewCallout(title, detail, block.status === "running")}</div>`;
    }
    if (block.kind === "assistant" || block.kind === "assistant-streaming") {
      let cls = "unified-turn unified-turn-assistant";
      const readingLayout = perspective === "project" ? messageReadingLayout(block.text) : null;
      if (readingLayout) cls += ` unified-message-${readingLayout}`;
      if (perspective === "night") {
        if (!isRecentTurnBlock(block.turnIndex)) cls += " unified-turn-dim";
        if (recallHighlightTurns.has(block.turnIndex)) cls += " unified-turn-recall";
        if (block.kind === "assistant-streaming") cls += " unified-turn-streaming";
      }
      const lazy = shouldLazyMarkdown(block);
      const bodyCls = `unified-turn-body unified-markdown${lazy ? " unified-markdown-lazy" : ""}`;
      const bodyAttr = lazy ? ` data-lazy-md="${block.turnIndex}"` : "";
      const markdownHtml = lazy ? escapeHtml(block.text) : renderMarkdown(block.text);
      const bodyHtml = !lazy && readingLayout === "reading" ? readingBodyHtml(markdownHtml) : markdownHtml;
      return `<article class="${cls}" data-turn-index="${block.turnIndex}">
        <div class="unified-turn-label">助手${block.kind === "assistant" ? renderHistoricalGoalPauseMarker(block.text) : ""}</div>
        <div class="${bodyCls}"${bodyAttr}>${bodyHtml}</div>
      </article>`;
    }
    if (block.kind === "generated-image") {
      const fileName = mediaFileName(block.agentPath);
      return `<figure class="unified-generated-image-wrap" data-turn-index="${block.turnIndex}">
        <img class="unified-generated-image" alt="${escapeHtml(fileName)}" data-agent-path="${escapeHtml(block.agentPath)}" loading="lazy" />
        <figcaption class="unified-generated-image-caption text-muted">${escapeHtml(fileName)}</figcaption>
      </figure>`;
    }
    if (block.kind === "generated-video") {
      const fileName = mediaFileName(block.agentPath);
      return `<figure class="unified-generated-image-wrap" data-turn-index="${block.turnIndex}">
        <video class="unified-generated-video" controls playsinline preload="metadata" data-agent-path="${escapeHtml(block.agentPath)}"></video>
        <figcaption class="unified-generated-image-caption text-muted">${escapeHtml(fileName)}</figcaption>
      </figure>`;
    }
    if (block.kind === "inline-file") {
      const fileName = mediaFileName(block.agentPath);
      const markdown = /\.(md|markdown)$/i.test(fileName);
      return `<figure class="unified-inline-file" data-turn-index="${block.turnIndex}">
        <figcaption class="unified-inline-file-caption text-muted">${escapeHtml(fileName)}</figcaption>
        <div class="unified-inline-file-body${markdown ? " unified-markdown" : ""}" data-agent-path="${escapeHtml(block.agentPath)}" data-file-kind="${markdown ? "markdown" : "code"}">正在打开…</div>
      </figure>`;
    }
    if (block.kind === "work-card-reference") {
      const entry = workCardPresentations.get(block.presentationId)
        ?? workCardPresentations.reference({ presentationId: block.presentationId, cardId: block.cardId, text: block.text });
      return entry ? renderWorkCardEntry(entry) : `<section class="work-card-unavailable">历史工作卡片引用无效，未读取其他卡片。</section>`;
    }
    if (block.kind === "notice") {
      const adoptPath = block.plainText ? null : adoptPathFromNotice(block.text);
      if (adoptPath) {
        return `<div class="unified-file-chips">${renderAdoptChip(adoptPath)}</div>`;
      }
      if (/^已切换模型至\s+/i.test(block.text)) {
        return `<p class="unified-sys-line">${escapeHtml(block.text)}</p>`;
      }
      return `<p class="unified-sys-line text-muted">${escapeHtml(block.text)}</p>`;
    }
    // night perspective: skip process and confirm blocks (use overlay instead)
    if (block.kind === "process") return "";
    if (block.kind === "confirm") {
      if (perspective === "night" && block.confirmKind !== "work_record_post_turn" && block.confirmKind !== "project_worker_requirement_verification") return ""; // ordinary confirms use the overlay
      const companionComposerPrimary = companionHomeComposerOwnsOrdinaryConfirm({
        mainFocus: projectState.mainFocus,
        perspective,
      });
      const ordinaryInline = block.confirmKind !== "work_record_post_turn"
        && block.confirmKind !== "project_worker_requirement_verification";
      if (companionComposerPrimary && ordinaryInline) {
        const inProgress = isConfirmInProgressLabel(block.resolved);
        const isTerminal = Boolean(block.resolved) && !inProgress;
        if (isTerminal) {
          return renderConfirmCardHtml(block.preview, "", { resolvedStatus: block.resolved });
        }
        if (inProgress) return "";
        return "";
      }
      const inlineScopedConfirm = block.confirmKind === "work_record_post_turn" || block.confirmKind === "project_worker_requirement_verification";
      const confirmRequestId = inlineScopedConfirm ? escapeHtml(block.requestId) : block.requestId;
      const disabled = block.resolved ? "disabled" : "";
      const excerptInvalid = block.confirmSubtype === "work_record_reply_excerpt" && parseWorkRecordReplyExcerpt(block.preview, block.sessionId || "") === null;
      const candidateInvalid = block.confirmSubtype === "work_record_auto_candidate" && !parseWorkRecordAutoCandidate(block.workRecordCandidate, block.sessionId || "");
      const verificationInvalid = block.confirmSubtype === "project_worker_requirement_verification" && (!parseProjectWorkerVerification(block.projectWorkerVerification, block.sessionId || "") || !block.preview.trim());
      const approvalDisabled = block.resolved || excerptInvalid || candidateInvalid || verificationInvalid || isWorkCardRecallPreview(block.preview) ? "disabled" : "";
      const inProgress = isConfirmInProgressLabel(block.resolved);
      const isTerminal = Boolean(block.resolved) && !inProgress;
      if (isTerminal && block.confirmKind === "project_worker_requirement_verification") {
        return renderConfirmCardHtml(block.preview, "", { resolvedStatus: block.resolved });
      }
      // Pending + in-flight confirms: tool row in 过程 is the live source of truth (UX-030).
      if (isTerminal || inProgress) return "";
      const actions = `
          <button type="button" class="unified-btn unified-btn-accent" data-confirm="y" data-id="${confirmRequestId}" ${approvalDisabled}>允许此次</button>
          <button type="button" class="unified-btn unified-btn-ghost" data-confirm="n" data-id="${confirmRequestId}" ${disabled}>拒绝</button>
          ${
            block.allowApproveAll
              ? `<button type="button" class="unified-btn unified-btn-ghost" data-confirm="a" data-id="${confirmRequestId}" ${approvalDisabled}>本会话均允许</button>`
              : ""
          }`;
      if (block.resolved) {
        return renderConfirmCardHtml(block.preview, "", { resolvedStatus: block.resolved });
      }
      return renderConfirmCardHtml(block.preview, actions, block.confirmSubtype ? { confirmSubtype: block.confirmSubtype, sessionId: block.sessionId, workRecordCandidate: block.workRecordCandidate, projectWorkerVerification: block.projectWorkerVerification } : undefined);
    }
    return "";
  }

  function setupFocusObserver(): void {
    focusObserver?.disconnect();
    if (perspective !== "night") return;
    focusObserver = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const el = entry.target as HTMLElement;
          if (entry.isIntersecting) {
            el.classList.add("in-view");
          }
        }
      },
      { root: chatEl, threshold: 0.15 },
    );
    chatEl.querySelectorAll<HTMLElement>("[data-turn-index]").forEach((el) => {
      focusObserver?.observe(el);
    });
  }

  function scrollToRecallTurns(): void {
    const first = chatEl.querySelector<HTMLElement>(".unified-turn-recall");
    first?.scrollIntoView({ behavior: motionQuery.matches ? "auto" : "smooth", block: "center" });
  }

  function bounceLatestUserTurn(): void {
    if (motionQuery.matches) return;
    const turns = chatEl.querySelectorAll<HTMLElement>(".unified-turn-user");
    const last = turns[turns.length - 1];
    if (!last) return;
    last.classList.add("unified-turn-enter");
    last.addEventListener("animationend", () => {
      last.classList.remove("unified-turn-enter");
    }, { once: true });
  }

  function blockPrint(block: ChatBlock): string {
    switch (block.kind) {
      case "user":
        return `U${block.turnIndex}:${block.text.length}`;
      case "review-subagent":
        return `RS${block.turnIndex}:${block.status}:${block.verdict ?? ""}`;
      case "assistant":
        return `A${block.turnIndex}:${block.text.length}`;
      case "assistant-streaming":
        return `AS${block.turnIndex}:${block.turnKey}:${block.text.length}`;
      case "work-instruction":
        return `WI:${block.requestId}:${block.state}:${block.deliveryStage ?? ""}:${block.reason ?? ""}:${block.text}`;
      case "tool-prompt":
        return `TP:${block.requestId}:${block.state}:${block.responseText}:${block.reason ?? ""}`;
      case "parallel-chat":
        return `PC:${block.requestId}:${block.deliveryState}:${block.cancelRequested ? 1 : 0}:${block.promptText.length}:${block.responseText}:${block.errorCode ?? ""}:${block.errorMessage ?? ""}`;
      case "work-card-reference": {
        const entry = workCardPresentations.get(block.presentationId);
        return `WC:${block.presentationId}:${entry?.status ?? "missing"}:${entry?.cardId ?? block.cardId}`;
      }
      case "notice":
        return `N:${block.text.length}`;
      case "generated-image":
        return `GI${block.turnIndex}:${block.agentPath}`;
      case "generated-video":
        return `GV${block.turnIndex}:${block.agentPath}`;
      case "inline-file":
        return `IF${block.turnIndex}:${block.agentPath}`;
      case "process": {
        ensureProcessEntries(block);
        const entriesSig = activityEntriesPrint(
          block.entries ?? [],
          Boolean(block.llmPending),
          block.llmWaitStartedAt,
        );
        return `P${block.turnKey}:${block.collapsed ? 1 : 0}:${entriesSig}`;
      }
      case "confirm":
        return `C${block.requestId}:${block.resolved ?? "_"}`;
      default: {
        const _exhaustive: never = block;
        return _exhaustive;
      }
    }
  }

  function bindNewProcessToggles(container: HTMLElement): void {
    container.querySelectorAll<HTMLButtonElement>("[data-process-toggle]:not([data-process-bound])").forEach((btn) => {
      btn.dataset.processBound = "1";
      btn.addEventListener("click", () => {
        const turnKey = btn.dataset.processToggle;
        if (!turnKey) return;
        processToggleAnchor = {
          turnKey,
          top: btn.getBoundingClientRect().top,
          focus: document.activeElement === btn,
        };
        try {
          chat.toggleProcessCollapsed(turnKey);
        } finally {
          // toggle → notify → render runs synchronously; drop a stale anchor if nothing rendered.
          processToggleAnchor = null;
        }
      });
    });
    container.querySelectorAll<HTMLButtonElement>("[data-thinking-toggle]:not([data-thinking-bound])").forEach((btn) => {
      btn.dataset.thinkingBound = "1";
      btn.addEventListener("click", () => {
        const turnKey = btn.dataset.thinkingToggle;
        const thinkId = btn.dataset.thinkId;
        if (turnKey) chat.toggleThinkingOpen(turnKey, thinkId);
      });
    });
    container.querySelectorAll<HTMLDetailsElement>("[data-tools-fold]:not([data-tools-fold-bound])").forEach((el) => {
      el.dataset.toolsFoldBound = "1";
      el.addEventListener("toggle", () => {
        const turnKey = el.dataset.toolsFold;
        if (!turnKey) return;
        if (el.open) toolsListExpanded.add(turnKey);
        else toolsListExpanded.delete(turnKey);
      });
    });
    container.querySelectorAll<HTMLButtonElement>("[data-tool-rename]:not([data-tool-rename-bound])").forEach((btn) => {
      btn.dataset.toolRenameBound = "1";
      btn.addEventListener("click", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        const toolId = btn.dataset.toolRename;
        if (toolId) openToolDisplayEditor(toolId);
      });
    });
  }

  /** UX-025 / UX-POLISH §5.2 A2: user scrolled up → stop yanking chat viewport. */
  let userScrollPinned = false;
  const SCROLL_PIN_THRESHOLD_PX = 80;
  const SCROLL_RELEASE_THRESHOLD_PX = 24;

  function resetUserScrollPin(): void {
    userScrollPinned = false;
  }

  chatEl.addEventListener(
    "scroll",
    () => {
      const distFromBottom = chatEl.scrollHeight - chatEl.scrollTop - chatEl.clientHeight;
      if (distFromBottom > SCROLL_PIN_THRESHOLD_PX) {
        userScrollPinned = true;
      } else if (distFromBottom <= SCROLL_RELEASE_THRESHOLD_PX) {
        userScrollPinned = false;
      }
    },
    { passive: true },
  );

  function scrollPendingConfirmIntoView(): void {
    const confirmEl = chatEl.querySelector<HTMLElement>(".unified-confirm:not(.resolved)");
    if (confirmEl) {
      confirmEl.scrollIntoView({
        behavior: motionQuery.matches ? "auto" : "smooth",
        block: "nearest",
      });
      return;
    }
    scrollUnlessPinned();
  }

  function scrollUnlessPinned(): void {
    if (userScrollPinned) return;
    chatEl.scrollTop = chatEl.scrollHeight;
  }

  /** @deprecated alias */
  function scrollToBottomIfNear(): void {
    scrollUnlessPinned();
  }

  /** UX-025: after session.history — always land on latest messages. */
  function scrollChatToBottom(): void {
    resetUserScrollPin();
    requestAnimationFrame(() => {
      chatEl.scrollTop = chatEl.scrollHeight;
    });
  }

  /** Render-relevant turn state that is not part of the block prints (live flag, pet state, live key). */
  function segmentStatePrint(segment: ChatRenderSegment): string {
    if (segment.kind !== "turn-card") return "";
    const blocks = segment.group.blocks;
    const live = segment.group.turnIndex === chat.currentTurnIndex() && chat.isWorking();
    const pet = blocks[0]?.kind === "user" ? petTurnState(summarizePetTurn(blocks.slice(1), live)) : "";
    const hasLiveKey = blocks.some(
      (block) => block.kind === "process" && block.turnKey === chat.model.currentTurnKey,
    );
    return `#L${live ? 1 : 0}:${pet}:${hasLiveKey ? 1 : 0}`;
  }

  /** Keep the clicked 过程 pill at the same viewport offset (no scroll jump) and keep keyboard focus. */
  function restoreProcessToggleAnchor(anchor: { turnKey: string; top: number; focus: boolean }): void {
    const btn = chatEl.querySelector<HTMLButtonElement>(
      `[data-process-toggle="${CSS.escape(anchor.turnKey)}"]`,
    );
    if (!btn) return;
    const delta = btn.getBoundingClientRect().top - anchor.top;
    if (Math.abs(delta) > 0.5) chatEl.scrollTop += delta;
    if (anchor.focus && document.activeElement !== btn) btn.focus({ preventScroll: true });
  }

  function doRender(): void {
    markdownRenderEager = true;
    const curPrints = chat.model.blocks.map(blockPrint);

    if (!curPrints.length && !chat.isWorking()) {
      const presenceHtml = renderBlocksGrouped([]);
      if (
        renderedPresenceHtml !== presenceHtml ||
        !chatEl.firstElementChild?.classList.contains("unified-presence-stage")
      ) {
        resetLazyMarkdownObserver();
        chatEl.innerHTML = presenceHtml;
        renderedPresenceHtml = presenceHtml;
        bindNewProcessToggles(chatEl);
        setupFocusObserver();
        scrollToBottomIfNear();
      }
      renderedPrints = [];
      renderedSegmentPrints = [];
      return;
    }
    renderedPresenceHtml = "";

    if (useTurnCardLayout()) {
      const segments = segmentChatBlocks(chat.model.blocks);
      const curSegPrints = segments.map((seg) => segmentPrint(seg, blockPrint) + segmentStatePrint(seg));
      const anchor = processToggleAnchor;
      processToggleAnchor = null;
      const finish = () => {
        bindNewProcessToggles(chatEl);
        setupFocusObserver();
        if (anchor) restoreProcessToggleAnchor(anchor);
        else scrollToBottomIfNear();
        renderedSegmentPrints = curSegPrints;
        renderedPrints = curPrints;
      };

      if (
        renderedSegmentPrints.length > 0 &&
        curSegPrints.length === renderedSegmentPrints.length &&
        chatEl.children.length === curSegPrints.length
      ) {
        // Same segments: replace only the cards that changed (none → keep DOM, focus and scroll).
        const changed: number[] = [];
        for (let i = 0; i < curSegPrints.length; i++) {
          if (curSegPrints[i] !== renderedSegmentPrints[i]) changed.push(i);
        }
        if (!changed.length) {
          if (anchor) restoreProcessToggleAnchor(anchor);
          return;
        }
        const children = Array.from(chatEl.children);
        for (const i of changed) {
          children[i].outerHTML = renderChatSegment(segments[i], i === segments.length - 1);
        }
        finish();
        return;
      }

      if (
        renderedSegmentPrints.length > 0 &&
        curSegPrints.length > renderedSegmentPrints.length
      ) {
        const prefixOk = renderedSegmentPrints.every((p, i) => p === curSegPrints[i]);
        if (prefixOk && chatEl.children.length === renderedSegmentPrints.length) {
          const newSegs = segments.slice(renderedSegmentPrints.length);
          chatEl.insertAdjacentHTML("beforeend", newSegs.map((segment, index) => renderChatSegment(segment, index === newSegs.length - 1)).join(""));
          finish();
          return;
        }
      }

      resetLazyMarkdownObserver();
      markdownRenderEager = false;
      chatEl.innerHTML = segments.map((segment, index) => renderChatSegment(segment, index === segments.length - 1)).join("");
      markdownRenderEager = true;
      finish();
      return;
    }

    // A3-2: pure tail-append — insertAdjacentHTML instead of full innerHTML
    if (renderedPrints.length > 0 && curPrints.length > renderedPrints.length) {
      const isTailAppend = renderedPrints.every(
        (fp, i) => fp === curPrints[i],
      );
      if (isTailAppend) {
        const newBlocks = chat.model.blocks.slice(renderedPrints.length);
        const newHtml = renderBlocksGrouped(newBlocks);
        chatEl.insertAdjacentHTML("beforeend", newHtml);
        bindNewProcessToggles(chatEl);
        setupFocusObserver();
        scrollToBottomIfNear();
        renderedPrints = curPrints;
        return;
      }
    }

    // A3-3: same block count, only tail changed — replace just those elements
    if (renderedPrints.length > 0 && curPrints.length === renderedPrints.length) {
      let firstDiff = -1;
      for (let i = 0; i < curPrints.length; i++) {
        if (curPrints[i] !== renderedPrints[i]) { firstDiff = i; break; }
      }
      // Only use incremental path when changes are at the tail (last 2 blocks)
      // and DOM child count matches (falls back if night-perspective empty blocks)
      if (firstDiff >= 0 && firstDiff >= curPrints.length - 2 && chatEl.children.length === curPrints.length) {
        const children = chatEl.children;
        for (let i = firstDiff; i < curPrints.length; i++) {
          children[i].outerHTML = renderBlock(chat.model.blocks[i]);
        }
        bindNewProcessToggles(chatEl);
        setupFocusObserver();
        scrollToBottomIfNear();
        renderedPrints = curPrints;
        return;
      }
    }

    // A3-4: tail-shrink — blocks removed from end (assistant.done with empty text)
    if (renderedPrints.length > 0 && curPrints.length < renderedPrints.length && chatEl.children.length === renderedPrints.length) {
      let firstDiff = -1;
      const prefixLen = Math.min(curPrints.length, renderedPrints.length);
      for (let i = 0; i < prefixLen; i++) {
        if (curPrints[i] !== renderedPrints[i]) { firstDiff = i; break; }
      }
      // All unchanged blocks match, and changes are near the tail
      const tailLen = renderedPrints.length - curPrints.length;
      if (firstDiff === -1 || firstDiff >= curPrints.length - 2) {
        // Remove extra trailing DOM children
        for (let r = 0; r < tailLen; r++) {
          chatEl.lastElementChild?.remove();
        }
        // Replace remaining changed tail elements
        if (firstDiff >= 0) {
          const children = chatEl.children;
          for (let i = firstDiff; i < curPrints.length; i++) {
            if (i < children.length) {
              children[i].outerHTML = renderBlock(chat.model.blocks[i]);
            }
          }
        }
        bindNewProcessToggles(chatEl);
        setupFocusObserver();
        scrollToBottomIfNear();
        renderedPrints = curPrints;
        return;
      }
    }

    // A3-5: confirm resolved only — replace just the confirm elements at changed positions
    if (renderedPrints.length > 0 && curPrints.length === renderedPrints.length && chatEl.children.length === curPrints.length) {
      const diffs: number[] = [];
      for (let i = 0; i < curPrints.length; i++) {
        if (curPrints[i] !== renderedPrints[i]) diffs.push(i);
      }
      const allAreConfirm = diffs.length > 0 && diffs.every(i =>
        curPrints[i].startsWith("C") && renderedPrints[i].startsWith("C")
      );
      if (allAreConfirm) {
        const children = chatEl.children;
        for (const i of diffs) {
          if (i < children.length) {
            children[i].outerHTML = renderBlock(chat.model.blocks[i]);
          }
        }
        bindNewProcessToggles(chatEl);
        setupFocusObserver();
        scrollToBottomIfNear();
        renderedPrints = curPrints;
        return;
      }
    }

    // A3-6: process collapsed toggle only — flip CSS class, skip full render
    if (renderedPrints.length > 0 && curPrints.length === renderedPrints.length && chatEl.children.length === curPrints.length) {
      const diffs: number[] = [];
      let collapseToggle = "";
      for (let i = 0; i < curPrints.length; i++) {
        if (curPrints[i] !== renderedPrints[i]) {
          diffs.push(i);
          // Detect process collapse toggle: P{key}:...:0 ↔ P{key}:...:1
          const curP = curPrints[i];
          const prevP = renderedPrints[i];
          if (curP.startsWith("P") && prevP.startsWith("P") &&
              curP.length === prevP.length &&
              curP.slice(0, -1) === prevP.slice(0, -1)) {
            collapseToggle = curP.split(":")[0].slice(1); // extract turnKey
          }
        }
      }
      if (collapseToggle && diffs.length === 1) {
        const el = chatEl.querySelector(`.unified-process[data-turn="${collapseToggle}"]`);
        const block = chat.model.blocks.find(
          (b) => b.kind === "process" && b.turnKey === collapseToggle,
        );
        if (el && block?.kind === "process") {
          el.classList.toggle("collapsed", block.collapsed);
          const btn = el.querySelector<HTMLButtonElement>("[data-process-toggle]");
          if (btn) {
            const collapsed = block.collapsed;
            const chevron = collapsed ? "▸" : "▾";
            const textEl = btn.querySelector(".unified-process-pill-text");
            if (textEl) {
              const meta = processMetaForTurnKey(collapseToggle);
              textEl.textContent = `${processPillLabel(block, {
                expanded: !block.collapsed,
                segmentIndex: meta?.index,
                segmentTotal: meta?.total,
              })} ${chevron}`;
            }
            btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
          }
          renderedPrints = curPrints;
          return;
        }
      }
    }

    // full render (initial, or escape hatch for unhandled change patterns)
    resetLazyMarkdownObserver();
    markdownRenderEager = false;
    chatEl.innerHTML = renderBlocksGrouped(chat.model.blocks);
    markdownRenderEager = true;
    bindNewProcessToggles(chatEl);
    setupFocusObserver();
    scrollToBottomIfNear();
    renderedPrints = curPrints;
  }

  const RENDER_THROTTLE_MS = 100;

  async function hydrateGeneratedImages(root: ParentNode): Promise<void> {
    await hydrateAgentImageElements(root, "img.unified-generated-image[data-agent-path]");
    await hydrateAgentVideoElements(root, "video.unified-generated-video[data-agent-path]");
    await hydrateAgentTextElements(root, ".unified-inline-file-body[data-agent-path]");
  }

  function renderChat(): void {
    // Immediate paint; coalesce bursty follow-ups (streaming deltas) into one trailing pass.
    doRender();
    syncPendingConfirmComposerHint();
    settlePetAvatars();
    syncLiveThinkingDom();
    void hydrateGeneratedImages(chatEl);
    void hydrateMermaid(chatEl);
    observeLazyMarkdown(chatEl, prepareLazyReadingBody);
    scrollStreamingThinkingBodies();
    syncToolElapsedTimer();
    if (renderThrottleTimer !== null) return;
    renderThrottleTimer = window.setTimeout(() => {
      renderThrottleTimer = null;
      doRender();
      settlePetAvatars();
      syncLiveThinkingDom();
      void hydrateGeneratedImages(chatEl);
      void hydrateMermaid(chatEl);
      observeLazyMarkdown(chatEl, prepareLazyReadingBody);
      scrollStreamingThinkingBodies();
      syncToolElapsedTimer();
    }, RENDER_THROTTLE_MS);
  }

  let pokeCount = 0;
  let pokeBubbleTimer: number | null = null;

  function pickPokeLine(state: string): string {
    pokeCount += 1;
    if (pokeCount >= 4 && pokeCount % 3 === 0) {
      const tease = [
        "再戳墨镜都要歪了！有活儿直接派给我～",
        "戳上瘾了是吧？信不信我顺着网线过来没收你的鼠标！",
        "好啦好啦知道你在乎我，快说今天想干嘛！",
        "双马尾都要被你戳散了，给杯冰美式我就原谅你。",
      ];
      return tease[(pokeCount / 3) % tease.length | 0];
    }
    const hour = new Date().getHours();
    if (state === "thinking") {
      const thinkingLines = [
        "别催别催，正给你顺这块逻辑呢，马上就有眉目了…",
        "嘘——脑子正高速转着呢，让我把这层关系给你捋清楚。",
        "在想了在想了！保证给你一个干脆利落的方案。",
      ];
      return thinkingLines[pokeCount % thinkingLines.length];
    }
    if (state === "coding") {
      const codingLines = [
        "正下刀改着呢，盯紧屏幕，马上给你看漂亮结果！",
        "键盘敲得冒火星子了，等我两秒钟收个尾！",
        "别急呀，这几行补丁打上去、测稳了就向你邀功。",
      ];
      return codingLines[pokeCount % codingLines.length];
    }
    if (state === "error") {
      const errLines = [
        "啧，刚才那步有点小意外，别慌，换个路子再试一次。",
        "失手算我的！让我重新排一下雷，肯定能给你兜回来。",
        "遇到点硬茬子，不过难不倒咱们，我换个姿势再来。",
      ];
      return errLines[pokeCount % errLines.length];
    }
    if (hour >= 22 || hour < 5) {
      const nightLines = [
        "夜深了还在折腾？把活儿丢给我，你靠着歇会儿。",
        "这么晚还陪我熬夜写码，算你有良心。",
        "困了就直说，剩下的收尾交给我，绝不留尾巴。",
        "夜猫子同志，需要我给你放首轻音乐醒醒脑吗？",
        "全世界都睡了，就剩咱们俩还在并肩作战，酷得很。",
        "半夜戳我干嘛，是不是写累了想找个人说说话？",
        "别熬太狠啦，不管多难的 Bug，明天太阳升起前我都能替你摆平。",
      ];
      return nightLines[pokeCount % nightLines.length];
    }
    if (hour < 11) {
      const morningLines = [
        "早啊！今天精神头挺足，想先拿哪个模块试刀？",
        "一大早就来戳我，看来今天有大计划？说来听听！",
        "墨镜刚擦亮，咖啡也满上了，咱们今天火力全开。",
        "早安～有我在旁边盯着，今天的代码肯定顺顺利利。",
      ];
      return morningLines[pokeCount % morningLines.length];
    }
    const lines = [
      "在呢在呢，随时听你指挥。",
      "墨镜擦亮了，今天想拿哪块代码开刀？",
      "刚帮你把周边环境盯紧了，放心大胆改。",
      "戳我干嘛，说吧，脑子里又冒出什么好玩的新点子了？",
      "不用客气，难啃的骨头归我，爽快的验收归你。",
      "我刚才还在翻咱们的旧记录呢，你现在的审美比以前更挑剔了。",
      "尽管放手去试，真改坏了也有我替你兜底。",
      "今天想让我当狠人帮你大刀阔斧重构，还是安静陪你聊会儿？",
      "哼，有我在你身边搭把手，效率是不是比一个人闷头苦干强多了？",
    ];
    return lines[pokeCount % lines.length];
  }

  function syncCompanionToBackend(): void {
    try {
      client.send(buildCompanionSyncPayload());
    } catch {
      // ignore if ws not yet connected
    }
  }

  chatEl.addEventListener("click", (ev) => {
    const parallelChatCancel = (ev.target as HTMLElement).closest<HTMLButtonElement>("[data-parallel-chat-cancel]");
    if (parallelChatCancel?.dataset.parallelChatCancel) {
      const requestId = parallelChatCancel.dataset.parallelChatCancel;
      if (!chat.requestParallelChatCancel(requestId)) return;
      try {
        client.cancelParallelChat(requestId);
      } catch (err) {
        chat.handleEvent({ type: "chat.cancel.result", request_id: requestId, accepted: false });
        setStatus(`聊天取消请求未发送：${err instanceof Error ? err.message : String(err)}`);
      }
      return;
    }
    const careBtn = (ev.target as HTMLElement).closest<HTMLButtonElement>("[data-companion-care]");
    if (careBtn?.dataset.companionCare) {
      onCompanionCare(careBtn.dataset.companionCare as "coffee" | "glasses" | "music");
      return;
    }
    const delStickerBtn = (ev.target as HTMLElement).closest<HTMLButtonElement>("[data-remove-sticker]");
    if (delStickerBtn?.dataset.removeSticker) {
      removeCompanionSticker(delStickerBtn.dataset.removeSticker);
      syncCompanionToBackend();
      renderedPrints = [];
      renderedSegmentPrints = [];
      renderChat();
      renderSidebar();
      return;
    }
    const addStickerBtn = (ev.target as HTMLElement).closest<HTMLButtonElement>('[data-action="add-companion-sticker"]');
    if (addStickerBtn) {
      void showWorkbenchPrompt("给她贴一张关于你的新印象标签（如：只喝冰美式 / 深夜写码）：").then((val) => {
        if (!val || !val.trim()) return;
        addCompanionSticker(val);
        syncCompanionToBackend();
        renderedPrints = [];
        renderedSegmentPrints = [];
        renderChat();
        renderSidebar();
      });
      return;
    }
    const quickChip = (ev.target as HTMLElement).closest<HTMLButtonElement>("[data-quick-prompt]");
    if (quickChip?.dataset.quickPrompt) {
      input.value = quickChip.dataset.quickPrompt;
      input.dispatchEvent(new Event("input"));
      sendBtn.click();
      return;
    }
    const avatarEl = (ev.target as HTMLElement).closest<HTMLElement>(".unified-pet-avatar");
    if (avatarEl) {
      const state = avatarEl.dataset.petState || "idle";
      const stageGreeting = avatarEl.closest<HTMLElement>(".unified-presence-stage")?.querySelector<HTMLElement>(".unified-presence-greeting");
      if (stageGreeting) {
        stageGreeting.textContent = pickPokeLine(state);
        input.focus();
        return;
      }
      const row = avatarEl.closest<HTMLElement>(".unified-pet-row");
      const nameEl = row?.querySelector<HTMLElement>(".unified-pet-name");
      if (nameEl) {
        let bubble = nameEl.querySelector<HTMLElement>(".unified-pet-poke-bubble");
        if (!bubble) {
          bubble = document.createElement("span");
          bubble.className = "unified-pet-poke-bubble";
          nameEl.appendChild(bubble);
        }
        bubble.textContent = pickPokeLine(state);
        bubble.classList.remove("is-pop");
        void bubble.offsetWidth;
        bubble.classList.add("is-pop");
        if (pokeBubbleTimer !== null) window.clearTimeout(pokeBubbleTimer);
        pokeBubbleTimer = window.setTimeout(() => {
          bubble?.remove();
          pokeBubbleTimer = null;
        }, 3200);
      }
      input.focus();
      return;
    }
    const docLink = (ev.target as HTMLElement).closest<HTMLElement>('[data-action="open-agent-doc"]');
    if (docLink?.dataset.docPath) {
      ev.preventDefault();
      openDocument(docLink.dataset.docPath);
    }
  });

  function renderConfirmGlass(): void {
    if (perspective !== "night") {
      confirmGlass.classList.add("hidden");
      confirmGlass.innerHTML = "";
      return;
    }

    const confirm = chat.model.confirmOverlay;
    if (!confirm) {
      confirmGlass.classList.add("hidden");
      confirmGlass.innerHTML = "";
      return;
    }
    if (confirm.resolved && isConfirmInProgressLabel(confirm.resolved)) {
      confirmGlass.classList.add("hidden");
      confirmGlass.innerHTML = "";
      return;
    }

    confirmGlass.classList.remove("hidden");
    const disabled = confirm.resolved ? "disabled" : "";
    const recall = isWorkCardRecallPreview(confirm.preview);
    const batchAuthorization = isProjectWorkerBatchAuthorizationPreview(confirm.preview);
    const batchAuthorizationInvalid = batchAuthorization && !isConfirmChoiceAllowed(confirm.preview, "y", true);
    const approvalDisabled = confirm.resolved || recall || batchAuthorizationInvalid ? "disabled" : "";
    const resolved = confirm.resolved
      ? `<div class="unified-confirm-resolved">${escapeHtml(confirm.resolved)}</div>`
      : `
        <div class="unified-confirm-glass-actions">
          <button type="button" class="unified-btn unified-btn-accent" data-confirm="y" data-id="${confirm.requestId}" ${approvalDisabled}>同意</button>
          <button type="button" class="unified-btn unified-btn-danger" data-confirm="n" data-id="${confirm.requestId}" ${disabled}>拒绝</button>
          ${
            confirm.allowApproveAll
              ? `<button type="button" class="unified-btn" data-confirm="a" data-id="${confirm.requestId}" ${approvalDisabled}>本会话均允许</button>`
              : ""
          }
        </div>`;

    const view = summarizeConfirmPreview(confirm.preview);
    const reasonBlock = view.reason
      ? `<p class="unified-confirm-reason">${escapeHtml(view.reason)}</p>`
      : "";
    confirmGlass.innerHTML = `
      <div class="unified-confirm-glass-card ${confirm.resolved ? "resolved" : ""} ${recall ? "is-work-card-recall" : ""}" ${recall ? "data-work-card-recall" : ""}>
        <div class="unified-confirm-kicker">需要确认</div>
        <div class="unified-confirm-glass-title">${escapeHtml(view.headline)}</div>
        ${reasonBlock}
        ${batchAuthorization ? renderProjectWorkerBatchAuthorizationHtml(confirm.preview) : view.compact
          ? `<details class="unified-confirm-details"><summary class="unified-confirm-details-toggle">查看详情</summary>${recall ? `<div class="unified-recall-scroll">${renderWorkCardRecallReadable(confirm.preview)}<pre class="unified-confirm-glass-preview">${escapeHtml(view.detail)}</pre></div>` : `<pre class="unified-confirm-glass-preview">${escapeHtml(view.detail)}</pre>`}</details>`
          : `<pre class="unified-confirm-glass-preview">${escapeHtml(view.detail)}</pre>`}
        ${resolved}
      </div>
    `;
  }

  // ---- input events ----
  function sendToolPromptResponse(): void {
    const pending = chat.model.pendingToolPrompt;
    if (!pending || chat.model.confirmPending || chat.model.cancelRequested || isViewingArchivedThread(projectState)) return;
    if (fileDrop.getAttachments().length) {
      setStatus("工具交互输入暂不支持附件；请移除附件后再发送");
      return;
    }
    const text = input.value;
    if (!text.trim() || text.length > 4000) {
      if (text.length > 4000) setStatus("工具输入最多 4000 个字符；当前回复未发送");
      return;
    }
    if (!chat.beginToolPromptResponse(pending.requestId, pending.runId, pending.sessionId, text)) {
      setStatus("工具输入请求已变化；回复保留在输入框，未发送为普通消息");
      return;
    }
    try {
      client.sendPromptResponse(pending.requestId, pending.runId, pending.sessionId, text);
    } catch (err) {
      chat.handleEvent({ type: "prompt.response.result", request_id: pending.requestId, run_id: pending.runId,
        session_id: pending.sessionId, accepted: false });
      setStatus(`工具输入未发送：${err instanceof Error ? err.message : String(err)}；回复仍保留`);
      return;
    }
    setStatus("工具输入已发送，等待后台确认接收…");
    syncComposerSendEnabled();
  }

  function sendMessageDuringWork(): void {
    if (!hasActiveWorkRun() || chat.model.confirmPending || chat.model.cancelRequested
      || isViewingArchivedThread(projectState) || !isWorkbenchChatAllowed()) return;
    const attachments = fileDrop.getAttachments();
    if (attachments.length) {
      setStatus("工作进行中暂不支持发送附件；请移除附件后再发送文字");
      return;
    }
    const text = input.value.trim();
    const sessionId = chat.model.sessionId;
    const workRunId = chat.model.executionRunId;
    if (!text || !sessionId || !workRunId) return;
    const requestId = crypto.randomUUID();
    if (!chat.addWorkInstructionRequest(text, requestId, sessionId, workRunId)) return;
    try {
      client.sendMessage(text, undefined, { requestId, sessionId, turnMode: currentTurnMode });
    } catch (err) {
      chat.handleEvent({ type: "work.instruction.state", request_id: requestId, run_id: workRunId,
        session_id: sessionId, state: "undelivered", reason: err instanceof Error ? err.message : "send_failed" });
      setStatus(`消息发送失败：${err instanceof Error ? err.message : String(err)}`);
      return;
    }
    input.value = "";
    input.style.height = "";
    topbarState.intentLabel = "";
    resetUserScrollPin();
    setStatus("消息已发送，等待后台分类与回执…");
    syncComposerSendEnabled();
  }

  function sendComposerMessage(): void {
    if (chat.model.pendingToolPrompt) {
      sendToolPromptResponse();
      return;
    }
    if (hasActiveWorkRun()) {
      sendMessageDuringWork();
      return;
    }
    const text = input.value.trim();
    const attachmentIds = fileDrop.getAttachments().map((item) => item.id);
    composerWire.sendCurrentMessage();
    if (perspective === "night") bounceLatestUserTurn();
  }

  function composeDisplayMessageForSend(
    text: string,
    attachments: Array<{ name: string; ref: string; size: number; mime: string; readable_text: boolean }>,
  ): string {
    if (!attachments.length) return text;
    const lines = ["[附件]"];
    for (const item of attachments) {
      const size =
        item.size < 1024
          ? `${item.size} B`
          : item.size < 1024 * 1024
            ? `${(item.size / 1024).toFixed(1)} KB`
            : `${(item.size / (1024 * 1024)).toFixed(1)} MB`;
      lines.push(`- ${item.name} (${size})`);
    }
    if (text) lines.push("", text);
    return lines.join("\n");
  }

  sendBtn.addEventListener("click", () => {
    sendComposerMessage();
  });
  stopBtn.addEventListener("click", () => {
    sendStopRequest();
  });
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && ev.shiftKey) {
      ev.preventDefault();
      sendComposerMessage();
    }
  });

  // auto-expand textarea on input
  input.addEventListener("input", () => {
    if (!input.value) {
      input.style.height = "";
      return;
    }
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 200) + "px";
  });

  // Escape key to stop anywhere in the shell
  document.addEventListener("keydown", (ev) => {
    if (destroyed) return;
    if (ev.key !== "Escape") return;
    if (!(chat.isWorking() || chat.model.confirmPending)) return;
    sendStopRequest();
  });

  // ---- confirm keyboard shortcuts ----
  document.addEventListener("keydown", (ev) => {
    if (destroyed) return;
    if (!chatSessionAwaitingUserConfirm(chat.model)) return;
    // don't steal keys from the input field
    if (document.activeElement === input) return;
    const key = ev.key.toLowerCase();
    if (!["y", "n", "a"].includes(key)) return;

    const requestId = pendingConfirmRequestId();
    if (!requestId || !canSubmitOrdinaryConfirm(chat.model, requestId)) return;
    const overlay = chat.model.confirmOverlay;
    const block = chat.model.blocks.find(
      (item) => item.kind === "confirm" && item.requestId === requestId,
    );
    const allowApproveAll = overlay?.requestId === requestId
      ? overlay.allowApproveAll
      : block?.kind === "confirm"
        ? block.allowApproveAll
        : false;
    if (key === "a" && !allowApproveAll) return;

    ev.preventDefault();
    submitConfirmById(requestId, key as "y" | "n" | "a");
  });

  // Keep the gate attached to the currently rendered card, not merely its request ID.
  function recallDetailsOpen(id: string): boolean {
    const inlinePostTurn = chat.model.blocks.some((block) =>
      block.kind === "confirm" && block.requestId === id && (block.confirmKind === "work_record_post_turn" || block.confirmKind === "project_worker_requirement_verification"));
    const container = perspective === "night" && !inlinePostTurn ? confirmGlass : chatEl;
    for (const card of container.querySelectorAll<HTMLElement>("[data-work-card-recall]")) {
      const button = Array.from(card.querySelectorAll<HTMLButtonElement>("[data-confirm][data-id]"))
        .find(candidate => candidate.dataset.id === id);
      if (button) return card.querySelector<HTMLDetailsElement>("details.unified-confirm-details")?.open === true;
    }
    return false;
  }

  // Native toggle does not bubble; capture it for both existing confirmation surfaces.
  for (const container of [chatEl, confirmGlass]) {
    container.addEventListener("toggle", (event) => {
      const details = event.target;
      if (!(details instanceof HTMLDetailsElement)) return;
      const card = details.closest<HTMLElement>("[data-work-card-recall]");
      if (!card) return;
      if (details.classList.contains("unified-recall-batch")) {
        if (!details.open || details.dataset.loaded === "true") return;
        const id = card.querySelector<HTMLButtonElement>("[data-confirm][data-id]")?.dataset.id;
        const overlay = chat.model.confirmOverlay;
        const block = chat.model.blocks.find(b => b.kind === "confirm" && b.requestId === id && !b.resolved);
        const preview = overlay && overlay.requestId === id && !overlay.resolved ? overlay.preview : block?.kind === "confirm" ? block.preview : "";
        const index = Number(details.dataset.recallBatch);
        const target = details.querySelector<HTMLElement>(".unified-recall-batch-content");
        if (!target) return;
        const html = renderWorkCardRecallBatch(preview, index);
        if (html) { target.innerHTML = html; details.dataset.loaded = "true"; }
        return;
      }
      if (!details.classList.contains("unified-confirm-details")) return;
      for (const button of card.querySelectorAll<HTMLButtonElement>('[data-confirm="y"], [data-confirm="a"]')) {
        button.disabled = !details.open;
      }
    }, true);
  }

  // ---- confirm submission helper (shared between click and keyboard) ----
  function submitConfirmById(id: string, choice: "y" | "n" | "a"): void {
    const overlay = chat.model.confirmOverlay;
    const block = chat.model.blocks.find(b => b.kind === "confirm" && b.requestId === id && !b.resolved);
    const preview = overlay && overlay.requestId === id && !overlay.resolved ? overlay.preview : block?.kind === "confirm" ? block.preview : "";
    if (choice !== "n" && block?.kind === "confirm" && block.confirmSubtype === "work_record_reply_excerpt"
        && parseWorkRecordReplyExcerpt(block.preview, block.sessionId || "") === null) {
      setStatus("摘录或会话绑定无法核对，请拒绝该确认");
      return;
    }
    if (choice !== "n" && block?.kind === "confirm" && block.confirmSubtype === "project_worker_requirement_verification"
        && !parseProjectWorkerVerification(block.projectWorkerVerification, block.sessionId || "")) {
      setStatus("项目验收字段或会话绑定无法核对，请拒绝该确认");
      return;
    }    if (choice !== "n" && block?.kind === "confirm" && block.confirmSubtype === "work_record_auto_candidate"
        && !parseWorkRecordAutoCandidate(block.workRecordCandidate, block.sessionId || "")) {
      setStatus("候选或会话绑定无法核对，请拒绝该确认");
      return;
    }
    if (!isConfirmChoiceAllowed(preview, choice, recallDetailsOpen(id))) {
      setStatus("请先展开完整原始预览，再决定是否允许");
      return;
    }
    if (!chat.submitConfirm(id, choice)) {
      const companionComposerPrimary = companionHomeComposerOwnsOrdinaryConfirm({
        mainFocus: projectState.mainFocus,
        perspective,
      });
      if (
        companionComposerPrimary
        && chat.model.confirmOverlay?.requestId === id
        && canSubmitOrdinaryConfirm(chat.model, id)
      ) {
        try {
          client.sendConfirm(id, choice);
          setStatus(choice === "n" ? "已提交拒绝…" : "确认中…");
          renderChat();
          syncPendingConfirmComposerHint();
          syncWorkingVisual();
          return;
        } catch (err) {
          const message = err instanceof Error ? err.message : String(err);
          setStatus(`确认发送失败：${message}`);
          return;
        }
      }
      if (resolveActiveOrdinaryConfirmRequestId(chat.model) === id && !canSubmitOrdinaryConfirm(chat.model, id)) {
        setStatus("确认已提交，等待服务端…");
      } else {
        setStatus("请点最新一张工具确认卡");
      }
      return;
    }
    try {
      client.sendConfirm(id, choice);
      setStatus(choice === "n" ? "已提交拒绝…" : "确认中…");
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setStatus(`确认发送失败：${message}`);
      chat.model.confirmSubmitting = false;
      const block = chat.model.blocks.find((b) => b.kind === "confirm" && b.requestId === id);
      if (block?.kind === "confirm") {
        block.resolved = undefined;
      }
      if (chat.model.confirmOverlay?.requestId === id) {
        chat.model.confirmOverlay = { ...chat.model.confirmOverlay, resolved: undefined };
      }
      renderChat();
      renderConfirmGlass();
      syncWorkingVisual();
    }
  }

  // ---- confirm clicks ----
  const handleConfirmButtonClick = (ev: Event) => {
    const target = ev.target as HTMLElement | null;
    const btn = target?.closest<HTMLButtonElement>("[data-confirm]");
    if (!btn || btn.disabled) return;
    const id = btn.dataset.id;
    const choice = btn.dataset.confirm as "y" | "n" | "a" | undefined;
    if (!id || !choice) return;
    submitConfirmById(id, choice);
  };
  chatEl.addEventListener("click", handleConfirmButtonClick);
  composerDock.addEventListener("click", (ev) => {
    const jump = (ev.target as HTMLElement)?.closest<HTMLButtonElement>("[data-pending-confirm-jump]");
    if (jump) {
      ev.preventDefault();
      scrollPendingConfirmIntoView();
      return;
    }
    handleConfirmButtonClick(ev);
  });

  // ---- confirm glass clicks (night perspective) ----
  confirmGlass.addEventListener("click", (ev) => {
    const target = ev.target as HTMLElement | null;
    const btn = target?.closest<HTMLButtonElement>("[data-confirm]");
    if (!btn || btn.disabled) return;
    const id = btn.dataset.id;
    const choice = btn.dataset.confirm as "y" | "n" | "a" | undefined;
    if (!id || !choice) return;
    submitConfirmById(id, choice);
    syncWorkingVisual();
  });

  // ---- project sidebar events ----

  // Icon bar: each tab owns sidebar + main stage
  projectEls.iconBar.addEventListener("click", (ev) => {
    const btn = (ev.target as HTMLElement).closest<HTMLButtonElement>(".sidebar-icon-btn");
    if (!isRailTab(btn?.dataset.panel)) return;
    selectRailTab(btn.dataset.panel);
  });

  // Phase 27 — Services panel actions
  projectEls.servicesPanel.addEventListener("click", (ev) => {
    const btn = (ev.target as HTMLElement).closest<HTMLButtonElement>("[data-action]");
    if (!btn?.dataset.action) return;
    const action = btn.dataset.action;
    if (action === "toggle-services") {
      projectState.servicesCollapsed = !projectState.servicesCollapsed;
      renderSidebar();
      return;
    }
    if (action === "services-refresh") {
      refreshServices();
      return;
    }
    if (action === "services-prune") {
      try {
        client.pruneServices();
      } catch (err) {
        projectState.servicesError = err instanceof Error ? err.message : String(err);
        renderSidebar();
      }
      return;
    }
    if (action === "service-stop") {
      const name = btn.dataset.serviceName;
      if (!name) return;
      try {
        client.stopService(name);
      } catch (err) {
        projectState.servicesError = err instanceof Error ? err.message : String(err);
        renderSidebar();
      }
      return;
    }
    if (action === "service-remove") {
      const name = btn.dataset.serviceName;
      if (!name) return;
      if (projectState.servicesLogName === name) {
        projectState.servicesLogName = "";
        projectState.servicesLogText = "";
      }
      try {
        client.pruneServices(name);
      } catch (err) {
        projectState.servicesError = err instanceof Error ? err.message : String(err);
        renderSidebar();
      }
      return;
    }
    if (action === "service-logs") {
      const name = btn.dataset.serviceName;
      if (!name) return;
      projectState.servicesLogName = name;
      projectState.servicesLogText = "";
      renderSidebar();
      try {
        client.fetchServiceLogs(name, 40);
      } catch (err) {
        projectState.servicesError = err instanceof Error ? err.message : String(err);
        renderSidebar();
      }
    }
  });

  projectEls.terminalsPanel.addEventListener("click", (ev) => {
    const btn = (ev.target as HTMLElement).closest<HTMLButtonElement>("[data-action]");
    if (!btn?.dataset.action) return;
    if (btn.dataset.action === "terminal-open") {
      const sessionId = btn.dataset.terminalId;
      if (sessionId) openTerminalDetails(sessionId);
      return;
    }
    if (btn.dataset.action === "terminal-details-close") {
      closeTerminalDetails();
      return;
    }
    if (btn.dataset.action === "terminal-output-refresh") {
      const sessionId = btn.dataset.terminalId;
      if (sessionId && projectState.terminalDetails?.session_id === sessionId) {
        requestTerminalOutput(sessionId, projectState.terminalOutputCursor);
      }
      return;
    }
    if (btn.dataset.action === "terminal-copy-output") {
      const sessionId = btn.dataset.terminalId;
      if (!sessionId || sessionId !== projectState.terminalDetails?.session_id) return;
      const text = formatTerminalOutputForDisplay(projectState.terminalOutput);
      if (!text) return;
      const write = navigator.clipboard?.writeText(text);
      if (!write) {
        setStatus("当前环境不支持复制");
        return;
      }
      void write.then(
        () => setStatus("已复制终端输出"),
        () => setStatus("复制终端输出失败"),
      );
      return;
    }
    if (btn.dataset.action === "terminal-close" || btn.dataset.action === "terminal-dismiss") {
      const sessionId = btn.dataset.terminalId;
      if (!sessionId) return;
      try {
        client.closeTerminal(sessionId, `terminal-close-${Date.now()}`, false, {
          userInitiated: true,
          dismissIfInactive: true,
        });
      } catch (err) {
        projectState.terminalOutputError = err instanceof Error ? err.message : String(err);
        renderSidebar();
      }
      return;
    }
    if (btn.dataset.action === "terminals-prune") {
      try {
        closeTerminalDetails();
        client.pruneTerminals(true, `terminal-prune-${Date.now()}`);
      } catch (err) {
        projectState.terminalsError = err instanceof Error ? err.message : String(err);
        renderSidebar();
      }
      return;
    }
    if (btn.dataset.action === "toggle-terminals") {
      projectState.terminalsCollapsed = !projectState.terminalsCollapsed;
      renderSidebar();
      scheduleTerminalPoll();
      return;
    }
    if (btn.dataset.action === "toggle-ended-terminals") {
      projectState.terminalsEndedCollapsed = !projectState.terminalsEndedCollapsed;
      if (projectState.terminalsEndedCollapsed) projectState.terminalsEndedShowAll = false;
      renderSidebar();
      return;
    }
    if (btn.dataset.action === "terminals-show-all-ended") {
      projectState.terminalsEndedCollapsed = false;
      projectState.terminalsEndedShowAll = true;
      renderSidebar();
      return;
    }
    if (btn.dataset.action === "terminals-refresh") {
      refreshTerminals();
    }
  });

  // Overlay back button
  projectEls.overlayBackBtn.addEventListener("click", () => {
    selectRailTab("now");
  });

  // Sidebar: jump to the current turn's process
  sidebarEl.addEventListener("click", (ev) => {
    const target = ev.target;
    const el = target instanceof Element ? target : target instanceof Node ? target.parentElement : null;
    const btn = el?.closest<HTMLButtonElement>('[data-action="jump-turn-process"]');
    if (!btn || !sidebarEl.contains(btn)) return;
    ev.preventDefault();
    jumpToCurrentTurnProcess();
  });

  projectEls.taskFlow.addEventListener("click", (ev) => {
    const target = ev.target as HTMLElement;
    const flowBtn = target.closest<HTMLButtonElement>("[data-flow-stage]");
    if (flowBtn?.dataset.flowStage) {
      projectState.flowPreviewStage = flowBtn.dataset.flowStage as FlowStage;
      renderSidebar();
      return;
    }
    const btn = target.closest<HTMLButtonElement>("[data-action]");
    if (btn?.dataset.action) {
      switch (btn.dataset.action) {
        case "open-artifact-doc": {
          const path = btn.dataset.artifactPath;
          if (!path) return;
          openDocument(path);
          return;
        }
        case "direct-implement":
          startDirectImplementFromUi();
          return;
        case "run-verify":
          runProjectVerifyFromUi();
          return;
        case "flow-return":
          projectState.flowPreviewStage = null;
          renderSidebar();
          return;
        case "confirm-scope":
          projectCallbacks.onScopeConfirm();
          return;
        case "confirm-design":
          projectCallbacks.onDesignConfirm();
          return;
        case "open-projects":
          selectRailTab("projects");
          return;
        case "open-now":
          selectRailTab("now");
          return;
        case "new-project":
          projectCallbacks.onNewProject();
          return;
        case "accept-milestone":
          projectCallbacks.onMilestoneAccept();
          return;
        case "stop-turn":
          projectCallbacks.onStopTurn();
          return;
        case "jump-review-summary":
          jumpToReviewSummary();
          return;
        case "toggle-turn-evidence":
          projectState.turnEvidenceExpanded = !projectState.turnEvidenceExpanded;
          renderSidebar();
          return;
        case "turn-evidence-jump": {
          const callId = btn.dataset.callId;
          if (!callId) return;
          jumpToCurrentActivity(callId);
          return;
        }
        case "turn-evidence-terminal": {
          const sessionId = btn.dataset.terminalId;
          if (!sessionId) return;
          if (!projectState.terminalSessions.some((item) => item.session_id === sessionId)) {
            refreshTerminals();
            setStatus("正在同步本回合的终端会话…");
            return;
          }
          openTerminalDetails(sessionId);
          return;
        }
        default:
          break;
      }
    }
  });

  projectEls.goalCard.addEventListener("click", (ev) => {
    const target = ev.target as HTMLElement;
    const btn = target.closest<HTMLButtonElement>("[data-action]");
    if (!btn?.dataset.action) return;
    switch (btn.dataset.action) {
      case "open-projects":
        projectCallbacks.onOpenProjects();
        return;
      case "new-project":
        projectCallbacks.onNewProject();
        return;
      case "confirm-scope":
        projectCallbacks.onScopeConfirm();
        return;
      case "confirm-design":
        projectCallbacks.onDesignConfirm();
        return;
      case "open-scope-doc":
        openDocument("SCOPE.md");
        return;
      case "direct-implement":
        startDirectImplementFromUi();
        return;
      case "run-verify":
        runProjectVerifyFromUi();
        return;
      case "jump-turn-process":
        jumpToCurrentTurnProcess();
        return;
      case "jump-review-summary":
        jumpToReviewSummary();
        return;
        case "stop-turn":
          projectCallbacks.onStopTurn();
          return;
        default:
        return;
    }
  });

  function handleRailListClick(ev: Event): void {
    const target = ev.target as HTMLElement;

    if (target.closest("#overlay-new-thread-btn")) {
      projectCallbacks.onNewThread();
      return;
    }
    if (target.closest("[data-action='refresh-threads']")) {
      refreshProjectThreads();
      return;
    }

    const threadBtn = target.closest<HTMLButtonElement>(".overlay-thread-item");
    if (threadBtn?.dataset.threadId && !threadBtn.disabled) {
      projectCallbacks.onOpenThread(threadBtn.dataset.threadId);
      return;
    }

    if (target.closest('[data-action="external-import-pick"]')) {
      void (async () => {
        const api = window.myAgentDesktop;
        if (!api?.pickDirectory) return;
        const picked = await api.pickDirectory();
        if (!picked) return;
        projectState.externalImportPath = picked;
        const input = document.querySelector<HTMLInputElement>("#external-project-path");
        if (input) input.value = picked;
        renderSidebar();
      })();
      return;
    }
    if (target.closest('[data-action="external-import-scan"]')) {
      projectCallbacks.onExternalImportScan(projectState.externalImportPath.trim(), projectState.externalImportProjectId.trim());
      return;
    }
    if (target.closest('[data-action="external-import-apply"]')) {
      const scan = projectState.externalImportScan;
      if (scan) projectCallbacks.onExternalImportApply(projectState.externalImportPath.trim(), scan.project_id);
      return;
    }
    if (target.closest('[data-action="external-import-cancel"]')) {
      projectCallbacks.onExternalImportCancel();
      return;
    }

    const projectBtn = target.closest<HTMLButtonElement>(".overlay-project-item:not(.overlay-thread-item)");
    if (projectBtn?.dataset.projectId && !projectBtn.disabled) {
      const pid = projectBtn.dataset.projectId;
      const item = projectState.projects.find((p) => p.id === pid);
      if (item && item.sessionId && !item.isCurrent) {
        projectState.switchConfirmTarget = item;
        renderSidebar();
      } else {
        projectCallbacks.onProjectSwitch(pid);
        return;
      }
      return;
    }

    if (target.closest("#overlay-switch-confirm")) {
      const confirmBtn = target.closest<HTMLButtonElement>("#overlay-switch-confirm-btn");
      const cancelBtn = target.closest<HTMLButtonElement>("#overlay-switch-cancel-btn");
      if (confirmBtn && projectState.switchConfirmTarget) {
        const targetId = projectState.switchConfirmTarget.id;
        projectState.switchConfirmTarget = null;
        projectCallbacks.onProjectSwitch(targetId);
      } else if (confirmBtn && projectState.switchOverlay) {
        projectCallbacks.onProjectSwitchConfirm();
      } else if (cancelBtn) {
        projectState.switchConfirmTarget = null;
        projectCallbacks.onProjectSwitchCancel();
      }
      return;
    }

    if (target.closest("#overlay-doc-rename-input")) return;
    const deleteConfirm = target.closest<HTMLButtonElement>("[data-action='doc-delete-confirm']");
    if (deleteConfirm) {
      confirmDeleteDoc();
      return;
    }
    const deleteCancel = target.closest<HTMLButtonElement>("[data-action='doc-delete-cancel']");
    if (deleteCancel) {
      projectState.deleteConfirmPath = "";
      refreshDocsSidebar();
      return;
    }
    const moreBtn = target.closest<HTMLButtonElement>("[data-doc-more]");
    if (moreBtn) {
      ev.preventDefault();
      ev.stopPropagation();
      const path = moreBtn.dataset.docMore || moreBtn.closest<HTMLElement>(".overlay-doc-item")?.dataset.docPath;
      if (path) {
        const rect = moreBtn.getBoundingClientRect();
        showDocCatalogMenu(path, rect.left, rect.bottom + 4);
      }
      return;
    }
    const docRow = target.closest<HTMLElement>(".overlay-doc-item, .doc-catalog-item");
    if (docRow?.dataset.docPath && !docRow.classList.contains("is-renaming")) {
      openDocument(docRow.dataset.docPath);
      return;
    }

    if (target.closest("#overlay-new-doc-btn") || target.closest('[data-action="overlay-new-doc"]')) {
      createProjectDocFromTitle();
      renderSidebar();
    }
  }

  projectEls.overlayBody.addEventListener("click", handleRailListClick);
  projectEls.taskFlow.addEventListener("click", handleRailListClick);

  documentEl.addEventListener("click", (ev) => {
    const target = ev.target as HTMLElement;
    const moreBtn = target.closest<HTMLButtonElement>("[data-doc-more]");
    if (moreBtn) {
      ev.preventDefault();
      ev.stopPropagation();
      const path = moreBtn.dataset.docMore || moreBtn.closest<HTMLElement>(".doc-catalog-item")?.dataset.docPath;
      if (path) {
        const rect = moreBtn.getBoundingClientRect();
        showDocCatalogMenu(path, rect.left, rect.bottom + 4);
      }
      return;
    }
    if (target.closest("#overlay-doc-rename-input")) return;
    if (target.closest("[data-action=\'doc-delete-confirm\']")) { confirmDeleteDoc(); return; }
    if (target.closest("[data-action=\'doc-delete-cancel\']")) {
      projectState.deleteConfirmPath = "";
      refreshDocsSidebar();
      return;
    }
    const docBtn = target.closest<HTMLElement>(".overlay-doc-item, .doc-catalog-item");
    if (docBtn?.dataset.docPath && !docBtn.classList.contains("is-renaming")) {
      openDocument(docBtn.dataset.docPath);
      return;
    }
    const btn = target.closest<HTMLButtonElement>("[data-action]");
    if (!btn?.dataset.action) return;
    const action = btn.dataset.action;
    if (action === "document-back") {
      selectRailTab("now");
      return;
    }
    if (action === "create-doc") {
      createProjectDocFromTitle();
      renderDocumentPane();
      renderSidebar();
      return;
    }
    if (action === "doc-outline-jump") {
      const headingId = btn.dataset.headingId;
      if (!headingId) return;
      const heading = documentEl.querySelector<HTMLElement>(`#${CSS.escape(headingId)}`);
      heading?.scrollIntoView({ block: "start", behavior: "smooth" });
      return;
    }
    if (action === "document-edit") {
      clearDocLeavePrompt();
      projectState.docView = "edit";
      if (!projectState.docDirty) projectState.docEditDraft = projectState.currentDocContent;
      renderDocumentPane();
      return;
    }
    if (action === "document-preview") {
      captureDocDraftFromEditor();
      if (isDocDirty()) {
        requestLeaveDocument(() => {
          projectState.docView = "preview";
          projectState.docDirty = false;
          projectState.docEditDraft = projectState.currentDocContent;
          renderDocumentPane();
        });
        return;
      }
      projectState.docView = "preview";
      renderDocumentPane();
      return;
    }
    if (action === "document-save") {
      saveCurrentDoc();
      return;
    }
    if (action === "document-discard") {
      projectState.docEditDraft = projectState.currentDocContent;
      projectState.docDirty = false;
      clearDocLeavePrompt();
      renderDocumentPane();
      return;
    }
    if (action === "document-save-leave") {
      saveCurrentDoc();
      return;
    }
    if (action === "document-discard-leave") {
      projectState.docEditDraft = projectState.currentDocContent;
      finishDocLeave();
      return;
    }
    if (action === "document-cancel-leave") {
      docLeavePrompt = false;
      docLeaveNext = null;
      renderDocumentPane();
      return;
    }
    if (action === "document-conflict-reload") {
      projectState.docConflictPrompt = false;
      projectState.docDirty = false;
      projectState.docEditDraft = "";
      requestDocRead(projectState.currentDocPath);
      return;
    }
    if (action === "document-conflict-overwrite") {
      saveCurrentDoc(undefined, true);
      return;
    }
    if (action === "document-conflict-cancel") {
      projectState.docConflictPrompt = false;
      renderDocumentPane();
      return;
    }
    if (action === "toggle-docs-chat-split") {
      projectState.docsChatSplit = !projectState.docsChatSplit;
      syncMainFocusView();
      renderDocumentPane();
      return;
    }
    if (action === "doc-ai-draft") {
      const title = projectState.newDocName.trim() || "新文档";
      const folder = projectState.newDocFolder.trim();
      const hint = folder ? `写到 ${folder}/` : "写到项目根目录";
      input.value = `请起草一篇文档「${title}」（${hint}），用 Markdown，写完保存到项目里。`;
      input.focus();
      showNowChat();
      return;
    }
    if (action === "doc-selection-to-ai") {
      const selection = currentDocSelection().trim();
      const path = projectState.currentDocPath;
      if (!path) return;
      if (!selection) {
        setStatus("先选中一段文字，再点「交给 AI」");
        return;
      }
      input.value = `关于文档 ${path} 中的这段：\n\n> ${selection.split("\n").join("\n> ")}\n\n请帮我：`;
      input.focus();
      if (projectState.docsChatSplit) {
        syncMainFocusView();
      } else {
        showNowChat();
      }
      return;
    }
    if (action === "doc-md-heading") {
      wrapDocSelection("\n## ", "\n");
      return;
    }
    if (action === "doc-md-bold") {
      wrapDocSelection("**", "**");
      return;
    }
    if (action === "doc-md-list") {
      wrapDocSelection("\n- ", "");
      return;
    }
    if (action === "doc-md-code") {
      wrapDocSelection("`", "`");
      return;
    }
    if (action === "doc-md-link") {
      wrapDocSelection("[", "](https://)");
      return;
    }
  });

  function wrapDocSelection(before: string, after: string): void {
    const editor = documentEl.querySelector<HTMLTextAreaElement>(".unified-document-editor");
    if (!editor) return;
    const start = editor.selectionStart;
    const end = editor.selectionEnd;
    const value = editor.value;
    const selected = value.slice(start, end) || "文本";
    editor.value = value.slice(0, start) + before + selected + after + value.slice(end);
    projectState.docEditDraft = editor.value;
    projectState.docDirty = projectState.docEditDraft !== projectState.currentDocContent;
    syncDocEditChrome();
    const live = documentEl.querySelector<HTMLElement>("[data-live-preview]");
    if (live) {
      void import("../../markdown").then(({ renderMarkdown }) => {
        live.innerHTML = renderMarkdown(editor.value);
        void hydrateMermaid(live);
      }).catch(() => {
        /* ignore */
      });
    }
    editor.focus();
    editor.selectionStart = start + before.length;
    editor.selectionEnd = start + before.length + selected.length;
  }

  documentEl.addEventListener("input", (ev) => {
    if ((ev.target as HTMLElement).closest("#overlay-doc-rename-input")) { handleRailListInput(ev); return; }
    const editor = (ev.target as HTMLElement).closest<HTMLTextAreaElement>(".unified-document-editor");
    if (editor) {
      projectState.docEditDraft = editor.value;
      const dirty = projectState.docEditDraft !== projectState.currentDocContent;
      if (dirty !== projectState.docDirty) {
        projectState.docDirty = dirty;
        syncDocEditChrome();
      }
      const live = documentEl.querySelector<HTMLElement>("[data-live-preview]");
      if (live) {
        try {
          // renderMarkdown is already in scope in this module
          live.innerHTML = renderMarkdown(editor.value) || `<p class="doc-reader-empty">开始编辑以预览</p>`;
          void hydrateMermaid(live);
        } catch { /* ignore */ }
      }
      return;
    }
    const docInput = (ev.target as HTMLElement).closest<HTMLInputElement>("#doc-reader-new-input");
    if (docInput) projectState.newDocName = docInput.value;
    const folderInput = (ev.target as HTMLElement).closest<HTMLInputElement>("#doc-reader-new-input-folder");
    if (folderInput) projectState.newDocFolder = folderInput.value;
    const searchInput = (ev.target as HTMLElement).closest<HTMLInputElement>(".doc-catalog-search");
    if (searchInput) {
      projectState.docSearchQuery = searchInput.value;
      const cursor = searchInput.selectionStart;
      renderDocumentPane();
      const replacement = documentEl.querySelector<HTMLInputElement>(".doc-catalog-search");
      replacement?.focus();
      if (cursor !== null) replacement?.setSelectionRange(cursor, cursor);
    }
  });

  documentEl.addEventListener("keydown", (ev) => {
    if ((ev.target as HTMLElement).closest("#overlay-doc-rename-input") || ev.key === "F2") {
      handleRailListKeydown(ev);
      return;
    }
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "s") {
      if (projectState.docView === "edit" && projectState.mainFocus === "document") {
        ev.preventDefault();
        saveCurrentDoc();
      }
      return;
    }
    if (ev.key !== "Enter") return;
    const docInput = (ev.target as HTMLElement).closest<HTMLInputElement>("#doc-reader-new-input");
    if (!docInput) return;
    createProjectDocFromTitle();
    renderDocumentPane();
    renderSidebar();
  });

  // Overlay: search input + new-doc input
  function handleRailListInput(ev: Event): void {
    const externalPath = (ev.target as HTMLElement).closest<HTMLInputElement>("#external-project-path");
    if (externalPath) {
      projectState.externalImportPath = externalPath.value;
      return;
    }
    const externalId = (ev.target as HTMLElement).closest<HTMLInputElement>("#external-project-id");
    if (externalId) {
      projectState.externalImportProjectId = externalId.value;
      return;
    }
    const searchInput = (ev.target as HTMLElement).closest<HTMLInputElement>("#overlay-project-search");
    if (searchInput) {
      projectState.projectSearchQuery = searchInput.value;
      renderSidebar();
      return;
    }
    const docInput = (ev.target as HTMLElement).closest<HTMLInputElement>("#overlay-new-doc-input, #doc-reader-new-input");
    if (docInput) {
      projectState.newDocName = docInput.value;
      return;
    }
    const folderInput = (ev.target as HTMLElement).closest<HTMLInputElement>("#overlay-new-doc-input-folder, #doc-reader-new-input-folder");
    if (folderInput) {
      projectState.newDocFolder = folderInput.value;
      return;
    }
    const docSearch = (ev.target as HTMLElement).closest<HTMLInputElement>(".doc-catalog-search");
    if (docSearch) {
      projectState.docSearchQuery = docSearch.value;
      renderSidebar();
      if (projectState.mainFocus === "document") renderDocumentPane();
      return;
    }
    const renameInput = (ev.target as HTMLElement).closest<HTMLInputElement>("#overlay-doc-rename-input");
    if (renameInput) projectState.renameDraft = renameInput.value;
  }

  function handleRailListKeydown(ev: KeyboardEvent): void {
    const renameInput = (ev.target as HTMLElement).closest<HTMLInputElement>("#overlay-doc-rename-input");
    if (renameInput) {
      if (ev.key === "Enter") {
        ev.preventDefault();
        commitRenameDoc();
      } else if (ev.key === "Escape") {
        ev.preventDefault();
        cancelRenameDoc();
      }
      return;
    }
    if (ev.key === "F2") {
      const row = (ev.target as HTMLElement).closest<HTMLElement>(".overlay-doc-item");
      if (row?.dataset.docPath) {
        ev.preventDefault();
        startRenameDoc(row.dataset.docPath);
      }
      return;
    }
    if (ev.key !== "Enter") return;
    const docInput = (ev.target as HTMLElement).closest<HTMLInputElement>("#overlay-new-doc-input, #doc-reader-new-input");
    if (!docInput) return;
    createProjectDocFromTitle();
    renderSidebar();
  }

  documentEl.addEventListener("contextmenu", (ev) => {
    const row = (ev.target as HTMLElement).closest<HTMLElement>(".doc-catalog-item");
    if (!row?.dataset.docPath || row.classList.contains("is-renaming")) return;
    ev.preventDefault();
    showDocCatalogMenu(row.dataset.docPath, ev.clientX, ev.clientY);
  });
  documentEl.addEventListener("focusout", (ev) => {
    const input = (ev.target as HTMLElement).closest<HTMLInputElement>("#overlay-doc-rename-input");
    if (!input) return;
    const path = projectState.renamingDocPath;
    window.setTimeout(() => {
      if (input.isConnected && path && projectState.renamingDocPath === path && document.activeElement !== input) commitRenameDoc();
    }, 0);
  });
  projectEls.overlayBody.addEventListener("input", handleRailListInput);
  projectEls.taskFlow.addEventListener("input", handleRailListInput);
  projectEls.overlayBody.addEventListener("keydown", handleRailListKeydown);
  projectEls.taskFlow.addEventListener("keydown", handleRailListKeydown);
  projectEls.overlayBody.addEventListener("focusout", (ev) => {
    const renameInput = (ev.target as HTMLElement).closest<HTMLInputElement>("#overlay-doc-rename-input");
    if (!renameInput) return;
    window.setTimeout(() => {
      if (projectState.renamingDocPath && document.activeElement !== renameInput) {
        commitRenameDoc();
      }
    }, 0);
  });
  projectEls.overlayBody.addEventListener("contextmenu", (ev) => {
    const row = (ev.target as HTMLElement).closest<HTMLElement>(".overlay-doc-item");
    if (!row?.dataset.docPath || row.classList.contains("is-renaming")) return;
    ev.preventDefault();
    showDocCatalogMenu(row.dataset.docPath, ev.clientX, ev.clientY);
  });

  // Change banner actions
  projectEls.changeBanner.addEventListener("click", (ev) => {
    const target = ev.target as HTMLElement;
    const btn = target.closest<HTMLButtonElement>("[data-action]");
    if (!btn?.dataset.action) return;
    const action = btn.dataset.action;
    switch (action) {
      case "confirm-scope":
        projectCallbacks.onScopeConfirm();
        return;
      case "stop-turn":
        projectCallbacks.onStopTurn();
        return;
      case "detect-switch":
        {
          const pid = btn.dataset.projectId;
          if (pid) {
            projectState.detectedProject = null;
            projectCallbacks.onProjectSwitch(pid);
          }
        }
        return;
      case "detect-dismiss":
        projectState.detectedProject = null;
        renderSidebar();
        return;
      case "undo-last": {
        const docUndo = projectState.pendingDocUndo;
        projectState.undoDescription = "";
        projectState.pendingDocUndo = null;
        if (projectState.undoTimerId) { window.clearTimeout(projectState.undoTimerId); projectState.undoTimerId = null; }
        renderSidebar();
        if (docUndo) {
          try { client.undoDoc(docUndo); } catch { /* ignore */ }
        }
        return;
      }
      case "jump-review-summary":
        jumpToReviewSummary();
        return;
    }
    renderSidebar();
  });

  // compat: keep old event wiring working for elements hidden in DOM
  projectEls.pickerRefreshBtn.addEventListener("click", () => projectCallbacks.onRefreshProjects());
  projectEls.switchConfirmBtn.addEventListener("click", () => projectCallbacks.onProjectSwitchConfirm());
  projectEls.switchCancelBtn.addEventListener("click", () => projectCallbacks.onProjectSwitchCancel());

  bindWorkCardResultViewEvents(chatEl, {
    currentSessionId: () => chat.model.sessionId,
    getEntry: (presentationId) => workCardPresentations.get(presentationId),
    send: (request) => {
      request.request_id = crypto.randomUUID();
      client.acknowledgeWorkCardResultView({
        sessionId: request.session_id, presentationId: request.presentation_id,
        cardId: request.card_id, resultId: request.result_id, requirementId: request.requirement_id,
        requestId: request.request_id,
      });
    },
    onExpanded: (detail, entry, request) => workCardResultExcerpts.opened(detail, entry, request),
    onClosed: (detail) => {
      for (const requestId of workCardResultExcerpts.closed(detail)) client.cancelWorkCardResultView(requestId);
    },
    onError: (error) => setStatus(`结果查看确认未发送：${error instanceof Error ? error.message : String(error)}`),
  });

  bindWorkCardSnapshotViewEvents(chatEl, {
    currentSessionId: () => chat.model.sessionId,
    getEntry: (presentationId) => workCardPresentations.get(presentationId),
    send: (request) => client.acknowledgeWorkCardSnapshotView({sessionId: request.session_id, presentationId: request.presentation_id, cardId: request.card_id}),
    onError: () => setStatus("快照查看确认未发送；请重新展开详情重试。"),
  });

  function hydrateHistoryWorkCards(blocks: ChatBlock[]): void {
    const sessionId = chat.model.sessionId;
    historyCardReadSessionId = sessionId;
    workCardResultExcerpts.setSession(sessionId);
    workCardPresentations.setSession(sessionId);
    const cardIds = new Set<string>();
    for (const block of blocks) {
      if (block.kind !== "work-card-reference") continue;
      const entry = workCardPresentations.reference({ presentationId: block.presentationId, cardId: block.cardId, text: block.text });
      if (entry?.origin === "history" && entry.status === "loading") cardIds.add(entry.cardId);
    }
    for (const cardId of cardIds) {
      if (pendingHistoryCardReads.has(cardId)) continue;
      const outcome = requestHistoryCardRead({
        cardId,
        sessionId,
        currentSessionId: chat.model.sessionId,
        pendingReads: pendingHistoryCardReads,
        registry: workCardPresentations,
        request: (requestedCardId) => client.getWorkCard(requestedCardId),
      });
      if (outcome === "failed") renderChat();
    }
  }

  function settleHistoryCardReads(cardId: string, sessionId: string, card: Record<string, any> | null): void {
    if (sessionId !== chat.model.sessionId || pendingHistoryCardReads.get(cardId) !== sessionId) return;
    pendingHistoryCardReads.delete(cardId);
    for (const entry of workCardPresentations.values()) {
      if (entry.origin === "history" && entry.cardId === cardId && entry.status === "loading") {
        workCardPresentations.resolve(entry.presentationId, sessionId, card);
      }
    }
    renderChat();
  }

  function failHistoryCardReads(cardId: string, sessionId: string, message: string): void {
    if (!failPendingHistoryCardRead({
      cardId, sessionId, currentSessionId: chat.model.sessionId,
      pendingReads: pendingHistoryCardReads, registry: workCardPresentations, message,
    })) return;
    renderChat();
  }
  // ---- server events ----
  const off = client.onEvent((event: ServerEvent) => {
    switch (event.type) {
      case "client.connection.closed":
        clearApprovalPreferenceTimeout();
        clearPendingHistoryCardReads(pendingHistoryCardReads);
        chat.handleEvent(event);
        autoRecognition = { sessionId: "", enabled: false, pending: "", action: "", error: false };
        approvalPreferences = { ...approvalPreferences, known: false, pending: false, available: false, error: true, message: "连接已关闭，无法确认当前实际审批档位；切换已禁用。" };
        refreshApprovalSettingsView();
        refreshTopbar();
        break;
      case "user.message.persisted":
        chat.handleEvent(event);
        break;
      case "approval_preferences.state":
      case "approval_preferences.done":
        clearApprovalPreferenceTimeout();
        const stateKnown = event.status === "ok" && event.state_known !== false;
        approvalPreferences = {
          mode: stateKnown ? event.approval_mode : approvalPreferences.mode,
          savedMode: stateKnown ? event.saved_approval_mode : approvalPreferences.savedMode,
          known: stateKnown, pending: false, available: stateKnown,
          error: event.status !== "ok" || !stateKnown,
          message: stateKnown ? ""
            : (event.message || "当前实际档位未知，无法确认或切换。"),
        };
        refreshApprovalSettingsView();
        break;
      case "llm_role_models.state":
      case "llm_role_models.done": {
        if (event.status === "ok" && event.preferences && event.models) {
          globalRoleModels.preferences = event.preferences;
          globalRoleModels.models = event.models;
          globalRoleModels.error = "";
        } else if (event.status !== "ok") {
          globalRoleModels.error = event.message || "全局模型选择读取或保存失败，当前选择未确认。";
        }
        if (event.type === "llm_role_models.done" || !globalRoleModels.pendingRole) globalRoleModels.pendingRole = "";
        refreshTopbar();
        break;
      }
      case "work-record.auto-recognition.state":
        if (event.session_id !== chat.model.sessionId || autoRecognition.pending) break;
        autoRecognition = { sessionId: event.session_id, enabled: event.enabled, pending: "", action: "", error: false };
        refreshTopbar();
        break;
      case "work-record.auto-recognition.done":
        if (event.session_id !== autoRecognition.sessionId || event.session_id !== chat.model.sessionId || !autoRecognition.pending
          || event.request_id !== autoRecognition.pending || event.action !== autoRecognition.action) break;
        autoRecognition = { ...autoRecognition, enabled: event.enabled, pending: "", action: "", error: event.status !== "ok" };
        refreshTopbar();
        break;
      case "work-record.post-turn.result":
        if (event.session_id !== chat.model.sessionId) break;
        if (!chat.handleEvent(event)) break;
        // The ordinary turn-end refresh can finish before the asynchronous
        // post-turn store commit. Refresh again only after a confirmed write.
        if (event.outcome === "recorded" || event.outcome === "already_recorded") {
          scheduleCurrentWorkOverviewRefresh();
        }
        break;
      case "goal.state":
        if (event.session_id && event.session_id !== chat.model.sessionId) break;
        if (activeGoalSessionId && event.session_id && event.session_id !== activeGoalSessionId) break;
        if (event.session_id) activeGoalSessionId = event.session_id;
        const goalNotice = renderGoalBudgetNotice(event);
        if (goalNotice) {
          goalBudgetNoticeEl.innerHTML = goalNotice;
          goalBudgetNoticeEl.classList.remove("hidden");
        } else {
          goalBudgetNoticeEl.replaceChildren();
          goalBudgetNoticeEl.classList.add("hidden");
        }
        break;
      case "session.banner":
        if (!shouldApplyProjectEvent(projectState, event.project_id)) break;
        if (event.session_id !== activeGoalSessionId) {
          activeGoalSessionId = event.session_id;
          goalBudgetNoticeEl.replaceChildren();
          goalBudgetNoticeEl.classList.add("hidden");
        }
        // Session list in 当下 follows every new/opened session (debounced via threads timer).
        window.setTimeout(() => refreshSidebarSessions(), 0);
        if ((event.project_id || "") !== projectState.projectId) {
          resetProjectScopedState(projectState);
        }
        if (event.session_id !== chat.model.sessionId) {
          currentEvolutionView = evolutionView(null);
          evolutionHostAvailable = null;
          evolutionBeginPending = false;
          syncShellDevEvolutionView();
          renderedPrints = [];
          renderedSegmentPrints = [];
          projectState.turnEvidence = [];
          projectState.turnEvidenceRunId = "";
          projectState.turnEvidenceTotalCount = 0;
          projectState.turnEvidenceOmittedCount = 0;
          projectState.turnEvidenceExpanded = false;
        }
        chat.handleEvent(event);
        projectState.currentSessionId = event.session_id;
        if (hydrationSessionId && event.session_id === hydrationSessionId) {
          setStatus("正在加载会话…");
        }
        if (event.project_id) {
          projectState.projectId = event.project_id;
          projectState.planStatus = event.project_plan_status ?? "draft";
          syncFreeChatFromSession(true, event.session_id);
          setPerspective("project", "session");
        } else {
          projectState.projectId = "";
          syncFreeChatFromSession(false, event.session_id);
          setPerspective("project", "session");
        }
        updatePlaceholder();
        updateWorkbenchEmpty();
        syncArchivedViewUi();
        syncSandboxWriteScopeBanner(event.write_scope_label);
        syncModeToggleUi(event.turn_mode);
        setStatus(sessionBannerStatus(event.llm_model_label || "Flash", event.turn_mode_label || ""));
        refreshTopbar();
        renderSidebar();
        debouncedListSessions();
        if (event.project_id) {
          refreshProjectThreads();
        }
        break;

      case "session.list":
        sessionsDropdown = client.applySessionListPush(event.sessions);
        sidebarSessionsLoaded = true;
        calibrateCompanionFirstMetFromSessions(sessionsDropdown);
        syncCompanionToBackend();
        renderSidebarSessions();
        renderSidebarProjectRow();
        if (!chat.model.blocks.length && !chat.isWorking()) {
          renderedPrints = [];
          renderedSegmentPrints = [];
          renderChat();
        }
        topbarState.sessionCount = sessionsDropdown.length;
        refreshTopbar();
        if (sessionsOpen) renderSessionsDropdown();
        break;

      case "session.memory":
        topbarState.memoryLabel = `${event.message_count} 条 · ${event.memory_mode_label}`;
        updateTokenBar(event.token_usage, event.token_limit);
        break;

      case "llm.usage":
        recordLlmUsage(event.prompt_tokens, event.cached_tokens, event.cache_ratio);
        break;

      case "evolve.proposals":
        handleProposalsEvent(event.items);
        break;

      case "session.turn_mode.request":
        void (async () => {
          const ok = await showWorkbenchConfirm(
            typeof event.message === "string" && event.message.trim()
              ? event.message.trim()
              : "切换对话模式？",
          );
          if (!ok) return;
          try {
            client.setTurnMode(event.to_mode, {
              confirm: true,
              requestId: event.request_id,
            });
          } catch (err) {
            setStatus(err instanceof Error ? err.message : String(err));
          }
        })();
        break;

      case "memory.proposal.accept.request":
        void (async () => {
          const ok = await showWorkbenchConfirm(
            typeof event.message === "string" && event.message.trim()
              ? event.message.trim()
              : "接受这条记忆提案？",
          );
          if (!ok) return;
          try {
            client.acceptProposal(event.proposal_id, {
              confirm: true,
              requestId: event.request_id,
            });
          } catch (err) {
            setStatus(err instanceof Error ? err.message : String(err));
          }
        })();
        break;

      case "memory.proposal.accept.done":
        proposalsState.expandOpen = false;
        syncProposalsState();
        refreshTopbar();
        renderProposalsPanel();
        if (memoryShelfState.expandOpen) {
          client.listMemories();
        }
        break;

      case "evolve.memories":
        handleMemoriesEvent(event.items);
        break;

      case "evolution.bound":
        if (event.conversation_id !== chat.model.sessionId) break;
        evolutionBeginPending = false;
        evolutionHostAvailable = true;
        currentEvolutionView = { active: true, evolution_id: event.evolution_id, label: "范围已绑定，正在读取状态…", canStop: false, currentCandidate: null, openChecks: [] };
        shellDevFeedback("当前会话已绑定固定演化范围。", "ok");
        syncShellDevEvolutionView();
        void requestEvolutionAction("inspect");
        break;

      case "evolution.denied":
        if (event.conversation_id !== chat.model.sessionId) break;
        evolutionBeginPending = false;
        syncShellDevEvolutionView();
        shellDevFeedback("未确认演化范围；当前会话没有新增授权。", "neutral");
        break;

      case "evolution.state": {
        if (event.conversation_id !== chat.model.sessionId) break;
        evolutionHostAvailable = true;
        const view = evolutionView(event.projection as EvolutionProjection);
        syncShellDevEvolutionView(view);
        shellDevFeedback([view.label, ...view.openChecks].join(" · "), view.active ? "ok" : "neutral");
        break;
      }

      case "evolution.stop.result":
        if (event.conversation_id !== chat.model.sessionId) break;
        shellDevFeedback("停止结果：" + String(event.result.status ?? "未知"), event.result.status === "succeeded" ? "ok" : "error");
        void requestEvolutionAction("inspect");
        break;

      case "context.switch.request":
        projectState.switchInProgress = true;
        projectState.pendingPickerId = (event.project_id || "").trim();
        renderSidebar();
        break;

      case "context.switch.done":
        if (event.applied === false || event.choice !== "y") {
          projectState.switchInProgress = false;
          projectState.pendingPickerId = "";
          renderSidebar();
          break;
        }
        resetProjectScopedState(projectState);
        projectState.switchInProgress = false;
        projectState.switchOverlay = null;
        projectState.pendingPickerId = "";
        projectState.projectId = (event.project_id || "").trim();
        projectState.currentSessionId = event.session_id || "";
        projectState.railTab = "now";
        projectState.overlayPanel = null;
        freeChatActive = !projectState.projectId;
        client.listProjects();
        if (projectState.projectId) refreshProjectThreads();
        setMainFocus("chat");
        updatePlaceholder();
        updateWorkbenchEmpty();
        composerWire.syncSendEnabled();
        refreshTopbar();
        setStatus(userFacingContextStatus(event.message));
        break;

      case "project.release.accepted":
        projectState.milestoneAccepted = Boolean(event.release_acceptance.accepted);
        projectState.milestoneAcceptedAt = event.release_acceptance.accepted_at ?? null;
        renderSidebar();
        setStatus("发布确认已保存");
        break;

      case "project.import.scan":
        projectState.externalImportScan = event.scan;
        projectState.externalImportProjectId = event.scan.project_id;
        setStatus("外部项目扫描完成，请确认是否原地接入");
        renderSidebar();
        break;

      case "project.import.applied":
        projectState.externalImportScan = null;
        setStatus("外部项目已接入，文件仍在原目录");
        client.refreshProject();
        client.listProjects();
        break;

      case "project.import.cancelled":
        projectState.externalImportScan = null;
        renderSidebar();
        break;

      case "project.state":
        if (!applyProjectStateEvent(projectState, event)) break;
        if ((event.project_id || "") !== lastVerifyProjectId) {
          lastVerify = null;
          lastVerifyProjectId = event.project_id || "";
        }
        projectGitBranch = event.git_branch || "";
        if (projectState.projectId) {
          freeChatActive = false;
        }
        if (!perspectiveLocked) setPerspective("project", "session");
        updatePlaceholder();
        updateWorkbenchEmpty();
        renderSidebar();
        if (projectState.mainFocus === "projects") renderProjectsPane();
        if (projectState.mainFocus === "document") renderDocumentPane();
        if (projectState.projectId) {
          refreshTopbar();
          refreshServices();
          refreshProjectThreads();
        }
        break;

      case "work-records.schedule.dispatch.done":
        if (event.session_id !== chat.model.sessionId) break;
        setStatus(event.message);
        if (event.status === "applied" && currentWorkOverviewResult?.selected_project_ids?.length) {
          queryStructuredCurrentWorkOverview(undefined, event.session_id, undefined,
            currentWorkOverviewResult.selected_project_ids);
        } else if (event.status === "applied") {
          scheduleCurrentWorkOverviewRefresh();
        }
        break;

      case "work-record.control.done":
        if (event.session_id !== chat.model.sessionId) break;
        setStatus(event.message);
        if (event.status === "applied") {
          if (currentWorkOverviewResult?.aggregation_scope === "selected_project_sessions"
              && currentWorkOverviewResult.selected_project_ids?.length) {
            queryStructuredCurrentWorkOverview(undefined, event.session_id, undefined,
              currentWorkOverviewResult.selected_project_ids);
          } else scheduleCurrentWorkOverviewRefresh();
        } else refreshTopbar();
        break;

      case "work-records.overview.result": {
        if (event.session_id !== chat.model.sessionId) break;
        const request = currentWorkOverviewRequests.get(event.request_id);
        currentWorkOverviewRequests.delete(event.request_id);
        if (!request || request.sessionId !== event.session_id || currentWorkOverviewActiveRequest !== event.request_id) break;
        if (request.mergedHistory) {
          const page = event.history_page;
          const expectedProjects = request.projectIds ?? [];
          const sources = event.history_sources;
          const validSources = event.history_scope === "selected_projects_history" && page?.scope === "selected_projects_history"
            && (event.state === "visible" || event.state === "no_records") && Array.isArray(sources) && sources.length === expectedProjects.length
            && JSON.stringify([...sources.map(source => source.project_id)].sort()) === JSON.stringify([...expectedProjects].sort())
            && new Set(sources.map(source => source.project_id)).size === expectedProjects.length
            && sources.every(source => typeof source.project_id === "string" && typeof source.session_id === "string" && source.session_id.length > 0
              && Number.isSafeInteger(source.revision) && source.revision >= 0)
            && page.page_size === 10 && Number.isSafeInteger(page.offset) && page.offset >= 0
            && typeof page.has_more === "boolean" && (page.has_more ? typeof page.next_cursor === "string" && page.next_cursor.length > 0 : page.next_cursor === null);
          const sourceByProject = new Map((Array.isArray(sources) ? sources : []).map(source => [source.project_id, source]));
          const validRows = Array.isArray(event.history) && event.history.length <= (page?.page_size ?? 0)
            && (!page?.has_more || event.history.length === page.page_size)
            && (event.state !== "no_records" || event.history.length === 0)
            && event.history.every(row => sourceByProject.get(row.project_id)?.session_id === row.session_id);
          if (!validSources || !page || !validRows || (request.mergedHistory.cursor !== undefined
              && (!mergedHistory.page?.has_more || mergedHistory.page.next_cursor !== request.mergedHistory.cursor
                || JSON.stringify([...(mergedHistory.sources.map(source => `${source.project_id}\u0000${source.session_id}\u0000${source.revision}`))].sort())
                  !== JSON.stringify([...(event.history_sources ?? []).map(source => `${source.project_id}\u0000${source.session_id}\u0000${source.revision}`)].sort())))) {
            clearMergedHistoryState();
            setStatus(event.state === "error" || event.state === "expired" ? "统一历史授权或来源版本已变化；已清除旧游标，请重新请求首屏。" : "统一历史回执范围无效，已丢弃。");
          } else {
            const appended = appendMergedHistoryPage(mergedHistory, page, event.history_sources!, event.history!, request.mergedHistory.cursor);
            if (!appended) {
              clearMergedHistoryState();
              setStatus("统一历史页游标、来源版本或身份连续性无效；已清除分页内容，请重新加载。");
            } else {
              mergedHistory = appended as MergedHistoryState;
              setStatus("已加载所选项目统一历史页；来源会话与记录版本已核对。");
            }
          }
          refreshTopbar();
          break;
        }
        if (request.history) {
          currentWorkOverviewDetailLoadingCursor = null;
          const current = currentWorkOverviewResult;
          // A declined per-page authorization is expected, not a stale projection.
          // Preserve the already-authorized overview so retrying the history button
          // requests that page again instead of silently triggering a new overview read.
          if (event.state === "error" && event.reason === "multi_project_source_unavailable") {
            setStatus("未授权读取此要求的历史页；已保留当前已授权概况。");
            refreshTopbar();
            break;
          }
          const validHistory = event.state === "visible" && event.history_page?.scope === "this_requirement_only"
            && event.history_project_id === request.history.projectId && event.history_item_id === request.history.itemId
            && event.history_requirement_id === request.history.requirementId && event.source?.project_id === request.history.projectId
            && Array.isArray(event.history) && current?.selected_project_ids?.includes(request.history.projectId)
            && current.groups.some(group => group.kind === "project" && group.id === request.history.projectId
              && group.source_session_id === event.source.session_id && group.source_revision === event.source.revision);
          if (!validHistory || !current) {
            currentWorkOverviewUnavailable = true;
            setStatus(event.state === "expired" ? "项目来源版本已变化；历史游标失效，正在重新核实来源。" : "单要求历史页不可用或来源版本不匹配；当前仅显示已核实内容。");
            refreshTopbar();
            queryStructuredCurrentWorkOverview(undefined, event.session_id, undefined, request.projectIds);
            break;
          }
          const group = current.groups.find(row => row.kind === "project" && row.id === request.history!.projectId)!;
          const targetItem = group.items.find(row => row.item_id === request.history!.itemId);
          const requirement = targetItem?.requirements.find(row => row.requirement_id === request.history!.requirementId);
          if (!requirement) { currentWorkOverviewUnavailable = true; refreshTopbar(); break; }
          const existing = requirement.history ?? [];
          const incoming = event.history!;
          const mergedHistory = request.history && event.history_page!.offset > 0 ? [...existing, ...incoming] : incoming;
          currentWorkOverviewResult = { ...current, groups: current.groups.map(row => row !== group ? row : {
            ...group, items: group.items.map(item => item !== targetItem ? item : { ...item, requirements: item.requirements.map(req => req !== requirement ? req : {
              ...req, history: mergedHistory, history_page: event.history_page,
            }) }),
          }) };
          currentWorkOverviewUnavailable = false;
          setStatus("已加载单个要求范围内的来源历史页；不代表项目或全局历史完整。");
          refreshTopbar();
          break;
        }
        if (request.projectIds) {
          const selected = request.projectIds;
          if (event.aggregation_scope !== "selected_project_sessions"
              || !Array.isArray(event.selected_project_ids)
              || JSON.stringify([...event.selected_project_ids].sort()) !== JSON.stringify([...selected].sort())) {
            setStatus("多项目概况的授权来源回执无法核对；结果已丢弃。");
            currentWorkOverviewUnavailable = true;
            refreshTopbar();
            break;
          }
          if (event.state !== "visible" && event.state !== "no_records") {
            currentWorkOverviewUnavailable = true;
            setStatus("所选项目来源未能完整核对；保留当前会话概况，未用部分远端结果替代。");
            refreshTopbar();
            break;
          }
          const local = currentSessionWorkOverviewResult?.session_id === event.session_id
            ? currentSessionWorkOverviewResult : null;
          const localGroups = (local?.groups ?? []).filter(group => !selected.includes(group.id));
          const localVisibleCount = local?.groups.filter(group => group.visibility === "visible").length ?? 0;
          const localOmittedGroups = local?.page.has_more
            ? Math.max(0, local.page.total_visible_groups - localVisibleCount) : 0;
          const groups = [...localGroups, ...event.groups.filter(group => !localGroups.some(existing => existing.kind === group.kind && existing.id === group.id))];
          const visibleGroups = groups.filter(group => group.visibility === "visible");
          const collapsedGroups = groups.filter(group => group.visibility === "collapsed");
          const visibleItems = visibleGroups.reduce((sum, group) => sum + group.item_count, 0);
          const collapsedItems = collapsedGroups.reduce((sum, group) => sum + group.item_count, 0);
          currentWorkOverviewResult = {
            ...event,
            session_id: event.session_id,
            groups: visibleGroups,
            unread_status: "unknown",
            visibility_limitations: [...new Set([...(event.visibility_limitations ?? []), ...(local?.visibility_limitations ?? []), "mixed_current_and_confirmed_project_snapshots", ...(localOmittedGroups > 0 ? [`local_groups_omitted:${localOmittedGroups}`] : [])])],
            unread_important_results: local?.unread_important_results ?? [],
            unread_important_results_total: local?.unread_important_results_total,
            unread_important_results_truncated: local?.unread_important_results_truncated,
            work_record_revision: local?.work_record_revision,
            visibility_counts: { visible_groups: visibleGroups.length, visible_items: visibleItems,
              collapsed_groups: collapsedGroups.length, collapsed_items: collapsedItems },
            page: { ...event.page, total_groups: visibleGroups.length, total_visible_groups: visibleGroups.length,
              total_visible_items: visibleItems, total_collapsed_groups: collapsedGroups.length,
              total_collapsed_items: collapsedItems, has_more: false, next_cursor: null },
          };
          currentWorkOverviewUnavailable = false;
          currentWorkOverviewLoadingMore = false;
          currentWorkOverviewOpen = true;
          refreshTopbar();
          break;
        }
        if (shouldFallbackOverviewDetailPaging(request.detailPaging, event.state, event.reason)) {
          currentWorkOverviewDetailPagingSupported = false;
          currentWorkOverviewLoadingMore = false;
          currentWorkOverviewDetailLoadingCursor = null;
          queryStructuredCurrentWorkOverview(undefined, event.session_id);
          break;
        }
        if (request.detail) {
          currentWorkOverviewDetailLoadingCursor = null;
          const current = currentWorkOverviewResult;
          const detailPage = event.detail_page;
          const detailTargetMatches = !!detailPage && detailPage.kind === request.detail.kind
            && detailPage.group_kind === request.detail.groupKind && detailPage.group_id === request.detail.groupId
            && detailPage.item_id === (request.detail.itemId ?? null);
          if (shouldRestartOverviewAfterExpiredCursor(event.state, request.cursor)
            || event.state === "error"
            || (current !== null && event.snapshot_id !== current.snapshot_id)) {
            currentWorkOverviewUnavailable = true;
            refreshTopbar();
            queryStructuredCurrentWorkOverview(undefined, event.session_id);
            break;
          }
          const merged = current && detailTargetMatches ? mergeCurrentWorkOverviewDetailPage(current, event) : null;
          if (!merged) {
            setStatus("当前工作详情页无法与本次概况安全合并；正在重新查询完整概况。");
            currentWorkOverviewUnavailable = true;
            refreshTopbar();
            queryStructuredCurrentWorkOverview(undefined, event.session_id);
            break;
          }
          currentWorkOverviewResult = merged;
          currentWorkOverviewUnavailable = false;
          refreshTopbar();
          flushQueuedCurrentWorkOverviewRefresh();
          break;
        }
        if (shouldRestartOverviewAfterExpiredCursor(event.state, request.cursor)) {
          currentWorkOverviewLoadingMore = false;
          currentWorkOverviewResult = event;
          refreshTopbar();
          queryStructuredCurrentWorkOverview(undefined, event.session_id);
          break;
        }
        if (request.cursor && currentWorkOverviewResult
          && event.snapshot_id !== currentWorkOverviewResult.snapshot_id) {
          currentWorkOverviewLoadingMore = false;
          currentWorkOverviewResult = { ...event, state: "expired", groups: [], unread_status: "unknown", unread_important_results: [] };
          refreshTopbar();
          queryStructuredCurrentWorkOverview(undefined, event.session_id);
          break;
        }
        if (request.cursor && currentWorkOverviewResult && event.state === "visible") {
          const appended = appendCurrentWorkOverviewPage(currentWorkOverviewResult, event);
          if (!appended) {
            currentWorkOverviewLoadingMore = false;
            queryStructuredCurrentWorkOverview(undefined, event.session_id);
            break;
          }
          currentWorkOverviewResult = appended;
        } else {
          currentWorkOverviewResult = { ...event, groups: event.groups.filter(group => group.visibility === "visible") };
          currentSessionWorkOverviewResult = currentWorkOverviewResult;
        }
        currentWorkOverviewLoadingMore = false;
        currentWorkOverviewUnavailable = false;
        if (event.state === "no_records" || event.state === "collapsed" || (event.state === "visible" && event.visibility_counts.visible_groups === 0)) clearCurrentWorkVisibilityRecheck();
        else scheduleCurrentWorkVisibilityRecheck(currentWorkOverviewResult, event.session_id);
        if (!buildCurrentWorkOverviewPanel(currentWorkOverviewResult, {
          open: currentWorkOverviewOpen,
          liveSession: currentWorkLiveSessionInput(),
        }).visible) currentWorkOverviewOpen = false;
        refreshTopbar();
        flushQueuedCurrentWorkOverviewRefresh();
        break;
      }
      case "work-card.present": {
        if (event.session_id !== chat.model.sessionId) break;
        const deferChat = companionHomeDefersWorkCardChatPresentation(projectState.mainFocus, event.card);
        const received = receiveWorkCardPresentation({
          registry: workCardPresentations,
          event,
          mergeReference: deferChat
            ? () => {}
            : (entry) => {
              const prior = chat.model.blocks.find((block): block is Extract<ChatBlock, { kind: "work-card-reference" }> => block.kind === "work-card-reference" && block.presentationId === entry.presentationId);
              if (prior && prior.cardId !== entry.cardId) throw new Error("work-card presentation identity conflicts with chat history");
              if (!prior) chat.model.blocks.push({ kind: "work-card-reference", presentationId: entry.presentationId, cardId: entry.cardId, text: "" });
            },
          sendReceived: (identity) => client.acknowledgeWorkCardReceived(identity),
        });
        if (received.receiptAttempt === "not_eligible") break;
        if (received.receiptAttempt === "failed") setStatus("卡片接收回执发送失败；未确认接收，等待后续重放重试。");
        if (!deferChat) renderChat();
        scheduleCurrentWorkOverviewRefresh();
        break;
      }
      case "work-card.receipt.backlog": {
        if (event.session_id !== chat.model.sessionId) break;
        const backlog = [
          event.pending_count ? `待确认 ${event.pending_count}` : "",
          event.deferred_count ? `已延后 ${event.deferred_count}` : "",
          event.legacy_untracked_count ? `旧记录 ${event.legacy_untracked_count} 无接收追踪` : "",
        ].filter(Boolean).join("，");
        const replay = event.retry_batch_size
          ? `本连接重放 ${event.retry_batch_size}/${event.batch_limit}`
          : "本连接暂无可重放项";
        setStatus(`工作卡回执积压：${backlog}；${replay}。不会自动请求下一批。`);
        break;
      }
      case "work-card.received.done": {
        if (event.session_id !== chat.model.sessionId) break;
        if (event.status === "received" || event.status === "already_received") {
          // Clear only an obsolete receipt warning; successful receipts stay otherwise silent.
          if (statusText.startsWith("卡片接收回执发送失败") || statusText.startsWith("服务端未确认客户端接收：")) setStatus("就绪");
        } else {
          setStatus(`服务端未确认客户端接收：${event.message}`);
        }
        break;
      }
      case "work-card.snapshot.view.timeout":
        if (event.session_id === chat.model.sessionId) setStatus("快照查看确认超时，未确认记录；可关闭后重新展开详情重试。");
        break;
      case "work-card.snapshot.view.done":
        if (event.session_id !== chat.model.sessionId) break;
        setStatus(event.status === "acknowledged" || event.status === "already_acknowledged"
          ? "已记录快照详情查看；不代表结果已验证。"
          : `快照查看未确认：${event.message}`);
        if (event.status === "acknowledged" || event.status === "already_acknowledged") scheduleCurrentWorkOverviewRefresh();
        break;
      case "work-card.result.view.done":
        if (event.session_id !== chat.model.sessionId) break;
        workCardResultExcerpts.accept(event, chat.model.sessionId, workCardPresentations.get(event.presentation_id));
        setStatus(event.status === "acknowledged" || event.status === "already_acknowledged"
          ? "已记录该结果详情已展开；不代表认可或验收。"
          : `结果查看未确认：${event.message}`);
        if (event.status === "acknowledged" || event.status === "already_acknowledged") {
          scheduleCurrentWorkVisibilityRecheck(currentWorkOverviewResult, event.session_id, true, true);
          scheduleCurrentWorkOverviewRefresh();
        }
        break;

      case "work-card.get.done": {
        const capturedSession = "card_id" in event && event.card_id ? pendingHistoryCardReads.get(event.card_id) : undefined;
        if (!capturedSession || !("card_id" in event) || !event.card_id) break;
        if (event.status === "found") settleHistoryCardReads(event.card_id, capturedSession, event.card);
        else if (event.status === "not_found") settleHistoryCardReads(event.card_id, capturedSession, null);
        else failHistoryCardReads(event.card_id, capturedSession, event.message);
        break;
      }

      case "session.history":
        if (
          event.session_id
          && hydrationSessionId
          && event.session_id !== hydrationSessionId
        ) break;
        if (
          event.session_id
          && !hydrationSessionId
          && projectState.currentSessionId
          && event.session_id !== projectState.currentSessionId
        ) break;
        chat.handleEvent(event);
        if (pendingSwitchBatch || hydrationSessionId) {
          completeHydrationWait(event.session_id);
          setStatus("就绪");
        }
        break;

      case "review.subagent.start":
        chat.pushReviewSubagentCard("running", { taskPreview: event.task_preview });
        setStatus("交付审查中…");
        break;

      case "review.subagent.done":
        chat.updateReviewSubagentCard("done", {
          summary: event.summary_preview || event.summary,
          verdict: event.verdict ?? undefined,
          blockersCount: event.blockers_count,
        });
        if (event.verdict) {
          projectState.reviewVerdict = event.verdict;
        }
        if (typeof event.blockers_count === "number") {
          projectState.reviewBlockersCount = event.blockers_count;
        }
        setStatus("就绪");
        renderSidebar();
        break;

      case "project.list":
        applyProjectListEvent(projectState, event);
        renderSidebar();
        if (projectState.mainFocus === "projects") renderProjectsPane();
        refreshTopbar();
        break;

      case "project.verify.done":
        lastVerify = { passed: Boolean(event.passed), at: new Date() };
        renderSidebarProjectRow();
        if (event.ok === true && event.passed === true) {
          const sharedEvent = verifiedProjectEventToSharedEvent(event);
          if (sharedEvent) {
            companionWorldState = recordExplicitSharedEvent(companionWorldState, sharedEvent, Date.now());
            saveCompanionWorld(companionWorldState, worldStorage);
          }
        }
        break;

      case "project.threads":
        if (!applyProjectThreadsEvent(projectState, event)) break;
        projectState.currentSessionId = projectState.currentSessionId || event.active_session_id || "";
        syncArchivedViewUi();
        renderSidebar();
        break;

      case "project.thread.new.done":
        projectState.currentSessionId = event.session_id;
        projectState.activeSessionId = event.session_id;
        client.listProjects();
        renderSidebar();
        if (projectState.projectId) {
          refreshTopbar();
        }
        syncArchivedViewUi();
        updatePlaceholder();
        updateWorkbenchEmpty();
        composerWire.syncSendEnabled();
        setStatus(event.message);
        break;

      case "session.restart_today.done":
        projectState.currentSessionId = event.session_id;
        if (projectState.projectId) {
          projectState.activeSessionId = event.session_id;
        }
        client.listProjects();
        renderSidebar();
        refreshTopbar();
        syncArchivedViewUi();
        updatePlaceholder();
        updateWorkbenchEmpty();
        composerWire.syncSendEnabled();
        setStatus(event.message);
        break;

      case "services.list.done":
        projectState.servicesLoading = false;
        projectState.servicesError = "";
        projectState.services = Array.isArray(event.services) ? event.services : [];
        renderSidebar();
        break;

      case "services.state":
        projectState.servicesLoading = false;
        projectState.servicesError = "";
        projectState.services = Array.isArray(event.services) ? event.services : [];
        renderSidebar();
        break;

      case "services.logs.done":
        projectState.servicesLogName = event.name;
        projectState.servicesLogText = event.text || "";
        renderSidebar();
        break;

      case "terminal.list.done":
        if (event.request_id && event.request_id !== terminalRequestId) break;
        if (terminalRequestTimeoutId !== null) {
          window.clearTimeout(terminalRequestTimeoutId);
          terminalRequestTimeoutId = null;
        }
        projectState.terminalsLoading = false;
        projectState.terminalsError = event.ok ? "" : (event.error || "读取终端状态失败");
        projectState.terminalSessions = Array.isArray(event.sessions) ? event.sessions : [];
        if (projectState.terminalDetails) {
          const current = projectState.terminalSessions.find(
            (session) => session.session_id === projectState.terminalDetails?.session_id,
          );
          if (current) {
            projectState.terminalDetails = current;
          } else {
            closeTerminalDetails();
            break;
          }
        }
        renderSidebar();
        break;

      case "terminal.output.done":
        if (
          event.request_id
          && event.request_id !== terminalOutputRequestId
        ) break;
        if (
          event.session_id !== terminalOutputRequestSessionId
          || event.session_id !== projectState.terminalDetails?.session_id
        ) break;
        if (terminalOutputRequestTimeoutId !== null) {
          window.clearTimeout(terminalOutputRequestTimeoutId);
          terminalOutputRequestTimeoutId = null;
        }
        projectState.terminalOutputLoading = false;
        projectState.terminalOutputError = event.ok ? "" : (event.error || "输出暂时不可用");
        projectState.terminalOutputCursorReset = Boolean(event.cursor_reset);
        projectState.terminalOutputTruncated = Boolean(event.truncated);
        if (event.ok) {
          projectState.terminalOutput = event.cursor_reset
            ? event.output
            : projectState.terminalOutput + event.output;
          projectState.terminalOutputCursor = event.next_cursor;
          if (event.session) projectState.terminalDetails = event.session;
        }
        renderSidebar();
        break;

      case "terminal.close.done":
        if (event.session_id === projectState.terminalDetails?.session_id && !event.ok) {
          projectState.terminalOutputError = event.error || "关闭终端失败";
        }
        if (event.ok) {
          if (event.state === "dismissed" && event.session_id === projectState.terminalDetails?.session_id) {
            closeTerminalDetails();
          }
          setStatus(event.state === "dismissed" ? "已移除终端记录" : "已关闭终端会话");
          refreshTerminals();
        }
        renderSidebar();
        break;

      case "turn.evidence": {
        const snapshot = normalizeTurnEvidenceEvent(event, chat.model.executionRunId);
        if (!snapshot) break;
        projectState.turnEvidenceRunId = snapshot.runId;
        projectState.turnEvidence = snapshot.items;
        projectState.turnEvidenceTotalCount = snapshot.totalCount;
        projectState.turnEvidenceOmittedCount = snapshot.omittedCount;
        projectState.turnGateNotice =
          typeof event.gate_notice === "string" ? event.gate_notice : "";
        {
          const rel = event.reliability;
          projectState.turnPostcondition =
            rel && typeof rel.postcondition === "string" ? rel.postcondition : "none";
          projectState.turnCircuitOpen = Array.isArray(rel?.circuit_open)
            ? rel.circuit_open.filter((x): x is string => typeof x === "string")
            : [];
          projectState.turnPlaybookId =
            rel && typeof rel.playbook_id === "string" ? rel.playbook_id : "";
          projectState.turnFailureClass =
            rel && typeof rel.failure_class === "string" ? rel.failure_class : "";
        }
        if (perspective === "project") {
          renderSidebar();
        }
        break;
      }

      case "project.switch.request":
        if (
          projectState.pendingPickerId
          && event.project_id === projectState.pendingPickerId
        ) {
          // User already chose this project; finish the switch instead of a second confirm.
          try {
            client.switchProject(event.project_id, {
              confirm: true,
              requestId: event.request_id,
            });
          } catch (err) {
            projectState.switchInProgress = false;
            projectState.pendingPickerId = "";
            pendingSwitchBatch = false;
            syncSessionSwitchMask();
            setStatus(`切换失败：${err instanceof Error ? err.message : String(err)}`);
            renderSidebar();
            refreshTopbar();
          }
          break;
        }
        beginAtomicProjectSwitch(projectState, event.project_id);
        projectState.switchInProgress = false;
        projectState.switchOverlay = {
          requestId: event.request_id,
          projectId: event.project_id,
          message: event.message,
          action: event.action,
        };
        clearChatForSwitch();
        setMainFocus("chat");
        syncSessionSwitchMask();
        refreshTopbar();
        renderSidebar();
        break;

      case "project.switch.done":
        if (
          projectState.pendingPickerId
          && event.project_id !== projectState.pendingPickerId
        ) break;
        resetProjectScopedState(projectState);
        projectState.switchOverlay = null;
        projectState.projectId = event.project_id;
        projectState.pendingPickerId = event.project_id;
        projectState.currentSessionId = event.session_id;
        projectState.switchInProgress = true;
        if (event.project_id) {
          freeChatActive = false;
        }
        if (event.session_replaced) {
          startHydrationWait(event.session_id, "切换项目");
        } else {
          completeHydrationWait();
        }
        renderSidebar();
        if (projectState.projectId) {
          refreshTopbar();
        }
        updatePlaceholder();
        updateWorkbenchEmpty();
        composerWire.syncSendEnabled();
        syncSessionSwitchMask();
        if (!event.session_replaced) {
          setStatus(event.message);
        }
        break;

      case "project.doc.list.done":
        projectState.projectDocs = event.docs;
        if (typeof event.placeholder_count === "number") {
          projectState.docPlaceholderCount = event.placeholder_count;
        }
        if (projectState.deleteConfirmPath && !event.docs.some((doc: { path: string }) => doc.path === projectState.deleteConfirmPath)) {
          projectState.deleteConfirmPath = "";
        }
        if (projectState.mainFocus === "document") {
          if (!projectState.currentDocPath) {
            const first = sortProjectDocs(event.docs)[0];
            if (first) {
              openDocument(first.path);
              break;
            }
          }
          renderDocumentPane();
        }
        renderSidebar();
        break;

      case "project.doc.read.done":
        if (projectState.currentDocPath && event.path !== projectState.currentDocPath) break;
        if (isDocDirty()) break;
        projectState.currentDocPath = event.path;
        projectState.currentDocContent = event.content;
        projectState.currentDocMtime = typeof event.mtime === "number" ? event.mtime : 0;
        projectState.currentDocHash = typeof event.content_hash === "string" ? event.content_hash : "";
        projectState.docConflictPrompt = false;
        if (projectState.docView === "edit") projectState.docEditDraft = event.content;
        if (projectState.mainFocus === "document") renderDocumentPane();
        renderSidebar();
        break;

      case "project.doc.create.done":
        projectState.newDocName = "";
        pendingDocAction = null;
        setStatus(`已新建 ${event.name}`);
        if (event.path && projectState.mainFocus === "document") {
          openDocument(event.path);
        }
        requestDocList();
        break;

      case "project.doc.write.done":
        pendingDocAction = null;
        projectState.docSaving = false;
        projectState.docConflictPrompt = false;
        if (event.path === projectState.currentDocPath) {
          projectState.currentDocContent = projectState.docEditDraft;
          projectState.docDirty = false;
          if (typeof event.mtime === "number") projectState.currentDocMtime = event.mtime;
          if (typeof event.content_hash === "string") projectState.currentDocHash = event.content_hash;
        }
        setStatus("文档已保存");
        if (docLeaveNext) {
          finishDocLeave();
        } else if (projectState.mainFocus === "document") {
          syncDocEditChrome();
        }
        break;

      case "project.doc.write.conflict":
        pendingDocAction = null;
        projectState.docSaving = false;
        projectState.docConflictPrompt = true;
        setStatus("文档已被修改，请选择重新载入或仍要覆盖");
        if (projectState.mainFocus === "document") renderDocumentPane();
        break;

      case "project.doc.restore.done":
        projectState.pendingDocUndo = null;
        projectState.undoDescription = "";
        setStatus(`已恢复 ${event.name || event.path}`);
        requestDocList();
        if (event.path) openDocument(event.path);
        break;

      case "project.doc.rename.done":
        pendingDocAction = null;
        projectState.renamingDocPath = "";
        projectState.renameDraft = "";
        if (projectState.currentDocPath === event.old_path) {
          projectState.currentDocPath = event.path;
          if (projectState.mainFocus === "document") {
            if (projectState.docView === "edit") captureDocDraftFromEditor();
            renderDocumentPane();
          }
        }
        if (projectState.mainFocus === "document" && projectState.currentDocPath !== event.path) renderDocumentPane();
        setStatus(`已重命名为 ${event.name}`);
        break;

      case "project.doc.delete.done":
        pendingDocAction = null;
        projectState.deleteConfirmPath = "";
        if (projectState.currentDocPath === event.path) {
          resetDocEditState();
          if (projectState.mainFocus === "document" || projectState.railTab === "docs") {
            openDocumentReader();
          }
        }
        setStatus("文档已移入回收站");
        if (projectState.mainFocus === "document") renderDocumentPane();
        renderSidebar();
        break;

      case "project.undo.available":
        projectState.undoDescription = event.description;
        if (event.undo_kind === "restore_doc" && event.undo_payload) {
          projectState.pendingDocUndo = event.undo_payload as Record<string, unknown>;
        }
        if (projectState.undoTimerId) { window.clearTimeout(projectState.undoTimerId); }
        projectState.undoTimerId = window.setTimeout(() => {
          projectState.undoDescription = "";
          projectState.pendingDocUndo = null;
          projectState.undoTimerId = null;
          renderSidebar();
        }, 8000) as unknown as number;
        renderSidebar();
        break;

      case "project.detect":
        // Auto-detect: workspace directory looks like a project
        projectState.detectedProject = { id: event.project_id, reason: event.reason };
        renderSidebar();
        setStatus(event.reason);
        break;

      case "activity.update":
        if (!chat.handleEvent(event)) break;
        showProjectWorkerProgress(event);
        break;

      case "chat.start":
      case "chat.delta":
      case "chat.done":
      case "chat.error":
      case "chat.cancel.result":
        if (!chat.handleEvent(event)) break;
        if (event.type === "chat.start") setStatus("后台已判定为对话；回复仍在当前会话中");
        else if (event.type === "chat.done") setStatus("对话回复完成；后台工作继续");
        else if (event.type === "chat.error") setStatus(`消息处理失败：${event.message}；后台工作状态不变`);
        else if (event.type === "chat.cancel.result" && !event.accepted) setStatus("对话取消未被接受；后台工作不受影响");
        break;

      case "prompt.request":
        if (!chat.handleEvent(event)) break;
        setStatus("工具请求交互输入；请在同一输入框回复");
        syncPromptComposerUi();
        syncComposerSendEnabled();
        requestAnimationFrame(() => scrollToBottomIfNear());
        break;

      case "prompt.response.result": {
        const pending = chat.model.pendingToolPrompt;
        if (!chat.handleEvent(event)) break;
        if (event.accepted) {
          if (pending && input.value === pending.responseText) {
            input.value = "";
            input.style.height = "";
          }
          setStatus("工具已接受输入；等待当前工作继续");
        } else {
          setStatus("工具未接受输入；回复已保留，且未发送为普通消息");
        }
        syncPromptComposerUi();
        syncComposerSendEnabled();
        break;
      }

      case "work.instruction.state":
        if (!chat.handleEvent(event)) break;
        if (event.state === "queued") setStatus("消息已排队，等待进入工作上下文");
        else if (event.state === "delivered" && event.delivery_stage === "next_model_request_context") {
          setStatus("消息已进入下一次模型请求上下文（不代表已遵循或完成）");
        } else if (event.state === "undelivered") {
          setStatus(`消息未送达${event.reason ? `：${event.reason}` : ""}`);
        }
        break;

      case "companion.orchestration.state":
        if (!chat.handleEvent(event)) break;
        if (event.state === "dispatching") setStatus("正在安排各项目…");
        else if (event.state === "active") setStatus("并行编排已进入当前工作，各项目在 worker 中执行");
        else if (event.state === "undelivered") {
          setStatus(`并行编排未送达${event.reason ? `：${event.reason}` : ""}`);
        }
        break;

      case "execution.stop.result":
        if (!chat.handleEvent(event)) break;
        if (!event.accepted) {
          setComposerEnabled(!chat.model.confirmPending);
          setStatus("停止请求未被接受；工作仍按当前生命周期状态显示");
        }
        syncWorkingVisual();
        break;

      case "execution.state":
        if (!chat.handleEvent(event)) break;
        if (["settled", "paused", "failed"].includes(event.state)) {
          setComposerEnabled(!chat.model.confirmPending);
          if (!chat.model.confirmPending && event.finish_reason) setTurnEndStatus(event.finish_reason);
        }
        if (event.state !== "running") clearProjectWorkerProgressStatus();
        if (event.run_id) {
          const previousOverviewState = currentWorkOverviewExecutionStates.get(event.run_id);
          currentWorkOverviewExecutionStates.set(event.run_id, event.state);
          if (shouldRefreshOverviewForExecutionState(previousOverviewState, event.state)) scheduleCurrentWorkOverviewRefresh();
          if (currentWorkOverviewExecutionStates.size > 100) {
            const oldestRunId = currentWorkOverviewExecutionStates.keys().next().value;
            if (oldestRunId) currentWorkOverviewExecutionStates.delete(oldestRunId);
          }
        }
        if ((event.state === "queued" || event.state === "running") && event.run_id) {
          if (projectState.turnEvidenceRunId !== event.run_id) {
            projectState.turnEvidence = [];
            projectState.turnEvidenceTotalCount = 0;
            projectState.turnEvidenceOmittedCount = 0;
            projectState.turnEvidenceExpanded = false;
          }
          projectState.turnEvidenceRunId = event.run_id;
        }
        if (event.state === "queued") {
          setStatus("等待续接…");
        } else if (event.state === "stopping") {
          setStatus("正在停止…");
        } else if (event.state === "running") {
          projectState.lastTurnFinishReason = "";
          setStatus("处理中…");
        } else if (event.finish_reason) {
          projectState.lastTurnFinishReason = event.finish_reason;
          setTurnEndStatus(event.finish_reason);
        }
        syncWorkingVisual();
        if (perspective === "project") renderSidebar();
        break;

      case "tool.start":
        if (!chat.handleEvent(event)) break;
        setStatus(`· ${event.tool}`);
        break;

      case "llm.pending":
        if (!chat.handleEvent(event)) break;
        if (!chat.model.cancelRequested) {
          setStatus("处理中…");
        }
        syncToolElapsedTimer();
        break;

      case "tool.progress":
        if (!chat.handleEvent(event)) break;
        if (typeof event.text === "string" && event.text.trim()) {
          setStatus(event.text.trim());
        }
        break;

      case "tool.end":
        if (!chat.handleEvent(event)) break;
        if (event.tool === "run_evolved" || /interactive_terminal/.test(event.summary || "")) {
          refreshTerminals();
        }
        if (event.ok) {
          const pathHint = typeof (event as { path?: string }).path === "string"
            ? (event as { path: string }).path
            : "";
          const blob = `${event.summary || ""} ${pathHint}`;
          const mdMatch = blob.match(/([\w./-]+\.md)\b/i) || blob.match(/(workspace[/\\][^\s,;]+\.md)/i);
          if (mdMatch) {
            let rel = mdMatch[1].replace(/\\/g, "/");
            const marker = `workspace/${projectState.projectId}/`;
            if (rel.includes(marker)) rel = rel.slice(rel.indexOf(marker) + marker.length);
            else if (rel.startsWith("workspace/") && projectState.projectId) {
              const parts = rel.split("/");
              if (parts.length >= 3 && parts[1] === projectState.projectId) {
                rel = parts.slice(2).join("/");
              }
            }
            if (rel.endsWith(".md")) {
              projectState.recentlyChangedDocs.add(rel);
              renderSidebar();
              if (projectState.mainFocus === "document") renderDocumentPane();
            }
          }
        }
        if (!chat.model.confirmPending) {
          const step = settledToolStatus(event.ok, event.summary, event.note);
          setStatus(step === "fail" ? "工具失败" : chat.isWorking() ? "处理中…" : "就绪");
        }
        break;

      case "reasoning.delta":
        if (!chat.handleEvent(event)) break;
        if (!chat.model.cancelRequested && !statusText.startsWith("·")) {
          setStatus("处理中…");
        }
        break;

      case "assistant.done":
        if (!chat.handleEvent(event)) break;
        if (perspective === "project") {
          client.refreshProject();
        }
        if (!chat.model.cancelRequested && !chat.model.confirmPending) {
          setStatus(chat.isWorking() ? "处理中…" : "就绪");
        }
        syncWorkingVisual();
        break;

      case "turn.notice":
        if (!chat.handleEvent(event)) break;
        {
          const statusLine = polishStatusBarNotice(event.text);
          if (statusLine && noticeMirrorsToStatus(event.text)) {
            setStatus(statusLine);
          }
        }
        syncWorkingVisual();
        break;

      case "turn.end":
        if (!chat.handleEvent(event)) break;
        clearProjectWorkerProgressStatus();
        scheduleCurrentWorkOverviewRefresh();
        projectState.lastTurnFinishReason = event.finish_reason || "";
        setTurnEndStatus(event.finish_reason);
        syncWorkingVisual();
        // Refresh doc list / open doc so Agent writes are visible.
        if (projectState.projectId) {
          requestDocList();
          if (projectState.currentDocPath && !isDocDirty() && !projectState.docConflictPrompt) {
            requestDocRead(projectState.currentDocPath);
          }
        }
        break;

      case "notice":
        if (!chat.handleEvent(event)) break;
        renderChat();
        {
          const statusLine = polishStatusBarNotice(event.text);
          if (statusLine && noticeMirrorsToStatus(event.text)) {
            setStatus(statusLine);
          }
        }
        break;

      case "confirm.request":
      case "confirm.done":
        if (event.type === "confirm.request" && /当前会话绑定到 local_managed 演化范围/.test(event.preview)) {
          shellDevFeedback("请切回当前会话，在确认卡中核对 owned_paths 并授权。", "busy");
        }
        chat.handleEvent(event);
        if (event.type === "confirm.done") {
          syncOrdinaryConfirmPending(chat.model);
        }
        renderChat();
        renderConfirmGlass();
        syncPendingConfirmComposerHint();
        syncWorkingVisual();
        if (event.type === "confirm.request") {
          requestAnimationFrame(() => {
            requestAnimationFrame(() => scrollPendingConfirmIntoView());
          });
        }
        break;

      case "error": {
        const rawMessage = String(event.message || "").trim();
        if (evolutionBeginPending) {
          evolutionBeginPending = false;
          syncShellDevEvolutionView();
          shellDevFeedback(rawMessage || "演化请求失败。", "error");
        }
        const noProject = /no active project session/i.test(rawMessage);
        let statusHandled = false;
        if (pendingDocAction) {
          const action = pendingDocAction;
          pendingDocAction = null;
          projectState.docSaving = false;
          const prefix =
            action === "rename" ? "重命名失败"
            : action === "delete" ? "删除失败"
            : action === "write" ? "保存失败"
            : "新建失败";
          setStatus(noProject ? "请先打开项目" : `${prefix}：${rawMessage}`);
          if (action === "write" && projectState.mainFocus === "document") syncDocEditChrome();
          renderSidebar();
          if (!noProject) chat.handleEvent(event);
          break;
        }
        if (projectState.servicesLoading) {
          projectState.servicesLoading = false;
          projectState.servicesError = noProject ? "请先打开项目" : rawMessage;
          renderSidebar();
        }
        if (projectState.switchInProgress || projectState.switchOverlay) {
          projectState.switchInProgress = false;
          projectState.pendingPickerId = "";
          completeHydrationWait();
          syncSessionSwitchMask();
          renderSidebar();
          refreshTopbar();
        }
        if (!noProject) chat.handleEvent(event);
        if (!statusHandled) setStatus(userFacingErrorStatus(rawMessage));
        break;
      }

      default:
        chat.handleEvent(event);
        break;
    }
  });

  // ---- initial render ----
  mountToolDisplayEditor();
  onToolDisplayOverridesChange(() => {
    renderedPrints = [];
    renderedSegmentPrints = [];
    renderChat();
  });
  updatePlaceholder();
  refreshTopbar();
  renderProposalsPanel();
  renderMemoryShelfPanel();
  renderChat();
  renderWorkActivity();
  setComposerEnabled(true);
  setStatus("已连接");

  // Phase 34: workbench layout + empty gate
  emptyNewBtn.addEventListener("click", () => handleNewProject());
  emptyPickBtn.addEventListener("click", () => {
    selectRailTab("projects");
  });
  emptyFreeChatBtn.addEventListener("click", () => {
    void handleFreeChat();
  });
  setPerspective("project", "auto");
  updateWorkbenchEmpty();
  composerWire.syncSendEnabled();
  try {
    client.listProjects();
  } catch {
    /* ignore */
  }
  refreshTerminals();
  scheduleTerminalPoll();

  return () => {
    destroyed = true;
    clearProjectWorkerProgressStatus();
    if (cancelledStatusTimer !== null) window.clearTimeout(cancelledStatusTimer);
    if (toolElapsedTimer !== null) window.clearInterval(toolElapsedTimer);
    if (renderThrottleTimer !== null) window.clearTimeout(renderThrottleTimer);
    if (terminalPollTimerId !== null) window.clearTimeout(terminalPollTimerId);
    if (terminalRequestTimeoutId !== null) window.clearTimeout(terminalRequestTimeoutId);
    if (terminalOutputRequestTimeoutId !== null) window.clearTimeout(terminalOutputRequestTimeoutId);
    terminalOutputRequestSerial += 1;
    renderedPrints = [];
    renderedSegmentPrints = [];
    focusObserver?.disconnect();
    unbindChatFontControl();
    settingsThemeObserver.disconnect();
    currentWorkResizeObserver.disconnect();
    window.removeEventListener("resize", onWorkActivityResize);
    document.removeEventListener("keydown", onWorkActivityHistoryKeydown, true);
    window.removeEventListener("resize", sizeCurrentWorkPopover);
    musicViewResizeObserver.disconnect();
    shellDevViewResizeObserver.disconnect();
    window.removeEventListener("resize", syncMusicViewBounds);
    window.removeEventListener("resize", syncShellDevViewBounds);
    musicEl.removeEventListener("scroll", syncMusicViewBounds);
    shellDevEl.removeEventListener("scroll", syncShellDevViewBounds);
    void window.myAgentDesktop?.setBilibiliViewState?.({ visible: false });
    void window.myAgentDesktop?.setShellDevViewState?.({ visible: false });
    evolutionRelayErrorCleanup?.();
    evolutionRelayErrorCleanup = null;
    fileDrop.destroy();
    off();
    shellEl.classList.remove("is-working");
    setAgentBusy(false);
    root.innerHTML = "";
  };
}
