/** Same scenario on every prototype page for apples-to-apples comparison. */
window.CHAT_SAMPLE = {
  user: "WS 已连上但前端没收到 ACK 时，别一直转「发送中」——应对齐 pending clientMsgId 并超时重发。",
  assistantHtml: `
    <p><strong>先自动扛，再说话。</strong> 模型 503 应多轮退避并切 Flash；空正文同回合重试。</p>
    <ul>
      <li>鉴权/参数错误才直接提示改配置</li>
      <li>过程默认折叠，正文只留结论</li>
    </ul>
    <blockquote>气泡「素」多半是层次叠了，不是色号不对。</blockquote>
  `,
  exploredSummary: "read_file, grep, explore",
  toolSteps: [
    { ok: true, label: "读取 resume.pdf", sub: "1.2s" },
    { ok: true, label: "grep Java 笔记", sub: "0.4s" },
    { ok: false, label: "explore 子代理", sub: "上游 overloaded" },
  ],
};

window.mountChatSample = function mountChatSample(root, opts = {}) {
  const s = window.CHAT_SAMPLE;
  const showLabel = opts.showLabel !== false;
  const name = opts.botName || "小魔王";
  const pill = opts.pill || "探索";
  root.innerHTML = `
    <div class="row-user"><div class="bubble-user">${s.user}</div></div>
    <div class="row-bot">
      ${opts.avatar ? '<div class="avatar" aria-hidden="true"></div>' : ""}
      <div class="bubble-bot md">
        ${showLabel ? `<div class="bot-meta"><span>${name}</span><span class="pill">${pill}</span></div>` : ""}
        ${s.assistantHtml}
        ${opts.processPill ? `
          <button type="button" class="process-pill" data-toggle-process>
            <span class="dot"></span><span data-process-label>Explored · ${opts.processPillLabel || "3 步"} ▸</span>
          </button>
          <div class="process-detail hidden" data-process-detail></div>
        ` : ""}
      </div>
    </div>
  `;
  const detail = root.querySelector("[data-process-detail]");
  if (detail && s.toolSteps) {
    detail.innerHTML = s.toolSteps
      .map(
        (step) =>
          `<div class="step"><span>${step.ok ? "✓" : "✗"}</span><div><div>${step.label}</div><div style="opacity:0.7;font-size:0.72rem">${step.sub}</div></div></div>`,
      )
      .join("");
  }
  const btn = root.querySelector("[data-toggle-process]");
  const label = root.querySelector("[data-process-label]");
  if (btn && detail && label) {
    btn.addEventListener("click", () => {
      const open = detail.classList.toggle("hidden");
      label.textContent = (open ? "Explored · 3 步 ▸" : "Explored · 3 步 ▾").replace(
        "Explored",
        opts.processPillLabel?.startsWith("过程") ? "过程" : "Explored",
      );
    });
  }
};
