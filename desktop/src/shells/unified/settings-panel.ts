/** Settings content is independent of chat rendering and the backend transport. */
export const SETTINGS_SECTIONS = [
  { id: 'appearance', label: '外观与阅读', description: '舒服的界面，刚刚好的阅读节奏。' },
  { id: 'companion', label: '专属搭档', description: '熟悉的名字，你们自己的默契。' },
  { id: 'models', label: '模型与权限', description: '能力从哪里来，边界由你决定。' },
  { id: 'runtime', label: '运行环境', description: '偶尔整理一下，继续轻装上阵。' },
] as const;
export type SettingsSection = typeof SETTINGS_SECTIONS[number]['id'];
export function isSettingsSection(value: string | undefined): value is SettingsSection {
  return SETTINGS_SECTIONS.some(section => section.id === value);
}
function esc(value: string): string {
  return value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
}
export function renderSettingsSidebar(section: SettingsSection = 'appearance'): string {
  return `<nav class="settings-category-nav" aria-label="设置分类">${SETTINGS_SECTIONS.map(item =>
    `<button type="button" data-settings-section="${item.id}"${section === item.id ? ' aria-current="page"' : ''}>${item.label}</button>`
  ).join('')}</nav><button type="button" class="settings-back-chat" data-action="settings-back-chat">← <span>返回聊天</span></button><p class="settings-device-note">偏好留在本机，陪伴始终如一。</p>`;
}
export interface SettingsPaneData {
  section: SettingsSection;
  partnerName: string;
  nicknameDraft: string;
  avatarSrc: string;
  daysTogether: number;
  theme: 'light' | 'dark';
  inactiveTerminals: number;
  stoppedServices: number;
  /** Trusted markup from the existing font control, never user input. */
  fontControlHtml: string;
  feedback: string;
}
const count = (value: number): number => Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0;
export function renderSettingsPane(data: SettingsPaneData): string {
  const section = SETTINGS_SECTIONS.find(item => item.id === data.section) || SETTINGS_SECTIONS[0];
  const name = esc(data.partnerName), avatar = esc(data.avatarSrc);
  let body = '';
  if (section.id === 'appearance') {
    body = `<section class="section" aria-labelledby="settings-theme-heading"><div class="section-heading"><h2 id="settings-theme-heading">界面主题</h2><span class="muted">选一个喜欢的氛围</span></div>
      <div class="theme-options" role="radiogroup" aria-label="界面主题">${(['light', 'dark'] as const).map(theme => `<label class="theme-option"><input type="radio" name="settings-theme" data-settings-theme="${theme}" value="${theme}"${data.theme === theme ? ' checked' : ''} /><span class="theme-mini${theme === 'dark' ? ' is-dark' : ''}" aria-hidden="true"><span class="theme-mini-side"><img src="${avatar}" alt="" /><i class="mini-nav-dot"></i><i class="mini-nav-dot"></i></span><span class="theme-mini-chat"><span class="mini-name">${name}</span><span class="mini-bubble">${theme === 'light' ? '今天，也一起搞定。' : '今晚，我陪着你。'}</span></span></span><span class="theme-meta"><i class="radio" aria-hidden="true"></i><span>${theme === 'light' ? '亮色' : '暗色'}</span><small>${theme === 'light' ? '清透、轻盈' : '安静、沉浸'}</small></span></label>`).join('')}</div></section>
      <section class="section reading-section" aria-label="聊天字号">${data.fontControlHtml}
        <div class="settings-reading-preview"><div class="preview-header"><span>阅读预览</span><span><span data-chat-font-value>14px</span> · 实时预览</span></div><div class="preview-person"><img src="${avatar}" alt="${name}的头像" /><div class="preview-words"><div class="preview-byline">${name}</div><div class="preview-bubble">不用凑近屏幕，调到你舒服的大小就好。</div><p class="preview-prose">短句保留一点俏皮，长文留出呼吸。无论是一小段闲聊，还是认真展开的回答，我都会陪你慢慢看完。</p></div></div></div>
      </section><section class="section row row-section"><div><h3>在终端中继续</h3><p>偏爱命令行？随时切换回熟悉的工作方式。</p></div><button type="button" class="secondary" data-action="settings-switch-cli">改用终端</button></section>`;
  } else if (section.id === 'companion') {
    body = `<section class="section"><div class="profile-head"><img src="${avatar}" alt="${name}的头像" /><div><strong>${name}</strong><p>已经陪你走过 ${Math.max(1, count(data.daysTogether))} 天。</p></div></div><h2>怎么称呼她</h2><p class="page-desc">取一个你叫起来顺口的名字。</p><form class="name-form" data-settings-name-form><input aria-label="搭档昵称" id="settings-partner-name-input" maxlength="24" value="${esc(data.nicknameDraft)}" placeholder="小魔王" autocomplete="off" /><button class="primary" type="submit">保存昵称</button></form></section><section class="section"><h2>性格与说话默契</h2><p class="persona-description">喜欢简短一点，还是多一点调侃？直接在聊天里告诉她。<br />不必在一堆开关中，定义你们的相处方式。</p><details class="quiet-details"><summary>查看配置文件位置</summary><code>evolve/persona.md</code></details></section>`;
  } else if (section.id === 'models') {
    body = `<div class="list-row row"><div><h3>模型密钥</h3><p>管理模型供应商的连接配置。</p></div><button type="button" class="secondary" data-action="settings-open-model-keys">管理密钥</button></div><div class="list-row row"><div><h3>托管区文件夹</h3><p>指定允许读写的本机文件夹，保持访问范围清晰。</p></div><button type="button" class="secondary" data-action="settings-open-host-scope">管理文件夹</button></div><p class="page-note">密钥与访问范围沿用现有配置，在原配置窗口中管理。</p>`;
  } else {
    const terminals = count(data.inactiveTerminals), services = count(data.stoppedServices);
    body = `<div class="list-row row"><div><h3>终端记录</h3><p>失效或已结束的终端。</p></div><span class="status-empty" data-settings-terminal-count>${terminals ? terminals + ' 个可清理' : '暂无可清理记录'}</span></div><div class="list-row row"><div><h3>已停止服务</h3><p>保留中的历史服务记录。</p></div><span class="status-empty" data-settings-service-count>${services ? services + ' 个可清理' : '暂无可清理记录'}</span></div><div class="section row"><div><h3>清理残留记录</h3><p>不影响正在运行的终端和服务。</p></div><button type="button" class="secondary" data-action="settings-prune-runtime"${terminals + services === 0 ? ' disabled' : ''}>${terminals + services === 0 ? '无需清理' : '清理记录'}</button></div><p class="page-note">数量随运行环境刷新；清理仅移除已结束的记录。</p>`;
  }
  return `<div class="settings-pane"><select class="settings-category-mobile" aria-label="设置分类">${SETTINGS_SECTIONS.map(item => `<option value="${item.id}"${section.id === item.id ? ' selected' : ''}>${item.label}</option>`).join('')}</select><header class="page-heading"><div><h1 id="settings-page-heading">${section.label}</h1><p class="page-desc">${section.description}</p></div>${section.id === 'appearance' ? '<span class="saved-state">更改自动保存</span>' : ''}</header>${body}<p class="settings-feedback" role="status" aria-live="polite">${esc(data.feedback)}</p></div>`;
}
