export function mountGovernPlaceholder(root: HTMLElement): () => void {
  root.innerHTML = `
    <div class="shell-placeholder">
      <h2>治理壳（govern）</h2>
      <p class="text-muted">占位 — 周期性 review 时再实现。</p>
      <p class="text-muted">处理 proposal 请在外壳菜单切回「生长」。</p>
    </div>
  `;
  return () => {
    root.innerHTML = "";
  };
}
