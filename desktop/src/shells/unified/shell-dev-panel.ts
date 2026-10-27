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
        <p class="shell-dev-hint">实验树预览与主壳切换；详细合入说明见「注意事项」。</p>
        <details class="shell-dev-notes">
          <summary>注意事项</summary>
          <p>实验期在 <code>.worktrees/sandbox</code> 中修改并通过预览检查。旧整支合入按钮仅用于隔离夹具，普通项目由 E0 gate 拒绝；真实项目请使用下方源码选择与检查点原生入口。所选源码合入≠build/activate，不会自动同步 sandbox 的 dirty 内容。<strong>采用预览为主壳</strong>只临时切换预览 URL，不等于源码合入或激活。</p>
        </details>
      </div>
      <div class="shell-dev-toolbar" role="toolbar" aria-label="壳预览主操作">
        <div class="shell-dev-actions shell-dev-actions-primary">
          <button type="button" class="unified-btn unified-btn-accent" data-shell-dev-popout>独立窗口预览</button>
          <button type="button" class="unified-btn" data-shell-dev-reload>重新加载内嵌</button>
          <div class="shell-dev-lane-toggle" role="group" aria-label="主壳切换">
            <button type="button" class="unified-btn" data-shell-dev-promote title="仅切换 5173/5174 主窗口 URL">采用预览为主壳</button>
            <button type="button" class="unified-btn" data-shell-dev-reset-main>返回稳定版</button>
          </div>
        </div>
        <details class="shell-dev-more">
          <summary class="unified-btn">更多</summary>
          <div class="shell-dev-actions shell-dev-actions-advanced">
            <button type="button" class="unified-btn unified-btn-accent" data-shell-dev-sandbox-session title="旧入口：将当前会话切到候选 sidecar">在候选中试用（旧入口）</button>
            <button type="button" class="unified-btn" data-shell-dev-adopt-status>检查合入状态</button>
            <button type="button" class="unified-btn" data-shell-dev-managed-review>只读源码审查</button>
            <button type="button" class="unified-btn unified-btn-accent" data-shell-dev-adopt-merge title="旧整支合入仅用于隔离夹具；普通项目由 E0 gate 拒绝。真实项目请使用下方源码选择与检查点原生入口。">旧整支合入（仅隔离夹具）</button>
          </div>
        </details>
      </div>
    </header>
    <dl class="shell-dev-lanes">
      <div><dt>当前主壳</dt><dd data-shell-dev-main-url>${main}</dd></div>
      <div><dt>预览壳</dt><dd data-shell-dev-url>${preview}</dd></div>
    </dl>
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
