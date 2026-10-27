function esc(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

export function renderShellDevPanel(mainUrl: string, previewUrl: string): string {
  const main = esc(mainUrl.trim() || "http://127.0.0.1:5173/");
  const preview = esc(previewUrl.trim() || "http://127.0.0.1:5174/");
  return `<div class="shell-dev-page">
    <header class="shell-dev-heading">
      <div class="shell-dev-heading-copy">
        <h1 id="shell-dev-page-heading">壳开发预览</h1>
        <p class="shell-dev-hint">日常合入点这里；用稳定刷实验见主操作；实验树预览与主壳切换见下方。</p>
        <details class="shell-dev-notes">
          <summary>注意事项</summary>
          <p>实验期在 <code>.worktrees/sandbox</code> 中修改并通过预览检查。<strong>一键完成合入</strong>会先检查状态，再走项目真实合入（原生确认），不等于只切换预览 URL。<strong>用稳定刷实验</strong>把稳定 HEAD 同步进实验树（优先 fast-forward；分叉时需确认以稳定为准），不等于合入。<strong>采用预览为主壳</strong>只临时切换预览 URL，不等于源码合入或激活。旧整支合入按钮仅用于隔离夹具。</p>
        </details>
      </div>
      <div class="shell-dev-toolbar" role="toolbar" aria-label="壳预览主操作">
        <div class="shell-dev-cta">
          <button type="button" class="unified-btn unified-btn-accent shell-dev-one-click" data-shell-dev-one-click title="先检查合入状态，再走项目真实合入（桌面原生确认）；取消不会改代码">一键完成合入</button>
          <button type="button" class="unified-btn" data-shell-dev-sync-stable title="把当前稳定版刷进实验树；实验区 dirty 会拒绝并列出阻断">用稳定刷实验</button>
          <button type="button" class="unified-btn" data-shell-dev-refresh-status title="刷新双车道 HEAD / ahead / dirty / 阻断">刷新状态</button>
        </div>
        <div class="shell-dev-actions shell-dev-actions-primary">
          <button type="button" class="unified-btn" data-shell-dev-popout>独立窗口预览</button>
          <button type="button" class="unified-btn" data-shell-dev-reload>重新加载内嵌</button>
          <div class="shell-dev-lane-toggle" role="group" aria-label="主壳切换">
            <button type="button" class="unified-btn" data-shell-dev-promote title="仅切换 5173/5174 主窗口 URL">采用预览为主壳</button>
            <button type="button" class="unified-btn" data-shell-dev-reset-main>返回稳定版</button>
          </div>
        </div>
        <details class="shell-dev-more">
          <summary class="unified-btn">更多</summary>
          <div class="shell-dev-actions shell-dev-actions-advanced">
            <button type="button" class="unified-btn" data-shell-dev-sandbox-session title="旧入口：将当前会话切到候选 sidecar">在候选中试用（旧入口）</button>
            <button type="button" class="unified-btn" data-shell-dev-adopt-status>检查合入状态</button>
            <button type="button" class="unified-btn" data-shell-dev-managed-review>只读源码审查</button>
            <button type="button" class="unified-btn" data-shell-dev-adopt-merge title="旧整支合入仅用于隔离夹具；普通项目由 E0 gate 拒绝。真实项目请使用一键完成合入或下方源码选择与检查点原生入口。">旧整支合入（仅隔离夹具）</button>
          </div>
        </details>
      </div>
    </header>
    <dl class="shell-dev-lanes">
      <div><dt>当前主壳</dt><dd data-shell-dev-main-url>${main}</dd></div>
      <div><dt>预览壳</dt><dd data-shell-dev-url>${preview}</dd></div>
    </dl>
    <section class="shell-dev-dual-status" aria-label="双车道状态" data-shell-dev-dual-status>
      <div class="shell-dev-dual-status-heading">
        <strong>双车道状态</strong>
        <button type="button" class="unified-btn shell-dev-dual-refresh" data-shell-dev-refresh-status>刷新</button>
      </div>
      <dl class="shell-dev-dual-grid">
        <div><dt>稳定 HEAD</dt><dd data-shell-dev-stable-head>—</dd></div>
        <div><dt>实验 HEAD</dt><dd data-shell-dev-sandbox-head>—</dd></div>
        <div><dt>实验领先</dt><dd data-shell-dev-commits-ahead>—</dd></div>
        <div><dt>稳定领先</dt><dd data-shell-dev-stable-ahead>—</dd></div>
        <div><dt>实验脏文件</dt><dd data-shell-dev-sandbox-dirty>—</dd></div>
        <div><dt>稳定脏文件</dt><dd data-shell-dev-stable-dirty>—</dd></div>
      </dl>
      <p class="shell-dev-sync-hint" role="status" aria-live="polite" data-shell-dev-sync-hint>正在读取双车道状态…</p>
      <p class="shell-dev-dual-blockers" role="status" aria-live="polite" data-shell-dev-dual-blockers hidden></p>
    </section>
    <section class="shell-dev-evolution" aria-label="当前会话演化状态">
      <div class="shell-dev-evolution-heading">
        <strong>当前会话演化</strong>
        <span role="status" aria-live="polite" data-evolution-status>未查询</span>
      </div>
      <div class="shell-dev-evolution-actions">
        <button type="button" class="unified-btn unified-btn-accent" data-evolution-begin>开始演化</button>
        <button type="button" class="unified-btn" data-evolution-inspect>查看状态</button>
        <button type="button" class="unified-btn" data-evolution-stop disabled>停止候选</button>
      </div>
      <p class="shell-dev-evolution-checks" data-evolution-checks></p>
    </section>
    <details class="shell-dev-section" aria-label="只读管理源码审查" data-managed-review-panel>
      <summary>只读源码审查面板</summary>
      <div data-managed-review-browser aria-live="polite" class="shell-dev-scroll-pane"></div>
      <pre data-managed-review-output hidden aria-live="polite" class="shell-dev-scroll-pane shell-dev-pre"></pre>
    </details>
    <details class="shell-dev-section" aria-label="源码范围选择">
      <summary>源码范围选择</summary>
      <p class="shell-dev-section-note">所选范围只生成提案，不执行合入。归属声明不等于来源证明；稳定区现有修改默认保留。</p>
      <div data-managed-source-selection aria-live="polite" class="shell-dev-scroll-pane"></div>
    </details>
    <p class="shell-dev-feedback" role="status" aria-live="polite" data-shell-dev-feedback></p>
    <div class="shell-dev-web-slot" data-shell-dev-web-slot aria-label="Desktop Vite 候选预览"></div>
  </div>`;
}