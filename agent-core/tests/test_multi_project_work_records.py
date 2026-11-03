from __future__ import annotations

import json
import uuid
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_record_cards import build_project_work_record_cards
from work_card_ledger import WorkCardLedger
from work_card_presentations import WorkCardPresentations
import work_record_visibility_clock
from multi_project_work_records import render_selected_projects_overview


class Fixture:
    def __init__(self, root: Path, project_count: int = 6):
        self.root = root
        self.paths = AgentPaths(
            agent_root=root,
            workspace=root / "workspace",
            data=root / "data",
            evolve=root / "evolve",
        )
        self.current_id = "20261030-aaaaaaaa"
        current_dir = self.paths.data / "sessions" / self.current_id
        current_dir.mkdir(parents=True)
        self.current = Session(
            conversation_id=self.current_id,
            session_dir=current_dir,
            goal="current goal must not be read",
            meta=SessionMeta(),
            messages=[],
            paths=self.paths,
        )
        current_store = WorkRecordStore(self.current)
        independent = current_store.create_item(
            title="current independent item", scope={"kind": "independent"},
        )
        current_store.add_requirement(
            independent["item_id"], "current independent requirement A", event_id="current-independent-a",
        )
        current_store.add_requirement(
            independent["item_id"], "current independent requirement B", event_id="current-independent-b",
        )
        self.project_ids = [f"project-{index}" for index in range(1, project_count + 1)]
        self.session_ids = {pid: f"20261030-{index:08x}" for index, pid in enumerate(self.project_ids, 1)}
        self.state_path = self.paths.data / "state.json"
        self.write_mapping()
        for project_id in self.project_ids:
            self.create_project_session(project_id)

    def write_mapping(self, mapping: dict[str, str] | None = None) -> None:
        pairs = mapping if mapping is not None else self.session_ids
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({"project_sessions": pairs}), encoding="utf-8")

    def create_project_session(self, project_id: str, *, session_id: str | None = None) -> Session:
        sid = session_id or self.session_ids[project_id]
        session_dir = self.paths.data / "sessions" / sid
        session_dir.mkdir(parents=True, exist_ok=True)
        meta_path = session_dir / "meta.json"
        meta_path.write_text(json.dumps({"project_id": project_id}), encoding="utf-8")
        session = Session(sid, session_dir, "SECRET GOAL", SessionMeta(project_id=project_id), [], self.paths)
        store = WorkRecordStore(session)
        item = store.create_item(title=f"{project_id} task", scope={"kind": "project", "project_id": project_id})
        store.add_requirement(item["item_id"], f"{project_id} requirement A", event_id=f"{project_id}-req-a")
        store.add_requirement(item["item_id"], f"{project_id} requirement B", event_id=f"{project_id}-req-b")
        store.create_item(title=f"{project_id} independent private item", scope={"kind": "independent"})
        return session

    def confirm(self, choice="y", seen=None):
        def confirm_fn(preview, allow_approve_all):
            if seen is not None:
                seen.append((preview, allow_approve_all))
            return choice
        return confirm_fn


def test_six_project_overview_includes_current_independent_items_and_multiple_requirements(tmp_path):
    f = Fixture(tmp_path)
    seen = []
    rendered = render_selected_projects_overview(f.current, f.project_ids, f.confirm("y", seen))

    assert len(seen) == 1 and seen[0][1] is False
    preview = seen[0][0]
    assert "project-1" in preview and f.session_ids["project-1"] in preview
    assert "结果会进入当前对话" in preview and "后续可能被模型看到" in preview
    assert "汇总来源不会读取所选其他项目会话的 goal.md/messages.jsonl；当前对话仍按原有机制加载并记录本问答。" in preview
    assert "当前会话独立事项" in preview
    assert "该来源仅取独立事项；如当前会话也是所选项目会话，其项目事项另按所选项目范围纳入" in preview
    assert "不汇入这些会话的 independent 事项" in preview
    assert "仅覆盖本次确认的已映射项目会话" in preview
    assert "未映射项目/其他历史未检查" in preview
    assert "goal.md" in preview and "messages.jsonl" in preview
    assert "跨项目原子快照" in preview
    assert "可能创建最小 .work-records.lock" in preview
    assert "current independent item" in rendered
    assert "current independent requirement A" in rendered
    assert "current independent requirement B" in rendered
    assert "独立事项" in rendered
    for project_id in f.project_ids:
        assert f"{project_id} independent private item" not in rendered
    for project_id in f.project_ids[:-1]:
        assert f"{project_id} task" in rendered
        assert f"{project_id} requirement A" in rendered
        assert f"{project_id} requirement B" in rendered
    assert "project-6" in rendered  # source is listed even if its group is capped
    assert "还有 1 组未展开，包含 1 个事项、2 项要求未展开" in rendered
    assert "确认时来源" in rendered and "读取时间" in rendered
    assert "汇总来源不会读取所选其他项目会话的 goal.md/messages.jsonl；当前对话仍按原有机制加载并记录本问答。" in rendered
    assert "项目实时执行状态" in rendered
    assert "未映射项目/其他历史未检查" in rendered


def test_rejection_reads_no_target_sidecar_or_meta(tmp_path, monkeypatch):
    f = Fixture(tmp_path, project_count=1)
    opened = []
    real_open = os.open
    target_meta = f.paths.data / "sessions" / f.session_ids["project-1"] / "meta.json"
    def tracked_open(path, flags, *args, **kwargs):
        if Path(path) == target_meta:
            opened.append(Path(path))
        return real_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, "open", tracked_open)
    sidecar_reads = []
    monkeypatch.setattr(WorkRecordStore, "_read", lambda self: sidecar_reads.append(self) or {})

    rendered = render_selected_projects_overview(f.current, f.project_ids, f.confirm("n"))

    assert "未读取其他会话元数据或工作记录侧车" in rendered
    assert opened == [] and sidecar_reads == []


def test_state_mapping_change_after_confirmation_prevents_target_reads(tmp_path, monkeypatch):
    f = Fixture(tmp_path, project_count=1)
    sidecar_reads = []
    original_read = WorkRecordStore._read
    def counted_read(self):
        sidecar_reads.append(self)
        return original_read(self)
    monkeypatch.setattr(WorkRecordStore, "_read", counted_read)
    def changing_confirm(_preview, allow):
        assert allow is False
        f.write_mapping({"project-1": "20261030-bbbbbbbb"})
        return "y"

    rendered = render_selected_projects_overview(f.current, f.project_ids, changing_confirm)

    assert "无法核对" in rendered
    assert sidecar_reads == []


def test_state_mapping_change_during_reads_discards_entire_result(tmp_path, monkeypatch):
    f = Fixture(tmp_path, project_count=1)
    original_read = WorkRecordStore._read
    calls = 0
    def changing_read(self):
        nonlocal calls
        calls += 1
        result = original_read(self)
        if calls == 1:
            f.write_mapping({"project-1": "20261030-bbbbbbbb"})
        return result
    monkeypatch.setattr(WorkRecordStore, "_read", changing_read)

    rendered = render_selected_projects_overview(f.current, f.project_ids, f.confirm("y"))

    assert "无法核对" in rendered
    assert "project-1 task" not in rendered


@pytest.mark.parametrize("bad_state", [
    None,
    "{broken",
    '{"project_sessions":{"project-1":"20261030-00000001","project-1":"20261030-00000002"}}',
    '{"project_sessions":{"../escape":"20261030-00000001"}}',
    '{"project_sessions":{"project-1":"../escape"}}',
    '{"project_sessions":[]}',
])
def test_missing_corrupt_duplicate_or_illegal_state_mapping_is_unverifiable(tmp_path, bad_state, monkeypatch):
    f = Fixture(tmp_path, project_count=1)
    if bad_state is None:
        f.state_path.unlink()
    else:
        f.state_path.write_text(bad_state, encoding="utf-8")
    sidecar_reads = []
    confirms = []
    original_read = WorkRecordStore._read
    def counted_read(self):
        sidecar_reads.append(self)
        return original_read(self)
    monkeypatch.setattr(WorkRecordStore, "_read", counted_read)
    def confirm_fn(preview, allow):
        confirms.append((preview, allow))
        return "y"

    rendered = render_selected_projects_overview(f.current, f.project_ids, confirm_fn)

    assert "无法核对项目会话映射" in rendered
    assert sidecar_reads == [] and confirms == []


def test_target_session_symlink_escape_is_rejected_before_metadata_or_sidecar_read(tmp_path, monkeypatch):
    f = Fixture(tmp_path, project_count=1)
    sid = f.session_ids["project-1"]
    target = f.paths.data / "sessions" / sid
    outside = tmp_path / "outside-session"
    outside.mkdir()
    try:
        target.rename(target.with_name(sid + "-original"))
        target.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")
    sidecar_reads = []
    monkeypatch.setattr(WorkRecordStore, "_read", lambda self: sidecar_reads.append(self) or {})

    rendered = render_selected_projects_overview(f.current, f.project_ids, f.confirm("y"))

    assert "无法核对" in rendered
    assert sidecar_reads == []


def test_corrupt_metadata_or_sidecar_binding_mismatch_fails_closed(tmp_path):
    f = Fixture(tmp_path, project_count=1)
    target = f.paths.data / "sessions" / f.session_ids["project-1"]
    (target / "meta.json").write_text("{broken", encoding="utf-8")
    result = render_selected_projects_overview(f.current, f.project_ids, f.confirm("y"))
    assert "无法核对" in result

    # Restore valid project metadata but corrupt the sidecar's session binding.
    (target / "meta.json").write_text(json.dumps({"project_id": "project-1"}), encoding="utf-8")
    sidecar = target / "work-records.v1.json"
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload["conversation_id"] = "20261030-bbbbbbbb"
    sidecar.write_text(json.dumps(payload), encoding="utf-8")
    result = render_selected_projects_overview(f.current, f.project_ids, f.confirm("y"))
    assert "无法核对" in result


def test_missing_target_meta_is_not_silently_defaulted(tmp_path):
    f = Fixture(tmp_path, project_count=1)
    target = f.paths.data / "sessions" / f.session_ids["project-1"]
    (target / "meta.json").unlink()

    rendered = render_selected_projects_overview(f.current, f.project_ids, f.confirm("y"))

    assert "无法核对" in rendered


def test_service_does_not_load_goal_messages_and_may_create_lock_file(tmp_path, monkeypatch):
    f = Fixture(tmp_path, project_count=1)
    target = f.paths.data / "sessions" / f.session_ids["project-1"]
    forbidden = {target / "goal.md", target / "messages.jsonl"}
    read_attempts = []
    original_read_text = Path.read_text
    def guarded_read_text(path, *args, **kwargs):
        if path in forbidden:
            read_attempts.append(path)
            raise AssertionError("goal/message content must not be read")
        return original_read_text(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    (target / ".work-records.lock").unlink(missing_ok=True)

    seen = []
    rendered = render_selected_projects_overview(f.current, f.project_ids, f.confirm("y", seen))

    assert "project-1 task" in rendered
    assert read_attempts == []
    assert (target / ".work-records.lock").exists()
    assert "可能创建最小 .work-records.lock" in seen[0][0]


def test_overview_caps_999_items_and_requirements_with_factual_status_labels(tmp_path, monkeypatch):
    f = Fixture(tmp_path, project_count=1)
    synthetic_items = []
    for item_index in range(999):
        synthetic_items.append({
            "item_id": f"item-{item_index:04d}",
            "title": "T" * 100,
            "status": "in_progress",
            "scope": {"kind": "project", "project_id": "project-1"},
            "requirements": [{
                "requirement_id": f"req-{item_index}-{req_index}",
                "text": "R" * 150,
                "status": "in_progress",
                "current_run_id": "run-still-unconfirmed",
            } for req_index in range(8)],
            "runs": [],
            "history": [],
        })
    original_list_items = WorkRecordStore.list_items
    def capped_list_items(store):
        return original_list_items(store) if not store._bound_project_id else synthetic_items
    monkeypatch.setattr(WorkRecordStore, "list_items", capped_list_items)

    rendered = render_selected_projects_overview(f.current, f.project_ids, f.confirm("y"))

    assert "1000 个事项、7994 项要求" in rendered
    assert "项目 project-1（关联 999 个事项、7992 项要求" in rendered
    assert "还有 994 个事项未展开，包含 7952 项要求未展开" in rendered
    assert "还有 2 项要求未展开" in rendered
    assert "文本截断，另有 20 字" in rendered
    assert "文本截断，另有 30 字" in rendered
    assert "执行状态待确认" in rendered
    assert "正在运行" not in rendered and "运行中" not in rendered
    assert rendered.count("事项：") <= 6


def test_state_json_symlink_is_unverifiable_without_confirmation_or_sidecar_read(tmp_path, monkeypatch):
    f = Fixture(tmp_path, project_count=1)
    outside = tmp_path / "outside-state.json"
    outside.write_text(json.dumps({"project_sessions": f.session_ids}), encoding="utf-8")
    f.state_path.unlink()
    try:
        f.state_path.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"file symlink unavailable: {exc}")
    confirms = []
    reads = []
    monkeypatch.setattr(WorkRecordStore, "_read", lambda self: reads.append(self) or {})
    rendered = render_selected_projects_overview(
        f.current, f.project_ids,
        lambda preview, allow: (confirms.append((preview, allow)), "y")[1],
    )
    assert "无法核对项目会话映射" in rendered
    assert confirms == [] and reads == []


def test_target_meta_symlink_is_unverifiable(tmp_path):
    f = Fixture(tmp_path, project_count=1)
    target = f.paths.data / "sessions" / f.session_ids["project-1"]
    meta = target / "meta.json"
    outside = tmp_path / "outside-meta.json"
    outside.write_text(json.dumps({"project_id": "project-1"}), encoding="utf-8")
    meta.unlink()
    try:
        meta.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"file symlink unavailable: {exc}")
    rendered = render_selected_projects_overview(f.current, f.project_ids, f.confirm("y"))
    assert "无法核对" in rendered


def test_native_windows_junction_target_session_is_rejected_without_reading_sidecar(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("native Windows junction validation requires Windows")

    f = Fixture(tmp_path, project_count=1)
    session_id = f.session_ids["project-1"]
    link = f.paths.data / "sessions" / session_id
    target = link.with_name(session_id + "-junction-target")
    tmp_root = tmp_path.resolve(strict=True)
    assert link.resolve(strict=True).is_relative_to(tmp_root)
    link.rename(target)
    assert target.resolve(strict=True).is_relative_to(tmp_root)

    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"native junction creation failed: {result.stderr.strip() or result.stdout.strip()}")

    is_junction = getattr(link, "is_junction", None)
    if not (callable(is_junction) and is_junction()):
        # Remove only the junction entry; never recurse through it.
        os.rmdir(link)
        pytest.fail("mklink succeeded but pathlib does not recognize the directory junction")
    assert link.resolve(strict=True) == target.resolve(strict=True)
    assert link.resolve(strict=True).is_relative_to(tmp_root)

    sidecar_reads = []
    original_list_items = WorkRecordStore.list_items
    def tracked_list_items(store):
        if store._conversation_id == session_id:
            sidecar_reads.append(store._conversation_id)
        return original_list_items(store)
    monkeypatch.setattr(WorkRecordStore, "list_items", tracked_list_items)

    try:
        rendered = render_selected_projects_overview(f.current, f.project_ids, f.confirm("y"))
        assert "无法核对" in rendered
        assert sidecar_reads == []
    finally:
        # os.rmdir removes only the junction itself and does not recurse into target.
        if getattr(link, "is_junction", lambda: False)():
            os.rmdir(link)
        assert target.is_dir()


def test_seven_project_selection_is_rejected_before_confirmation_or_reads(tmp_path, monkeypatch):
    f = Fixture(tmp_path, project_count=7)
    confirms = []
    reads = []
    monkeypatch.setattr(WorkRecordStore, "_read", lambda self: reads.append(self) or {})

    rendered = render_selected_projects_overview(
        f.current, f.project_ids, lambda preview, allow: (confirms.append((preview, allow)), "y")[1],
    )

    assert "无法核对" in rendered
    assert confirms == [] and reads == []


def test_current_session_binding_change_after_confirmation_prevents_all_sidecar_reads(tmp_path, monkeypatch):
    f = Fixture(tmp_path, project_count=1)
    reads = []
    monkeypatch.setattr(WorkRecordStore, "_read", lambda self: reads.append(self) or {})
    def change_current_binding(_preview, allow):
        assert allow is False
        f.current.meta.project_id = "project-1"
        return "y"

    rendered = render_selected_projects_overview(f.current, f.project_ids, change_current_binding)

    assert "无法核对" in rendered
    assert reads == []


def test_empty_current_or_project_scope_is_reported_without_claiming_no_work(tmp_path, monkeypatch):
    f = Fixture(tmp_path, project_count=1)
    monkeypatch.setattr(WorkRecordStore, "list_items", lambda _store: [])

    rendered = render_selected_projects_overview(f.current, f.project_ids, f.confirm("y"))

    assert "当前会话 independent 项目外事项侧车未发现已记录事项" in rendered
    assert "项目 project-1 会话侧车未发现该项目范围已记录事项" in rendered
    assert "其他历史或侧车外来源未检查" in rendered


def test_current_session_also_selected_project_includes_each_scope_once(tmp_path):
    f = Fixture(tmp_path, project_count=1)
    f.current.meta.project_id = "project-1"
    (f.current.session_dir / "meta.json").write_text(
        json.dumps({"project_id": "project-1"}), encoding="utf-8",
    )
    current_store = WorkRecordStore(f.current)
    project_item = current_store.create_item(
        title="same-session project task",
        scope={"kind": "project", "project_id": "project-1"},
    )
    current_store.add_requirement(
        project_item["item_id"], "same-session project requirement", event_id="same-session-project-requirement",
    )
    f.write_mapping({"project-1": f.current_id})
    seen = []

    rendered = render_selected_projects_overview(
        f.current, ["project-1"], f.confirm("y", seen),
    )

    preview = seen[0][0]
    assert "该来源仅取独立事项；如当前会话也是所选项目会话，其项目事项另按所选项目范围纳入" in preview
    assert rendered.count("current independent item") == 1
    assert rendered.count("same-session project task") == 1
    assert rendered.count("same-session project requirement") == 1
    assert "project-1 task" not in rendered


def test_selected_project_overview_hides_after_result_ack_monotonic_wait(tmp_path, monkeypatch):
    from types import SimpleNamespace
    f = Fixture(tmp_path, project_count=1)
    pid = f.project_ids[0]
    sid = f.session_ids[pid]
    directory = f.paths.data / "sessions" / sid
    session = Session(sid, directory, "", SessionMeta(project_id=pid), [], f.paths)
    store = WorkRecordStore(session)
    item = store.list_items()[0]
    for index, req in enumerate(item["requirements"]):
        run_id = f"finished-{index}"
        store.begin_requirement_run(item["item_id"], req["requirement_id"], run_id=run_id, expected_current_run_id=None)
        store.record_result_if_current_run(item["item_id"], req["requirement_id"], run_id=run_id,
            result_id=f"done-{index}", status="completed", summary=f"summary-{index}", verification={"status": "not_verified"})
    now = [SimpleNamespace(boot_id="boot-test", monotonic_ns=5_000_000_000, wall_utc="2026-10-30T00:00:00Z")]
    monkeypatch.setattr(work_record_visibility_clock, "sample_elapsed_clock", lambda: now[0])
    ledger = WorkCardLedger(session, store)
    saved = ledger.save_snapshot(str(uuid.uuid4()), build_project_work_record_cards(store, pid))
    assert saved.status == "saved"
    presentation = WorkCardPresentations(session, store, ledger)
    presentation_id = str(uuid.uuid4())
    assert presentation.create(presentation_id, saved.card_id).status == "created"
    for index in range(2):
        assert presentation.acknowledge_result(presentation_id, saved.card_id, f"done-{index}",
            action="result_detail", trusted_action=True).status == "acknowledged"

    render = lambda: render_selected_projects_overview(f.current, [pid], f.confirm("y"))
    assert f"{pid} task" in render()
    now[0] = SimpleNamespace(boot_id="boot-test", monotonic_ns=5_000_000_000 + 30 * 60 * 1_000_000_000,
                              wall_utc="2026-10-30T00:30:00Z")
    overview = render()
    assert f"{pid} task" not in overview
    assert "current independent item" in overview
    assert f"项目 {pid} 的已记录事项已读并收起；工作记录和历史卡片仍保留" in overview
    assert ledger.get_card(saved.card_id) is not None


def test_selected_project_empty_projection_distinguishes_real_empty_from_collapsed(tmp_path, monkeypatch):
    f = Fixture(tmp_path, project_count=1)
    monkeypatch.setattr(WorkRecordStore, "list_items", lambda self: [])
    overview = render_selected_projects_overview(f.current, f.project_ids, f.confirm("y"))
    assert "未发现已记录事项" in overview
    assert "已读并收起" not in overview
