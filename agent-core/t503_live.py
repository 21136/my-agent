"""T-503 interactive live walkthrough — real LLM + REPL (run from agent-core/)."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from main import ConversationRepl, ReplConfig
from paths import AgentPaths
from router import apply_topic_confirmation, resolve_topic_confirmation
from session import create_new

INBOX_REL = "_downloads_inbox"
SAMPLE_FILES = {
    "report.pdf": "pdf",
    "notes.txt": "txt",
    "photo.jpg": "jpg",
    "README": "no ext",
}


def _reset_inbox(paths: AgentPaths) -> Path:
    inbox = paths.workspace / INBOX_REL
    if inbox.exists():
        for child in sorted(inbox.rglob("*"), reverse=True):
            if child.is_file():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
    inbox.mkdir(parents=True, exist_ok=True)
    for name, body in SAMPLE_FILES.items():
        (inbox / name).write_text(body, encoding="utf-8")
    return inbox


def _print_banner() -> None:
    print("=" * 60)
    print("T-503 交互式验收 — sort_by_extension")
    print("=" * 60)
    print(f"测试目录: workspace/{INBOX_REL}/")
    print("当前应有 4 个顶层文件: report.pdf, notes.txt, photo.jpg, README")
    print()
    print("建议输入（可复制）：")
    print(f"  请用 sort_by_extension 整理 {INBOX_REL} 目录，先 dry_run 预览再正式执行")
    print()
    print("run_evolved 确认时：输入 y（或 a 本会话免确认）")
    print("结束后输入 exit 保存并退出")
    print("=" * 60)
    print()


def main() -> int:
    paths = AgentPaths.discover()
    _reset_inbox(paths)
    _print_banner()

    session_dir = paths.data / "sessions" / "_t503_live"
    if session_dir.exists():
        shutil.rmtree(session_dir)

    session = create_new(paths, conversation_id="_t503_live")
    session.set_goal(f"整理 workspace/{INBOX_REL} 下载夹，按扩展名分类")
    from router import TopicProposal

    confirmation = resolve_topic_confirmation(
        "workflow",
        TopicProposal(topics=("workflow",), reason="t503 setup"),
        valid_topic_ids={"workflow", "coding", "writing", "safety"},
    )
    apply_topic_confirmation(session, confirmation, mode="replace")
    session.save()

    repl = ConversationRepl.from_session(session, paths=paths, config=ReplConfig())
    print(f"session: {session.conversation_id} | topics: {session.meta.topics}")
    print(f"evolved 清单应含: write_text, sort_by_extension")
    print()
    return repl.run()


if __name__ == "__main__":
    raise SystemExit(main())
