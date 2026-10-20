/** Synthetic real-shell QA: verifies no-paste search is routed through the narrow preload API. */
export async function runMusicSearchBrowserTests(root: HTMLElement, requests: Array<{ query: string; bounds: { x: number; y: number; width: number; height: number } }>, viewStates: Array<{ visible: boolean }>): Promise<string[]> {
  const results: string[] = [];
  const check = (ok: unknown, label: string): void => { if (!ok) throw Error(label); results.push(label); };
  const musicButton = root.querySelector<HTMLButtonElement>('[data-panel="music"]');
  if (!musicButton) throw Error('missing music rail entry');
  const chat = root.querySelector<HTMLElement>('#unified-chat')!;
  const composer = root.querySelector<HTMLElement>('#unified-composer-dock')!;
  musicButton.click();
  const panel = root.querySelector<HTMLElement>('#unified-music')!;
  check(!panel.hidden && chat.hidden && composer.hidden, '听歌页独立显示并隐藏聊天输入区');
  check(root.querySelector('#sidebar-task-flow')?.textContent?.includes('结果直接显示在听歌页'), '左侧提示在主区搜索，不要求复制 BV');
  const input = panel.querySelector<HTMLInputElement>('#music-search-query')!;
  const form = panel.querySelector<HTMLFormElement>('[data-music-search-form]')!;
  form.requestSubmit();
  check(panel.querySelector('.music-feedback')?.textContent === '先输入歌名或歌手。' && requests.length === 0, '空搜索有提示且不打开页面');
  input.value = '  夜曲  ';
  form.requestSubmit();
  await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
  await Promise.resolve(); await Promise.resolve();
  check(requests.length === 1 && requests[0].query === '夜曲', '输入歌名后调用窄权限搜索桥接并自动去除空格');
  const slotRect = panel.querySelector<HTMLElement>('[data-music-web-slot]')!.getBoundingClientRect();
  check(requests[0].bounds.width > 100 && requests[0].bounds.height > 100 && !panel.querySelector<HTMLElement>('[data-music-web-slot]')!.hidden, '搜索结果获得听歌区域边界并嵌入当前页面');
  check(requests[0].bounds.x >= Math.floor(slotRect.left) && requests[0].bounds.x < Math.ceil(slotRect.right), '内嵌视图边界限制在听歌区域而非全窗口');
  check(panel.querySelector('.music-feedback')?.textContent === 'B 站搜索结果已显示在听歌页。', '搜索打开后显示确认状态');
  root.querySelector<HTMLButtonElement>('[data-panel="now"]')!.click();
  check(viewStates.some(state => !state.visible), '离开听歌页时隐藏内嵌 B 站视图');
  root.querySelector<HTMLButtonElement>('[data-panel="music"]')!.click();
  check(!panel.querySelector<HTMLElement>('[data-music-web-slot]')!.hidden && viewStates.at(-1)?.visible === true, '返回听歌页时恢复同一内嵌播放区域');
  root.querySelector<HTMLButtonElement>('[data-panel="now"]')!.click();
  check(!chat.hidden && root.querySelector('#unified-chat') === chat, '返回聊天仍是原会话节点');
  return results;
}
