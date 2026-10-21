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
      <div>
        <h1 id="shell-dev-page-heading">壳开发预览</h1>
        <p>实验期在 <code>.worktrees/sandbox</code> 里随便改 <strong>desktop + agent-core</strong>（及 evolve 工具），稳定树不动。预览：<code>5173</code> 稳定壳、<code>5174</code> 实验壳。验收满意后点<strong>一键合入实验分支</strong>：<code>git merge experiment/sandbox</code>，把实验提交整包写进当前稳定分支。<strong>采用预览为主壳</strong>只临时换 5173/5174 地址，不算合入。</p>
      </div>
      <div class="shell-dev-actions">
        <button type="button" class="unified-btn unified-btn-accent" data-shell-dev-popout>独立窗口预览</button>
        <button type="button" class="unified-btn unified-btn-accent" data-shell-dev-sandbox-session>开实验进化会话</button>
        <button type="button" class="unified-btn unified-btn-accent" data-shell-dev-adopt-merge title="git merge experiment/sandbox（含 agent-core 与 desktop）">一键合入实验分支</button>
        <button type="button" class="unified-btn" data-shell-dev-reload>重新加载内嵌</button>
        <button type="button" class="unified-btn" data-shell-dev-promote title="仅切换 5173/5174 主窗口 URL">采用预览为主壳</button>
      </div>
    </header>
    <dl class="shell-dev-lanes">
      <div><dt>当前主壳</dt><dd data-shell-dev-main-url>${main}</dd></div>
      <div><dt>预览壳</dt><dd data-shell-dev-url>${preview}</dd></div>
    </dl>
    <p class="shell-dev-feedback" role="status" aria-live="polite" data-shell-dev-feedback></p>
    <div class="shell-dev-web-slot" data-shell-dev-web-slot aria-label="Desktop Vite 候选预览"></div>
  </div>`;
}
