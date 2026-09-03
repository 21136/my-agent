(function () {
  const params = new URLSearchParams(location.search);
  const startWorking = !params.has("idle");
  const isMusic = document.documentElement.dataset.style === "music";

  const albumArt = isMusic ? `<div class="album-art">♪ Music Dreamer</div>` : "";
  const projectBadge = isMusic ? `<span class="project-badge">♪ 悦享音乐</span>` : "";

  const inner = `
    <header class="app-chrome topbar">
      <span class="brand">my-agent</span>
      <div class="tb-group">
        <label>外观</label>
        <select><option>亮色</option></select>
      </div>
      <div class="tb-group">
        <label>模型</label>
        <select><option>0x567 Luna (372k)</option></select>
      </div>
      <span class="chip on">狂奔：开</span>
      <span class="tb-spacer"></span>
      <button class="btn-ghost" type="button">模型 Key</button>
      <button class="btn-ghost" type="button">托管区</button>
      <button class="btn-ghost" type="button">切到 CLI</button>
    </header>

    <div class="unified-shell" id="unified-shell">
      <aside class="unified-sidebar sidebar">
        <div class="sidebar-inner">
          <div class="project-name">项目 · music ${projectBadge}</div>
          ${albumArt}
          <div class="task-card">
            <div class="label">实现当前任务</div>
            <h3>悦享音乐（Music Dreamer）</h3>
            <p>生产级音乐平台，支持发现、播放、收藏、歌单、搜索与个性化推荐。</p>
          </div>
          <div class="sidebar-log">
            <strong>ADR-003</strong> 已记录技术选型<br />
            开放任务 <strong>3 / 25</strong>
          </div>
          <span class="view-process">查看过程</span>
        </div>
        <div class="sidebar-footer">服务 · 2 运行中 · 3 已停止</div>
      </aside>

      <div class="unified-main main">
        <header class="unified-topbar context-bar">
          <span class="status-dot"></span>
          <span class="proj">music</span>
          <span class="sep">·</span>
          <span class="phase">正在实现任务 · 进行中</span>
        </header>

        <main class="unified-chat chat">
          <div class="msg"><strong>系统</strong> · 已切换模型至 <code>0x567-flash</code></div>
          <div class="msg"><strong>已采纳并写入</strong> <code>SCOPE.md</code></div>
          <div class="msg"><strong>已采纳并写入</strong> <code>DESIGN.md</code></div>
          <div class="msg"><strong>已采纳并写入</strong> <code>TECH-DESIGN.md</code></div>
        </main>

        <div class="unified-process process-card">
          <div class="icon"></div>
          <div>
            <div class="title">过程 · 思考中…</div>
            <div class="timer">36s</div>
          </div>
        </div>

        <footer class="unified-composer composer">
          <div class="token-bar">240k / 372k tokens</div>
          <div class="input-row unified-input-wrap">
            <button class="btn-stop" type="button">停止</button>
            <div class="input-wrap">输入消息或拖入文件；改计划会自动交给计划搭档</div>
            <button class="btn-primary" type="button">发送</button>
          </div>
        </footer>
      </div>
    </div>

    <button type="button" class="work-toggle${startWorking ? "" : " is-idle"}" id="work-toggle">
      ${startWorking ? "● 工作中" : "○ 空闲"}
    </button>
    <a class="back-link" href="index.html">← 返回对比</a>
  `;

  const frame = document.getElementById("app-frame");
  frame.classList.toggle("is-agent-busy", startWorking);
  frame.innerHTML = inner;

  const shellEl = document.getElementById("unified-shell");
  shellEl.classList.toggle("is-working", startWorking);

  document.getElementById("work-toggle").addEventListener("click", () => {
    const on = !frame.classList.contains("is-agent-busy");
    frame.classList.toggle("is-agent-busy", on);
    shellEl.classList.toggle("is-working", on);
    const btn = document.getElementById("work-toggle");
    btn.classList.toggle("is-idle", !on);
    btn.textContent = on ? "● 工作中" : "○ 空闲";
  });
})();
