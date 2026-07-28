"""Activity routing: infer shell + topic from turn context (DESKTOP P1)."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from session import Session, session_banner_event
from turn_intent import TurnIntent

ShellId = Literal["grow", "daily", "govern", "project"]

_SCOPE_STATIC = frozenset({"coding", "workflow", "data", "common"})


def _scope_pattern(paths: AgentPaths | None = None) -> re.Pattern[str]:
    from router import registered_topic_ids

    agent_paths = paths or AgentPaths.discover()
    scopes = sorted(_SCOPE_STATIC | registered_topic_ids(agent_paths))
    inner = "|".join(re.escape(scope) for scope in scopes)
    return re.compile(rf"evolve/tools/(?P<scope>{inner})/", re.IGNORECASE)


_WORKFLOW_TOOL_NAMES = frozenset(
    {
        "sort_by_extension",
        "rename_batch",
        "flatten_dir",
        "dedupe_by_name",
        "archive_by_date",
        "study_note",
    }
)
_GROW_EXECUTE_MARKERS = (
    "造",
    "实现",
    "scaffold",
    "write_evolve",
    "tool.toml",
    "新工具",
    "加工具",
    "注册工具",
    "proposal",
    "proposals",
    "write_evolve",
)
_GOVERN_MARKERS = ("review", "audit", "治理", "suspect", "governance")
_PROJECT_MARKERS = ("做项目", "项目模式", "项目 新建", "项目 打开", "workspace/")


def _project_topics(session: Session) -> tuple[str, ...]:
    return ("coding",) if "coding" not in session.meta.topics else ()


def _is_project_turn(text: str, lower: str) -> bool:
    if any(marker in text or marker in lower for marker in _PROJECT_MARKERS):
        return True
    if "workspace/" in lower:
        return True
    if text.startswith("项目"):
        return True
    return False


def _is_grow_exclusive(text: str, lower: str) -> bool:
    if any(marker in text or marker in lower for marker in _GROW_EXECUTE_MARKERS):
        return True
    if "evolve/" in lower and "workspace/" not in lower:
        return True
    if lower.strip() == "proposals" or "proposal" in lower:
        return True
    return False


def _is_explicit_grow_turn(text: str, lower: str) -> bool:
    """Strong signals to leave project shell for grow."""
    markers = (
        "write_evolve",
        "tool.toml",
        "proposal",
        "proposals",
        "新工具",
        "加工具",
        "注册工具",
        "evolve/",
    )
    return any(marker in text or marker in lower for marker in markers)


@dataclass(frozen=True, slots=True)
class ActivityRoute:
    shell: ShellId
    topics_to_add: tuple[str, ...]
    reason: str


def infer_topic_scope(text: str, *, paths: AgentPaths | None = None) -> str | None:
    """Infer evolve topic id from paths or tool names in user text."""
    match = _scope_pattern(paths).search(text)
    if match:
        scope = match.group("scope").lower()
        return "common" if scope == "common" else scope

    lower = text.casefold()
    for name in _WORKFLOW_TOOL_NAMES:
        if name in lower:
            return "workflow"

    coding_markers = ("coding", "git_snapshot", "patch_file", "run_tests", "run_demo", "code_scaffold")
    for marker in coding_markers:
        if marker in lower:
            return "coding"

    if "csv" in lower or "data/" in lower or "csv_head" in lower:
        return "data"

    if "data" in lower and ("主题" in text or "工具" in text):
        return "data"

    return None


def compute_activity_route(
    *,
    user_text: str,
    intent: TurnIntent,
    session: Session,
    paths: AgentPaths,
    pending_proposals: int = 0,
) -> ActivityRoute:
    """Infer shell + topics to append for one user turn."""
    text = user_text.strip()
    lower = text.casefold()

    if any(marker in lower for marker in _GOVERN_MARKERS):
        return ActivityRoute("govern", (), "治理 / 审查相关")

    from project_mode import project_plan_gate_open

    if project_plan_gate_open(session.meta):
        return ActivityRoute("project", _project_topics(session), "项目 · 计划待确认")

    if pending_proposals > 0:
        return ActivityRoute("grow", (), f"{pending_proposals} 条 proposal 待处理")

    if session.meta.active_shell == "project" and session.meta.project_root:
        if not _is_explicit_grow_turn(text, lower):
            return ActivityRoute("project", _project_topics(session), "续接 · 项目模式")

    if _is_grow_exclusive(text, lower):
        scope = infer_topic_scope(text, paths=paths)
        topics = (scope,) if scope and scope not in {"common", "workflow"} else ("coding",)
        return ActivityRoute("grow", topics, "养 agent / evolved")

    if _is_project_turn(text, lower):
        return ActivityRoute("project", _project_topics(session), "workspace 项目")

    scope = infer_topic_scope(text, paths=paths)

    if scope == "workflow" or any(name in lower for name in _WORKFLOW_TOOL_NAMES):
        topics = ("workflow",) if "workflow" not in session.meta.topics else ()
        return ActivityRoute("daily", topics, "使用 workflow 工具")

    grow_execute = intent == "execute" and any(
        marker in text or marker in lower for marker in _GROW_EXECUTE_MARKERS
    )
    if grow_execute or (intent == "execute" and scope in {"coding", "data"}):
        if "workspace/" in lower:
            return ActivityRoute("project", _project_topics(session), "workspace 开发")
        topics = (scope,) if scope and scope not in {"common", "workflow"} else ("coding",)
        return ActivityRoute("grow", topics, "造 / 改 evolved 工具")

    if intent == "research" and ("evolve/tools" in lower or "探索" in text or "调研" in text):
        topics = (scope,) if scope and scope not in {"common", "workflow"} else ()
        return ActivityRoute("grow", topics, "只读探索 evolve 资产")

    if intent in {"qa", "recall", "plan"}:
        return ActivityRoute("daily", (), "对话 / 方案")

    if "coding" in session.meta.topics:
        return ActivityRoute("grow", (), "当前主题为 coding")

    return ActivityRoute("daily", (), "继续当前任务")


def compute_session_route(session: Session, paths: AgentPaths) -> ActivityRoute:
    """Infer shell when connecting or refreshing (no new user line)."""
    from evolve import list_pending_proposals

    pending = len(list_pending_proposals(paths))
    if pending > 0:
        return ActivityRoute("grow", (), f"{pending} 条 proposal 待处理")

    if session.meta.active_shell == "project" and session.meta.project_root:
        label = session.meta.project_id or session.meta.project_root
        return ActivityRoute("project", (), f"续接 · 项目 · {label}")

    topics = session.meta.topics
    if "coding" in topics and session.meta.active_shell == "grow":
        return ActivityRoute("grow", (), "续接 · coding 主题")
    if topics:
        return ActivityRoute("daily", (), f"续接 · {', '.join(topics)}")
    return ActivityRoute("daily", (), "续接会话")


def should_persist_activity_shell(current_shell: str, route: ActivityRoute) -> bool:
    """Whether activity_route may rewrite session.meta.active_shell (BUG-020 / STD-001).

    Soft grow↔daily suggestions (e.g. qa → daily) still emit ``ui.route`` for the
    desktop, but must not change the session's owned shell line.
    """
    target = route.shell
    if current_shell == target:
        return False
    if current_shell == "project" or target == "project":
        return True
    if current_shell == "govern" or target == "govern":
        return True
    # Never demote a grow-owned turn to daily via activity inference.
    if current_shell == "grow" and target == "daily":
        return False
    # daily → grow (proposals / write_evolve / coding) is intentional.
    if current_shell == "daily" and target == "grow":
        return True
    return True


def apply_route_topics(session: Session, topics_to_add: tuple[str, ...]) -> bool:
    """Append validated topics; return True if session meta changed."""
    if not topics_to_add:
        return False

    from router import TopicRoutingError, apply_confirmed_topics, registered_topic_ids

    missing = [topic for topic in topics_to_add if topic not in session.meta.topics]
    if not missing:
        return False

    try:
        apply_confirmed_topics(
            session,
            missing,
            mode="append",
            valid_topic_ids=registered_topic_ids(session.paths),
        )
    except TopicRoutingError:
        return False
    return True


def ui_route_payload(session: Session, route: ActivityRoute) -> dict[str, Any]:
    added = [topic for topic in route.topics_to_add if topic in session.meta.topics]
    return {
        "type": "ui.route",
        "shell": route.shell,
        "topics": list(session.meta.topics),
        "topics_added": added,
        "reason": route.reason,
        "auto": True,
    }


def emit_activity_route(
    emit: Any,
    session: Session,
    route: ActivityRoute,
    *,
    topics_changed: bool,
) -> None:
    """Push ui.route and refresh banner when topics changed."""
    emit(ui_route_payload(session, route))
    if topics_changed:
        emit(session_banner_event(session))


def _demo() -> None:
    paths = AgentPaths.discover()
    from session import create_new

    demo_dir = paths.data / "sessions" / "_activity_route_demo"
    if demo_dir.is_dir():
        import shutil

        shutil.rmtree(demo_dir)

    session = create_new(paths, conversation_id="_activity_route_demo")

    route = compute_activity_route(
        user_text="帮我造一个 coding 工具 foo",
        intent="execute",
        session=session,
        paths=paths,
    )
    assert route.shell == "grow"
    assert route.topics_to_add == ("coding",)
    print("[PASS] execute + 造工具 → grow + coding")

    changed = apply_route_topics(session, route.topics_to_add)
    assert changed and "coding" in session.meta.topics
    print("[PASS] apply_route_topics appends coding")

    wf = compute_activity_route(
        user_text="用 sort_by_extension 整理 inbox",
        intent="execute",
        session=session,
        paths=paths,
    )
    assert wf.shell == "daily"
    assert "workflow" in wf.topics_to_add
    print("[PASS] workflow tool → daily + workflow")

    recall = compute_activity_route(
        user_text="刚刚我们说了什么",
        intent="recall",
        session=session,
        paths=paths,
    )
    assert recall.shell == "daily"
    assert recall.topics_to_add == ()
    print("[PASS] recall → daily, no topic change")

    data_scope = compute_activity_route(
        user_text="write_evolve evolve/tools/data/csv_head/tool.toml",
        intent="execute",
        session=session,
        paths=paths,
    )
    assert data_scope.shell == "grow"
    assert "data" in data_scope.topics_to_add
    print("[PASS] evolve/tools/data/ path → grow + data")

    session.meta.project_root = "workspace/demo"
    session.meta.project_id = "demo"
    session.meta.active_shell = "project"
    bound = compute_activity_route(
        user_text="继续实现",
        intent="execute",
        session=session,
        paths=paths,
    )
    assert bound.shell == "project"
    print("[PASS] bound project session → project")

    session.meta.project_plan_status = "draft"
    gated = compute_activity_route(
        user_text="开始写代码",
        intent="execute",
        session=session,
        paths=paths,
        pending_proposals=3,
    )
    assert gated.shell == "project"
    assert "计划待确认" in gated.reason
    print("[PASS] plan gate beats pending proposals")

    payload = ui_route_payload(session, route)
    assert payload["type"] == "ui.route" and payload["shell"] == "grow"
    print("[PASS] ui_route_payload")

    if demo_dir.is_dir():
        import shutil

        shutil.rmtree(demo_dir)


if __name__ == "__main__":
    _demo()
