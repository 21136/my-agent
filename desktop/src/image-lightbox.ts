import { loadAgentImageDataUrl, peekAgentImageDataUrl } from "./agent-image-hydrate";
import "./image-lightbox.css";

const MIN_SCALE = 0.25;
const MAX_SCALE = 4;
const STEP = 0.25;

let overlayEl: HTMLDivElement | null = null;
let scale = 1;

function clampScale(next: number): number {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, Math.round(next * 100) / 100));
}

function renderScale(): void {
  if (!overlayEl) return;
  const img = overlayEl.querySelector<HTMLImageElement>(".ma-image-lightbox-img");
  const value = overlayEl.querySelector<HTMLOutputElement>("[data-ma-lightbox-scale]");
  if (img) img.style.transform = `scale(${scale})`;
  if (value) value.value = `${Math.round(scale * 100)}%`;
}

function changeScale(delta: number): void {
  scale = clampScale(scale + delta);
  renderScale();
}

function resetScale(): void {
  scale = 1;
  renderScale();
}

function ensureOverlay(): HTMLDivElement {
  if (overlayEl?.isConnected) return overlayEl;

  overlayEl = document.createElement("div");
  overlayEl.className = "ma-image-lightbox hidden";
  overlayEl.setAttribute("role", "dialog");
  overlayEl.setAttribute("aria-modal", "true");
  overlayEl.setAttribute("aria-label", "图片预览");
  overlayEl.innerHTML = `
    <button type="button" class="ma-image-lightbox-backdrop" aria-label="关闭预览"></button>
    <div class="ma-image-lightbox-toolbar" role="toolbar" aria-label="缩放">
      <button type="button" class="ma-image-lightbox-tool" data-ma-zoom-out aria-label="缩小">−</button>
      <output data-ma-lightbox-scale>100%</output>
      <button type="button" class="ma-image-lightbox-tool" data-ma-zoom-in aria-label="放大">+</button>
      <button type="button" class="ma-image-lightbox-tool" data-ma-zoom-reset>适应窗口</button>
    </div>
    <div class="ma-image-lightbox-stage">
      <figure class="ma-image-lightbox-panel">
        <img class="ma-image-lightbox-img" alt="" draggable="false" />
      </figure>
    </div>
    <button type="button" class="ma-image-lightbox-close" aria-label="关闭预览">×</button>
  `;
  document.body.appendChild(overlayEl);

  const close = () => closeImageLightbox();
  overlayEl.querySelector<HTMLButtonElement>(".ma-image-lightbox-backdrop")!.addEventListener("click", close);
  overlayEl.querySelector<HTMLButtonElement>(".ma-image-lightbox-close")!.addEventListener("click", close);

  overlayEl.querySelector<HTMLButtonElement>("[data-ma-zoom-in]")!.addEventListener("click", (ev) => {
    ev.stopPropagation();
    changeScale(STEP);
  });
  overlayEl.querySelector<HTMLButtonElement>("[data-ma-zoom-out]")!.addEventListener("click", (ev) => {
    ev.stopPropagation();
    changeScale(-STEP);
  });
  overlayEl.querySelector<HTMLButtonElement>("[data-ma-zoom-reset]")!.addEventListener("click", (ev) => {
    ev.stopPropagation();
    resetScale();
  });

  const stage = overlayEl.querySelector<HTMLElement>(".ma-image-lightbox-stage");
  stage?.addEventListener(
    "wheel",
    (ev) => {
      if (overlayEl?.classList.contains("hidden")) return;
      ev.preventDefault();
      changeScale(ev.deltaY < 0 ? STEP : -STEP);
    },
    { passive: false },
  );

  overlayEl.addEventListener("keydown", (ev) => {
    if (overlayEl.classList.contains("hidden")) return;
    if (ev.key === "Escape") close();
    else if (ev.key === "+" || ev.key === "=") changeScale(STEP);
    else if (ev.key === "-") changeScale(-STEP);
    else if (ev.key === "0") resetScale();
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
    img.style.transform = "";
  }
  scale = 1;
  renderScale();
}

export function openImageLightboxFromSrc(src: string, alt = "图片预览"): void {
  const trimmed = src.trim();
  if (!trimmed) return;
  const root = ensureOverlay();
  const img = root.querySelector<HTMLImageElement>(".ma-image-lightbox-img")!;
  img.src = trimmed;
  img.alt = alt;
  resetScale();
  root.classList.remove("hidden");
  root.querySelector<HTMLButtonElement>(".ma-image-lightbox-close")?.focus();
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

/** Generated images, composer chips, and markdown inline images. */
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

    const markdownImg =
      target instanceof HTMLImageElement && target.closest(".unified-markdown")
        ? target
        : null;
    const generatedImg = target.closest<HTMLImageElement>(selector);
    const img = generatedImg ?? markdownImg;
    if (!img) return;

    const src = img.src?.trim() ?? "";
    if (src.startsWith("data:") || src.startsWith("blob:") || src.startsWith("http")) {
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
