/** Build a Bilibili search URL from a title only; callers cannot supply arbitrary destinations. */
export function buildBilibiliSearchUrl(rawQuery: unknown): string | null {
  if (typeof rawQuery !== 'string') return null;
  const query = rawQuery.trim().replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ');
  if (!query || query.length > 200) return null;
  return `https://search.bilibili.com/all?${new URLSearchParams({ keyword: query })}`;
}

/** Only these HTTPS origins remain inside the no-preload Bilibili window. */
export function isOfficialBilibiliUrl(candidate: string): boolean {
  try {
    const parsed = new URL(candidate);
    const host = parsed.hostname.toLowerCase();
    return parsed.protocol === 'https:' && !parsed.username && !parsed.password && !parsed.port &&
      (host === 'bilibili.com' || host.endsWith('.bilibili.com') || host === 'b23.tv' || host.endsWith('.b23.tv'));
  } catch { return false; }
}
