import { readChatFontSize, readTheme, writeChatFontSize, writeTheme } from '../src/settings';
/** Isolated real-shell regression suite; transport hooks are synthetic, never live. */
export function runSettingsPanelBrowserTests(root: HTMLElement, runtimeUpdate: () => void, calls: string[]): string[] {
  const results: string[] = [];
  const check = (ok: unknown, label: string): void => { if (!ok) throw Error(label); results.push(label); };
  const click = (selector: string): void => { const el=root.querySelector<HTMLButtonElement>(selector); if(!el)throw Error('missing '+selector);el.click(); };
  const savedName = localStorage.getItem('partner_name');
  const savedSize = readChatFontSize(), savedTheme = readTheme();
  const chat = root.querySelector<HTMLElement>('#unified-chat')!;
  const composer = root.querySelector<HTMLTextAreaElement>('#unified-input')!;
  const messageText = (): string => Array.from(chat.querySelectorAll(".unified-turn-body")).map(el => el.textContent).join("\n");
  const originalText = messageText();
  const chromeTitle = document.querySelector<HTMLElement>('.app-chrome-title');
  const savedTitle = chromeTitle?.textContent ?? '';
  try {
  composer.value = '尚未发送的草稿';
  click('[data-panel="settings"]');
  const stage=root.querySelector<HTMLElement>('#unified-settings')!;
  check(!stage.hidden && chat.hidden && root.querySelector<HTMLElement>('#unified-composer-dock')!.hidden, '设置独占主区，聊天与输入区隐藏');
  check(root.querySelector('#sidebar-task-flow [data-chat-font-control]') === null, '侧栏只显示分类，不再塞设置表单');
  const slider=stage.querySelector<HTMLInputElement>('[data-chat-font-input]')!;
  slider.focus();slider.value='18';slider.dispatchEvent(new Event('input',{bubbles:true}));
  runtimeUpdate();
  check(stage.querySelector('[data-chat-font-input]')===slider && document.activeElement===slider, '运行事件刷新不打断字号调整与焦点');
  check(readChatFontSize()===18 && getComputedStyle(stage.querySelector('.preview-prose')!).fontSize==='18px', '字号保存与真实阅读预览一致');
  click('[data-chat-font-step="1"]');
  check(readChatFontSize()===19, '加号逐级增大字号');
  click('[data-chat-font-step="-1"]');check(readChatFontSize()===18,'减号逐级缩小字号');
  slider.value='20';slider.dispatchEvent(new Event('input',{bubbles:true}));
  check(stage.querySelector<HTMLButtonElement>('[data-chat-font-step="1"]')!.disabled,'最大字号禁止继续增大');
  click('[data-chat-font-reset]');check(readChatFontSize()===14,'恢复默认字号有效');
  const dark=stage.querySelector<HTMLInputElement>('[data-settings-theme="dark"]')!;dark.click();
  check(document.documentElement.dataset.theme==='dark' && readTheme()==='dark','主题选择使用真实持久化入口');
  check(stage.querySelector('[data-chat-font-input]')===slider, '切换主题不重建字号控件');
  stage.querySelector<HTMLInputElement>('[data-settings-theme="light"]')!.click();
  click('[data-settings-section="companion"]');
  const nickname=stage.querySelector<HTMLInputElement>('#settings-partner-name-input')!;
  nickname.value='搭档测试';nickname.dispatchEvent(new Event('input',{bubbles:true}));nickname.focus();runtimeUpdate();
  check(stage.querySelector('#settings-partner-name-input')===nickname && nickname.value==='搭档测试' && document.activeElement===nickname,'运行事件保留未保存昵称与焦点');
  click('[data-settings-section="models"]');click('[data-settings-section="companion"]');
  check(stage.querySelector<HTMLInputElement>('#settings-partner-name-input')!.value==='搭档测试','切换分类保留未保存昵称');
  stage.querySelector<HTMLFormElement>('form')!.requestSubmit();
  check(localStorage.getItem('partner_name')==='搭档测试' && stage.textContent!.includes('昵称已保存'),'昵称提交真实保存并反馈');
  click('[data-settings-section="models"]');
  click('[data-action="settings-open-model-keys"]');
  const model=document.querySelector<HTMLElement>('[aria-labelledby="model-keys-title"]')!;
  check(!!model && !model.closest('.host-settings-overlay')!.classList.contains('hidden'),'模型入口打开现有真实配置窗口');
  model.querySelector<HTMLButtonElement>('[data-action="close"]')!.click();
  click('[data-action="settings-open-host-scope"]');
  const host=document.querySelector<HTMLElement>('.host-settings-overlay:not(.hidden)')!;
  check(!!host && !host.contains(model),'文件夹入口打开现有真实托管区窗口');
  host.querySelector<HTMLButtonElement>('[data-action="close"]')!.click();
  click('[data-settings-section="runtime"]');
  check(stage.textContent!.includes('1 个可清理'),'运行环境显示合成事件提供的实际记录数量');
  click('[data-action="settings-prune-runtime"]');
  check(calls.includes('pruneTerminals') && calls.includes('pruneServices'),'清理入口发出原有两类清理调用');
  check(stage.textContent!.includes('已请求清理') && !stage.textContent!.includes('已清理失效'),'清理反馈不提前宣称成功');
  click('[data-action="settings-back-chat"]');
  check(!chat.hidden && stage.hidden && messageText()===originalText,'返回原会话，不创建或替换聊天内容');
  check(composer.value==='尚未发送的草稿','进出设置保留输入草稿');
  check(root.querySelector('#unified-chat')===chat,'进出设置保留原聊天节点');
  return results;
  } finally {
  if(savedName===null)localStorage.removeItem('partner_name');else localStorage.setItem('partner_name',savedName);
  writeChatFontSize(savedSize);writeTheme(savedTheme);composer.value='';
  if (chromeTitle) chromeTitle.textContent = savedTitle;
  }
}
