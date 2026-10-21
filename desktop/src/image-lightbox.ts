import { loadAgentImageDataUrl, peekAgentImageDataUrl } from "./agent-image-hydrate";
import "./image-lightbox.css";

let overlayEl: HTMLDivElement | null = null;

function ensureOverlay(): HTMLDivElement {
  if (overlayEl?.isConnected) return overlayEl;

  overlayEl = document.createElement("div");
  overlayEl.className = "ma-image-lightbox hidden";
  overlayEl.setAttribute("role", "dialog");
  overlayEl.setAttribute("aria-modal", "true");
  overlayEl.innerHTML = `
    <button type="button" class="ma-image-lightbox-backdrop" aria-label="关闭预览"></button>
    <figure class="ma-image-lightbox-panel">
      <img class="ma-image-lightbox-img" alt="" />
    </figure>
    <button type="button" class="ma-image-lightbox-close" aria-label="关闭预览">×</button>
  `;
  document.body.appendChild(overlayEl);

  const close = () => closeImageLightbox();
  overlayEl.querySelector<HTMLButtonElement>(".ma-image-lightbox-backdrop")!.addEventListener("click", close);
  overlayEl.querySelector<HTMLButtonElement>(".ma-image-lightbox-close")!.addEventListener("click", close);
  overlayEl.querySelector<HTMLImageElement>(".ma-image-lightbox-img")!.addEventListener("click", close);
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") closeImageLightbox();
  });

  return overlayEl;
}

export function closeImageLightbox(): void {
  if (!overlayEl) return;
  overlayEl.classList.add("hidden");
  const img = overlayEl.querySelector<HTMLImageElement>(".ma-image-lightbox-img");
  if (img) {
    img.removeAttribute("src");
    img.alt = "";
  }
}

export function openImageLightboxFromSrc(src: string, alt = "图片预览"): void {
  const trimmed = src.trim();
  if (!trimmed) return;
  const root = ensureOverlay();
  const img = root.querySelector<HTMLImageElement>(".ma-image-lightbox-img")!;
  img.src = trimmed;
  img.alt = alt;
  root.classList.remove("hidden");
}

export async function openImageLightboxFromAgentPath(
  agentPath: string,
  alt = "图片预览",
): Promise<void> {
  const path = agentPath.trim();
  if (!path) return;
  const cached = peekAgentImageDataUrl(path);
  if (cached) {
    openImageLightboxFromSrc(cached, alt);
    return;
  }
  const dataUrl = await loadAgentImageDataUrl(path);
  if (dataUrl) openImageLightboxFromSrc(dataUrl, alt);
}

/** Click-to-zoom for inline chat / composer thumbnails. */
export function bindImageLightboxClick(
  root: ParentNode,
  selector: string,
): void {
  root.addEventListener("click", (ev) => {
    const target = ev.target;
    if (!(target instanceof Element)) return;
    if (target.closest(".file-drop-chip-remove")) return;

    const zoomBtn = target.closest<HTMLButtonElement>("button.file-drop-chip-zoom");
    if (zoomBtn) {
      ev.preventDefault();
      const path = zoomBtn.dataset.agentPath?.trim();
      const thumb = zoomBtn.querySelector<HTMLImageElement>("img");
      const src = thumb?.src?.trim() ?? "";
      if (src.startsWith("data:")) {
        openImageLightboxFromSrc(src);
        return;
      }
      if (path) void openImageLightboxFromAgentPath(path);
      return;
    }

    const img = target.closest<HTMLImageElement>(selector);
    if (!img) return;
    const src = img.src?.trim() ?? "";
    if (src.startsWith("data:")) {
      ev.preventDefault();
      openImageLightboxFromSrc(src, img.alt || "图片预览");
      return;
    }
    const path = img.dataset.agentPath?.trim();
    if (path) {
      ev.preventDefault();
      void openImageLightboxFromAgentPath(path, img.alt || "图片预览");
    }
  });
}
