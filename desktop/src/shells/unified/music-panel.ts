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
    <div class="music-web-slot" data-music-web-slot hidden aria-label="B 站搜索结果与播放器"></div>
  </div>`;
}
