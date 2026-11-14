export function renderMusicPanel(): string {
  return `<div class="music-page">
    <header class="music-page-heading">
      <div><h1 id="music-page-heading">听歌</h1><p>搜歌、点开、播放，都在这个页面里。</p></div>
    </header>
    <form class="music-search-form" data-music-search-form>
      <label class="music-search-box" for="music-search-query">
        <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.8" cy="10.8" r="6.8"></circle><path d="m16 16 4.2 4.2"></path></svg>
        <input id="music-search-query" name="query" type="search" aria-label="输入歌名或歌手" maxlength="200" placeholder="输入歌名或歌手" autocomplete="off" />
      </label>
      <button class="music-search-submit" type="submit">搜索 B 站</button>
    </form>
    <p class="music-search-help">搜索结果就在这里播放，不用复制 BV 号。</p>
    <p class="music-feedback" role="status" aria-live="polite"></p>
    <div class="music-empty-state" data-music-empty-state aria-labelledby="music-empty-title">
      <svg viewBox="0 0 64 64" aria-hidden="true" focusable="false">
        <circle cx="31" cy="33" r="20"></circle><circle cx="31" cy="33" r="12"></circle><circle cx="31" cy="33" r="3"></circle>
        <path d="M43 16V7l11-2v9M43 10l11-2M43 16a3 3 0 1 0 3 3V8"></path>
      </svg>
      <div><h2 id="music-empty-title">开始听点喜欢的</h2><p>输入歌名或歌手，搜索结果会直接显示在这里。</p></div>
    </div>
    <div class="music-web-slot" data-music-web-slot hidden aria-label="B 站搜索结果与播放器"></div>
  </div>`;
}
