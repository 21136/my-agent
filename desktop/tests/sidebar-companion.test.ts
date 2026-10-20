import assert from "node:assert/strict";
import test from "node:test";
import { renderProjectCompanionDockHtml, type ProjectCompanionDock } from "../src/shells/unified/sidebar-companion.ts";
function render(options: Partial<ProjectCompanionDock> = {}): string {
  return renderProjectCompanionDockHtml({partnerName: "小魔王", daysTogether: 55, projectId: "imported-b1f2b097", avatarSrc: "./pet-assets/avatar/pet-idle-still.webp", ...options});
}
function front(html: string): string { return html.replace(/(?:title|aria-label)="[^"]*"/g, ""); }

test("project dock keeps companion identity first and internal IDs out of the visible copy", () => {
  const html = render({projectName: "毕业设计", projectDirectory: "F:\\课题\\毕业设计", branch: "master"});
  assert.match(front(html), /小魔王 · 相伴 55 天/);
  assert.match(front(html), /正在陪你做「毕业设计」/);
  assert.doesNotMatch(front(html), /imported-b1f2b097|master|F:/);
  assert.ok(html.indexOf("小魔王 · 相伴") < html.indexOf("正在陪你做"));
});

test("project display name falls back to a real Windows or POSIX directory, never a generated ID", () => {
  for (const projectDirectory of ["F:\\研究项目\\毕业设计\\", "/work/毕业设计/"]) {
    assert.match(front(render({projectDirectory})), /正在陪你做「毕业设计」/);
  }
  assert.match(front(render({projectName: "imported-b1f2b097", projectDirectory: "F:/项目/毕业设计"})), /正在陪你做「毕业设计」/);
  assert.match(front(render()), /正在陪你做项目/);
  assert.doesNotMatch(front(render()), /imported-b1f2b097/);
});

test("normal English project names are preserved, not translated or treated as IDs", () => {
  assert.match(front(render({projectId: "music-dreamer", projectName: "Music Dreamer"})), /正在陪你做「Music Dreamer」/);
  assert.match(front(render({projectId: "music", projectName: "", projectDirectory: ""})), /正在陪你做「music」/);
});

test("switch action remains accessible and technical facts remain in the hover detail", () => {
  const html = render({projectName: "毕业设计", projectDirectory: "F:/项目/毕业设计", branch: "master", verification: "上次验证 ✓ 16:45"});
  assert.match(html, /data-sidebar-action="open-projects"/);
  assert.match(html, /aria-label="切换项目，当前：毕业设计"/);
  assert.match(html, /title="[^\"]*项目编号：imported-b1f2b097/);
  assert.match(html, /目录：F:\/项目\/毕业设计/);
  assert.match(html, /分支：master/);
  assert.match(html, /上次验证 ✓ 16:45/);
  assert.doesNotMatch(front(html), /上次验证|16:45/);
});

test("untrusted names and paths cannot inject HTML or break the tooltip", () => {
  const html = render({partnerName: '<img src=x onerror="oops">', projectName: '项目"><script>oops</script>', projectDirectory: 'F:/" autofocus="true', branch: '<bad>'});
  assert.doesNotMatch(html, /<script>|<img src=x| autofocus="true/);
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /&quot;/);
  assert.equal((html.match(/<button\b/g) || []).length, 1);
});
