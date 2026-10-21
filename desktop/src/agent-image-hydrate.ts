/** Load agent-relative image paths into <img> via Electron IPC (shared cache). */

const agentImageDataUrlCache = new Map<string, string>();

export function peekAgentImageDataUrl(agentPath: string): string | undefined {
  return agentImageDataUrlCache.get(agentPath.trim());
}

export async function loadAgentImageDataUrl(agentPath: string): Promise<string | null> {
  const path = agentPath.trim();
  if (!path) return null;
  const cached = agentImageDataUrlCache.get(path);
  if (cached) return cached;
  const api = window.myAgentDesktop?.readAgentImageDataUrl;
  if (!api) return null;
  try {
    const dataUrl = await api(path);
    if (!dataUrl) return null;
    agentImageDataUrlCache.set(path, dataUrl);
    return dataUrl;
  } catch {
    return null;
  }
}

export async function hydrateAgentImageElements(
  root: ParentNode,
  selector = "img[data-agent-path]",
): Promise<void> {
  const api = window.myAgentDesktop?.readAgentImageDataUrl;
  const imgs = root.querySelectorAll<HTMLImageElement>(selector);
  await Promise.all(
    [...imgs].map(async (img) => {
      const agentPath = img.dataset.agentPath?.trim();
      if (!agentPath) return;

      const cached = agentImageDataUrlCache.get(agentPath);
      if (cached) {
        if (img.src !== cached) img.src = cached;
        img.dataset.hydrated = "1";
        return;
      }
      if (img.dataset.hydrated === "1" && img.src.startsWith("data:")) return;

      if (!api) {
        img.alt = img.alt || "请完全退出并重启桌面壳后再试图片预览";
        return;
      }
      try {
        const dataUrl = await api(agentPath);
        if (!dataUrl) {
          img.alt = img.alt || "图片预览失败（路径或文件不存在）";
          return;
        }
        agentImageDataUrlCache.set(agentPath, dataUrl);
        img.src = dataUrl;
        img.dataset.hydrated = "1";
      } catch {
        img.alt = img.alt || "图片预览失败（读取异常）";
      }
    }),
  );
}
