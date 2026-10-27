function esc(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

/** Flat dual-lane control plane: 稳定 | 实验 peer cards + peer CTAs; 预览 is a sibling section. */
export function renderShellDevPanel(mainUrl: string, previewUrl: string): string {
  const main = esc(mainUrl.trim() || "http://127.0.0.1:5173/");
  const preview = esc(previewUrl.trim() || "http://127.0.0.1:5174/");
  return `<div class="shell-dev-page" data-dual-lane-panel>
    <header class="dual-lane-heading">
      <div class="shell-dev-heading-copy">
        <h1 id="shell-dev-page-heading">双车道</h1>
        <p class="shell-dev-hint">稳定与实验对等展示；「用稳定刷实验」与「合入稳定」同级。预览是同级区块，不是外层套娃。</p>
      </div>
      <button type="button" class="unified-btn" data-shell-dev-refresh-status title="刷新双车道 HEAD / ahead / dirty / 阻断">刷新状态</button>
    </header>

    <section class="dual-lane-cards" aria-label="双车道" data-shell-dev-dual-status>
      <article class="dual-lane-card" data-lane="stable">
        <header class="dual-lane-card-head">
          <h2>稳定</h2>
          <span class="dual-lane-card-sub">5173 / 8765</span>
        </header>
        <dl class="dual-lane-card-grid">
          <div><dt>分支</dt><dd data-shell-dev-stable-branch>—</dd></div>
          <div><dt>HEAD</dt><dd data-shell-dev-stable-head>—</dd></div>
          <div><dt>脏文件</dt><dd data-shell-dev-stable-dirty>—</dd></div>
          <div><dt>领先实验</dt><dd data-shell-dev-stable-ahead>—</dd></div>
          <div class="dual-lane-card-wide"><dt>地址</dt><dd data-shell-dev-main-url>${main}</dd></div>
        </dl>
        <button type="button" class="unified-btn dual-lane-peer-cta" data-shell-dev-sync-stable title="把当前稳定版刷进实验树；实验区 dirty 会拒绝并列出阻断">用稳定刷实验</button>
        <p class="dual-lane-card-note">稳定 → 实验（同步，不是合入）</p>
      </article>

      <article class="dual-lane-card" data-lane="sandbox">
        <header class="dual-lane-card-head">
          <h2>实验</h2>
          <span class="dual-lane-card-sub">5174 / 8766</span>
        </header>
        <dl class="dual-lane-card-grid">
          <div><dt>分支</dt><dd data-shell-dev-sandbox-branch>—</dd></div>
          <div><dt>HEAD</dt><dd data-shell-dev-sandbox-head>—</dd></div>
          <div><dt>脏文件</dt><dd data-shell-dev-sandbox-dirty>—</dd></div>
          <div><dt>领先稳定</dt><dd data-shell-dev-commits-ahead>—</dd></div>
          <div class="dual-lane-card-wide"><dt>地址</dt><dd data-shell-dev-url>${preview}</dd></div>
        </dl>
        <button type="button" class="unified-btn dual-lane-peer-cta" data-shell-dev-one-click title="先检查合入状态，再走项目真实合入（桌面原生确认）；取消不会改代码">合入稳定</button>
        <p class="dual-lane-card-note">实验 → 稳定（一键合入）</p>
        <p class="shell-dev-dual-blockers" role="status" aria-live="polite" data-shell-dev-dual-blockers hidden></p>
      </article>
    </section>

    <p class="shell-dev-sync-hint" role="status" aria-live="polite" data-shell-dev-sync-hint>正在读取双车道状态…</p>
    <p class="shell-dev-feedback" role="status" aria-live="polite" data-shell-dev-feedback></p>

    <section class="dual-lane-preview" aria-label="预览">
      <div class="dual-lane-preview-heading">
        <strong>预览</strong>
        <div class="shell-dev-actions shell-dev-actions-primary" role="group" aria-label="预览操作">
          <button type="button" class="unified-btn" data-shell-dev-popout>独立窗口预览</button>
          <button type="button" class="unified-btn" data-shell-dev-reload>重新加载内嵌</button>
          <button type="button" class="unified-btn" data-shell-dev-promote title="仅临时切换主窗口 URL，不等于源码合入">预览临时切主壳</button>
          <button type="button" class="unified-btn" data-shell-dev-reset-main>主壳回稳定</button>
        </div>
      </div>
      <div class="shell-dev-web-slot" data-shell-dev-web-slot aria-label="Desktop Vite 候选预览"></div>
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

    <details class="shell-dev-help shell-dev-notes" data-shell-dev-help>
      <summary>帮助与旧入口</summary>
      <p>刷实验 = 稳定 HEAD 同步进实验树（dirty 会拦；分叉需再点一次以稳定为准）。合入稳定 = 项目真实合入（原生确认）。预览切主壳只改 URL，不等于合入。</p>
      <div class="shell-dev-actions shell-dev-actions-advanced-inline">
        <button type="button" class="unified-btn" data-shell-dev-sandbox-session title="旧入口：将当前会话切到候选 sidecar">在候选中试用（旧入口）</button>
        <button type="button" class="unified-btn" data-shell-dev-adopt-status>检查合入状态</button>
        <button type="button" class="unified-btn" data-shell-dev-managed-review>只读源码审查</button>
        <button type="button" class="unified-btn" data-shell-dev-adopt-merge title="旧整支合入仅用于隔离夹具；普通项目由 E0 gate 拒绝。">旧整支合入（仅隔离夹具）</button>
      </div>
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
    </details>
  </div>`;
}
