/** Companion home: long work-record overview replies stay in chat history but collapse by default. */

export function isWorkRecordsOverviewBriefing(text: string): boolean {
  const source = text.trim();
  if (!source) return false;
  const hasOverviewLead =
    source.includes("当前会话已记录事项概况")
    || source.includes("工作记录和历史卡片不修改");
  const hasItemShape =
    source.includes("事项：")
    && source.includes("状态概况：")
    && (source.includes("状态计数：") || source.includes("要求："));
  return hasOverviewLead || (hasItemShape && source.includes("未展开"));
}

export function workRecordsBriefingSummary(text: string): string {
  const lines = text.trim().split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const lead = lines.find((line) => line.includes("当前会话已记录事项概况")) ?? lines[0] ?? "工作记录摘要";
  const totals = lines.find((line) => /^共 \d+ 组/.test(line));
  const omitted = lines.find((line) => line.includes("未展开") || line.includes("不是全部记录"));
  const parts = [lead.replace(/（[^）]*）$/, "（历史查询）").slice(0, 120)];
  if (totals) parts.push(totals.slice(0, 80));
  if (omitted) parts.push(omitted.slice(0, 72));
  return parts.join(" · ");
}

export function shouldPreferBriefingBubble(text: string, companionHome: boolean): boolean {
  return companionHome && isWorkRecordsOverviewBriefing(text);
}

export function renderWorkRecordsBriefingShell(summary: string, bodyHtml: string): string {
  return `<details class="unified-history-briefing">
<summary class="unified-history-briefing-summary">${summary}</summary>
<div class="unified-history-briefing-body unified-markdown">${bodyHtml}</div>
<p class="unified-history-briefing-foot" role="note">以上为对话中的历史查询记录，不会自动刷新；最新状态请用「当前工作」查看。</p>
</details>`;
}
