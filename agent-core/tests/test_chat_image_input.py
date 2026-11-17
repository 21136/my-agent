"""Natural-language local image path input tests (synthetic fixtures only)."""
from __future__ import annotations

import base64
from pathlib import Path

import pytest

from context import build_llm_messages
from file_stage import MAX_IMAGE_INPUT_BYTES, compose_user_message
from host_scope import HostRoot, HostScopeConfig
from session import create_new
from tests.isolation_helpers import temporary_agent_paths
from chat_image_input import resolve_chat_image_paths


@pytest.fixture
def agent_env():
    with temporary_agent_paths() as paths:
        yield paths, create_new(paths, conversation_id="_chat_image_path")


def test_explicit_local_image_path_becomes_real_vision_payload(tmp_path, agent_env):
    paths, session = agent_env
    image = tmp_path / "my image.png"
    payload = b"synthetic-png-bytes-only"
    image.write_bytes(payload)
    request = f'请看一下图片 "{image}"，告诉我里面有什么。'

    result = resolve_chat_image_paths(
        request, paths=paths, session=session, shell="grow", config=HostScopeConfig()
    )
    assert result.attachments == 1
    assert result.failed == 0
    message = compose_user_message(text=request, attachments=result.items)
    session.messages.append({"role": "user", "content": message})
    model_messages = build_llm_messages(session, include_images=True)
    blocks = model_messages[0]["content"]
    image_block = next(block for block in blocks if block.get("type") == "image_url")
    data_uri = image_block["image_url"]["url"]
    encoded = data_uri.split(",", 1)[1]
    assert base64.b64decode(encoded) == payload


def test_explicit_image_path_respects_8mb_limit_without_staging(tmp_path, agent_env):
    paths, session = agent_env
    image = tmp_path / "too-large.png"
    image.write_bytes(b"x" * (MAX_IMAGE_INPUT_BYTES + 1))

    result = resolve_chat_image_paths(
        f"请看这张图：{image}", paths=paths, session=session, shell="grow", config=HostScopeConfig()
    )
    assert result.items == []
    assert result.oversized == 1
    assert not list(paths.workspace.rglob("too-large.png"))


def test_image_path_is_not_opened_without_explicit_visual_request(tmp_path, agent_env):
    paths, session = agent_env
    image = tmp_path / "reference.png"
    image.write_bytes(b"synthetic")

    result = resolve_chat_image_paths(
        f"This note contains a file path, no action requested: {image}.",
        paths=paths, session=session, shell="grow", config=HostScopeConfig()
    )
    assert result.items == []
    assert result.failed == result.oversized == 0
    assert not list(paths.workspace.rglob("reference.png"))


def test_sensitive_image_path_is_rejected_without_reading_bytes(tmp_path, agent_env):
    paths, session = agent_env
    sensitive = tmp_path / ".ssh" / "id_rsa.png"
    sensitive.parent.mkdir()
    sensitive.write_bytes(b"synthetic")

    result = resolve_chat_image_paths(
        f"请查看 {sensitive}", paths=paths, session=session, shell="grow", config=HostScopeConfig()
    )
    assert result.items == []
    assert result.failed == 1


def test_host_scoped_image_path_remains_a_host_reference(tmp_path, agent_env):
    paths, session = agent_env
    host_root = tmp_path / "approved host"
    host_root.mkdir()
    image = host_root / "diagram.png"
    image.write_bytes(b"synthetic-host-image")
    config = HostScopeConfig(host_roots=[HostRoot(
        id="approved", path=host_root, label="approved", added_at="2026-01-01T00:00:00Z",
        read=True, write=False,
    )])

    result = resolve_chat_image_paths(
        f"请描述一下 \"{image}\"", paths=paths, session=session, shell="grow", config=config
    )
    assert result.attachments == 1
    assert result.items[0].ref == "host:approved/diagram.png"
    assert not list(paths.workspace.rglob("diagram.png"))


def test_directory_with_multiple_images_returns_choices_without_staging_any(tmp_path, agent_env):
    paths, session = agent_env
    directory = tmp_path / "图片目录"
    directory.mkdir()
    (directory / "one.png").write_bytes(b"synthetic-1")
    (directory / "two.jpg").write_bytes(b"synthetic-2")

    result = resolve_chat_image_paths(
        f'请看看目录 "{directory}" 里的图片',
        paths=paths, session=session, shell="grow", config=HostScopeConfig()
    )
    assert result.items == []
    assert result.choices == ["one.png", "two.jpg"]
    assert not list(paths.workspace.rglob("one.png"))
    assert not list(paths.workspace.rglob("two.jpg"))


def test_directory_with_one_image_stages_only_that_image(tmp_path, agent_env):
    paths, session = agent_env
    directory = tmp_path / "one-image-folder"
    directory.mkdir()
    only_image = directory / "only.webp"
    only_image.write_bytes(b"synthetic-single-image")
    (directory / "notes.txt").write_text("not an image", encoding="utf-8")

    result = resolve_chat_image_paths(
        f'请看这个目录 "{directory}" 里的图片',
        paths=paths, session=session, shell="grow", config=HostScopeConfig()
    )
    assert result.attachments == 1
    assert result.items[0].name == "only.webp"


def test_host_scoped_image_payload_is_resolved_under_approved_root(tmp_path, agent_env):
    paths, session = agent_env
    host_root = tmp_path / "approved-host"
    host_root.mkdir()
    image = host_root / "diagram.png"
    payload = b"synthetic-host-image-bytes"
    image.write_bytes(payload)
    config = HostScopeConfig(host_roots=[HostRoot(
        id="approved", path=host_root, label="approved", added_at="2026-01-01T00:00:00Z",
        read=True, write=False,
    )])
    from host_scope import save_host_scope
    save_host_scope(paths, config)
    result = resolve_chat_image_paths(
        f"请看 \"{image}\"", paths=paths, session=session, shell="grow", config=config
    )
    message = compose_user_message(text=f"Please look at {image}", attachments=result.items)
    session.messages.append({"role": "user", "content": message})
    blocks = build_llm_messages(session, include_images=True)[0]["content"]
    image_block = next(block for block in blocks if block.get("type") == "image_url")
    assert base64.b64decode(image_block["image_url"]["url"].split(",", 1)[1]) == payload






class _CapturingVisionClient:
    def __init__(self):
        self.messages = None
        self.models = []
        self.calls = []
        self.cancel_event = None
        self.after_chat = None

    def set_cancel_event(self, event):
        self.cancel_event = event

    def chat(self, messages, *, model=None, stream=None, **_kwargs):
        from llm_client import LLMResponse
        self.messages = messages
        self.models.append(model)
        self.calls.append(messages)
        if self.after_chat is not None:
            self.after_chat(messages, model, len(self.calls))
        text = "I inspected the supplied image input."
        if stream is not None and stream.on_content_delta:
            stream.on_content_delta(text)
        return LLMResponse("fixture", text, [], "stop", None, {})


def test_user_message_local_path_reaches_vision_client_without_external_call(tmp_path, agent_env, monkeypatch):
    import asyncio
    import json
    import server
    import websockets
    from websockets.asyncio.server import serve

    paths, _session = agent_env
    image = tmp_path / "synthetic local image.png"
    payload = b"synthetic-local-image-payload"
    image.write_bytes(payload)
    client = _CapturingVisionClient()
    original_build_repl = server._build_repl

    def build_repl(*args, **kwargs):
        args[0].set_llm_model("0x567-flash")
        repl = original_build_repl(*args, **kwargs)
        repl.session.set_llm_model("0x567-flash")
        client.set_cancel_event(repl.agent.cancel_event)
        repl.agent.llm = client
        return repl

    monkeypatch.setattr(server, "_build_repl", build_repl)
    handler = server.WsSessionHandler(paths)

    async def exercise():
        listener = await serve(handler.handle, "127.0.0.1", 0, max_size=65536)
        port = listener.sockets[0].getsockname()[1]
        ws = await websockets.connect(f"ws://127.0.0.1:{port}", open_timeout=5, close_timeout=5)
        try:
            while True:
                event = json.loads(await asyncio.wait_for(ws.recv(), 5))
                if event.get("type") == "session.banner":
                    break
            await ws.send(json.dumps({
                "type": "user.message",
                "text": f'Please look at the image "{image}" and describe it.',
            }))
            while True:
                event = json.loads(await asyncio.wait_for(ws.recv(), 10))
                if event.get("type") == "turn.end":
                    break
        finally:
            await ws.close()
            listener.close()
            await listener.wait_closed()

    asyncio.run(exercise())
    assert client.messages is not None
    user_message = [message for message in client.messages if message.get("role") == "user"][-1]
    assert isinstance(user_message["content"], list)
    image_block = next(block for block in user_message["content"] if block.get("type") == "image_url")
    assert base64.b64decode(image_block["image_url"]["url"].split(",", 1)[1]) == payload


@pytest.mark.parametrize("utterance", [
    "Do not look at this path: ",
    "The quoted instruction says don't inspect this: ",
    "Hypothetically, view this path: ",
    "\u6211\u8bf4\u8fc7\u4e0d\u8981\u770b ",
    "\u4e0d\u8981\u770b ",
])
def test_negated_or_quoted_path_is_never_staged(tmp_path, agent_env, utterance):
    paths, session = agent_env
    image = tmp_path / "do-not-send.png"
    image.write_bytes(b"synthetic")
    result = resolve_chat_image_paths(
        utterance + str(image), paths=paths, session=session, shell="grow", config=HostScopeConfig()
    )
    assert result.items == []
    assert not list(paths.workspace.rglob("do-not-send.png"))


def test_more_than_twenty_explicit_paths_are_bounded(tmp_path, agent_env):
    paths, session = agent_env
    images = []
    for index in range(21):
        image = tmp_path / f"image-{index}.png"
        image.write_bytes(b"synthetic")
        images.append(image)
    request = "Please inspect these images: " + " ".join(str(image) for image in images)
    result = resolve_chat_image_paths(
        request, paths=paths, session=session, shell="grow", config=HostScopeConfig()
    )
    assert result.attachments == 20
    assert result.paths_truncated


def test_duplicate_user_path_and_existing_image_attachment_are_deduplicated(tmp_path, agent_env):
    from file_stage import StagedAttachment, stage_absolute_path

    paths, session = agent_env
    image = paths.workspace / "already-attached.png"
    image.write_bytes(b"synthetic")
    existing = stage_absolute_path(
        str(image), paths=paths, session=session, shell="grow", config=HostScopeConfig()
    )
    assert existing.ref.startswith("workspace/")
    result = resolve_chat_image_paths(
        f"Please look at {image}", paths=paths, session=session,
        shell="grow", config=HostScopeConfig(), existing_items=[existing],
    )
    assert result.items == []
    assert result.duplicates == 1


def test_user_path_plus_other_attachments_obeys_twenty_item_total(tmp_path, agent_env):
    from file_stage import StagedAttachment

    paths, session = agent_env
    existing = []
    for index in range(19):
        ref = f"workspace/existing-{index}.txt"
        paths.resolve_under_agent(ref, must_exist=False).write_text("x", encoding="utf-8")
        existing.append(StagedAttachment(
            id=f"text-{index}", name=f"existing-{index}.txt", ref=ref,
            size=1, mime="text/plain", readable_text=True, copied=False,
        ))
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"synthetic-1")
    second.write_bytes(b"synthetic-2")
    result = resolve_chat_image_paths(
        f"Please inspect {first} and {second}", paths=paths, session=session,
        shell="grow", config=HostScopeConfig(), existing_items=existing,
    )
    assert len(existing) + result.attachments == 20
    assert [item.name for item in result.items] == ["first.png"]
    assert result.paths_truncated


def test_non_vision_server_path_warns_and_keeps_other_attachment_without_switching_models(tmp_path, agent_env, monkeypatch):
    import asyncio
    import json
    import server
    import websockets
    from websockets.asyncio.server import serve

    paths, _session = agent_env
    image = tmp_path / "local-image.png"
    image.write_bytes(b"synthetic-non-vision-image")
    note = tmp_path / "attached-note.txt"
    note.write_text("synthetic attachment text", encoding="utf-8")
    client = _CapturingVisionClient()
    original_build_repl = server._build_repl

    def build_repl(*args, **kwargs):
        args[0].set_llm_model("sophnet-deepseek-v4-flash")
        repl = original_build_repl(*args, **kwargs)
        repl.session.set_llm_model("sophnet-deepseek-v4-flash")
        client.set_cancel_event(repl.agent.cancel_event)
        repl.agent.llm = client
        return repl

    monkeypatch.setattr(server, "_build_repl", build_repl)
    handler = server.WsSessionHandler(paths)

    async def exercise():
        listener = await serve(handler.handle, "127.0.0.1", 0, max_size=65536)
        port = listener.sockets[0].getsockname()[1]
        ws = await websockets.connect(f"ws://127.0.0.1:{port}", open_timeout=5, close_timeout=5)
        events = []
        try:
            while True:
                event = json.loads(await asyncio.wait_for(ws.recv(), 5))
                if event.get("type") == "session.banner":
                    break
            await ws.send(json.dumps({"type": "file.stage", "paths": [str(note)]}))
            while True:
                event = json.loads(await asyncio.wait_for(ws.recv(), 5))
                if event.get("type") == "file.staged":
                    attachment_id = event["items"][0]["id"]
                    break
            await ws.send(json.dumps({
                "type": "user.message",
                "text": f'Please look at "{image}" and use the attached note as context.',
                "attachments": [attachment_id],
            }))
            while True:
                event = json.loads(await asyncio.wait_for(ws.recv(), 10))
                events.append(event)
                if event.get("type") == "turn.end":
                    break
        finally:
            await ws.close()
            listener.close()
            await listener.wait_closed()
        return events

    events = asyncio.run(exercise())
    warnings = [event for event in events if event.get("type") == "turn.notice" and event.get("level") == "warn"]
    assert any("当前模型不支持识图" in event.get("text", "") for event in warnings)
    assert client.models == ["sophnet-deepseek-v4-flash"]
    assert client.messages is not None
    user_message = [message for message in client.messages if message.get("role") == "user"][-1]
    assert isinstance(user_message["content"], str)
    assert "attached-note.txt" in user_message["content"]
    assert "图片附件" in user_message["content"]
    assert "当前模型不支持直接读取图片内容" in "\n".join(
        str(message.get("content", "")) for message in client.messages if message.get("role") == "system"
    )
    assert not any("image_url" in str(message.get("content")) for message in client.messages)

@pytest.mark.parametrize("mode", ["denied_file", "denied_dir", "no_read_file", "no_read_dir"])
def test_host_root_restrictions_never_fall_back_to_external_staging(tmp_path, agent_env, mode):
    paths, session = agent_env
    root = tmp_path / "registered"
    secret = root / "private"
    secret.mkdir(parents=True)
    image = secret / "synthetic.png"
    image.write_bytes(b"synthetic")
    cfg = HostScopeConfig(host_roots=[HostRoot(
        id="registered", path=root, label="registered", added_at="2026-01-01T00:00:00Z",
        read=not mode.startswith("no_read"), write=False,
    )], deny_globs=["private/**", "private"] if mode.startswith("denied") else [])
    target = secret if mode.endswith("dir") else image
    result = resolve_chat_image_paths(
        f'Please look at "{target}"', paths=paths, session=session, config=cfg,
    )
    assert result.items == []
    assert result.choices == []
    assert result.failed == 1
    assert not list(paths.workspace.rglob("synthetic.png"))


def test_link_to_denied_host_image_never_stages(tmp_path, agent_env):
    paths, session = agent_env
    root = tmp_path / "registered"
    secret = root / "private"
    secret.mkdir(parents=True)
    image = secret / "synthetic.png"
    image.write_bytes(b"synthetic")
    link = tmp_path / "alias.png"
    try:
        link.symlink_to(image)
    except OSError:
        pytest.skip("symlink creation unavailable")
    cfg = HostScopeConfig(host_roots=[HostRoot(
        id="registered", path=root, label="registered", added_at="2026-01-01T00:00:00Z",
    )], deny_globs=["private/**", "private"])
    result = resolve_chat_image_paths(f"Please look at {link}", paths=paths, session=session, config=cfg)
    assert result.items == []
    assert result.failed == 1
    assert not list(paths.workspace.rglob("synthetic.png"))


@pytest.mark.parametrize("multiple", [False, True])
def test_path_before_directory_word_with_spaces(tmp_path, agent_env, multiple):
    paths, session = agent_env
    directory = tmp_path / "folder with spaces"
    directory.mkdir()
    (directory / "a.png").write_bytes(b"synthetic-a")
    if multiple:
        (directory / "b.png").write_bytes(b"synthetic-b")
    text = f"\u8bf7\u770b\u770b {directory} \u76ee\u5f55\u4e2d\u7684\u56fe\u7247"
    result = resolve_chat_image_paths(text, paths=paths, session=session, config=HostScopeConfig())
    if multiple:
        assert result.items == []
        assert result.choices == ["a.png", "b.png"]
    else:
        assert len(result.items) == 1
        assert result.items[0].name == "a.png"


@pytest.mark.parametrize("negative", ["\u4e0d\u8981\u8bfb\u53d6", "\u4e0d\u8981\u5206\u6790"])
def test_more_negative_requests_never_stage(tmp_path, agent_env, negative):
    paths, session = agent_env
    image = tmp_path / "synthetic.png"
    image.write_bytes(b"synthetic")
    result = resolve_chat_image_paths(f"{negative} {image}", paths=paths, session=session, config=HostScopeConfig())
    assert not result.items
    assert not list(paths.workspace.rglob("synthetic.png"))


def test_unparseable_explicit_directory_request_is_reported(tmp_path, agent_env):
    paths, session = agent_env
    text = f"\u8bf7\u770b\u770b {tmp_path} \u8def\u5f84\u4e0b\u7684\u56fe\u7247"
    result = resolve_chat_image_paths(text, paths=paths, session=session, config=HostScopeConfig())
    assert result.failed == 1
    assert not result.items

@pytest.mark.parametrize("direction", ["into_denied_host", "out_of_denied_host"])
@pytest.mark.parametrize("target_kind", ["file", "directory"])
def test_junction_cannot_bypass_host_restrictions(tmp_path, agent_env, direction, target_kind):
    import _winapi

    paths, session = agent_env
    root = tmp_path / "registered"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    restricted = root / "private"
    if direction == "into_denied_host":
        restricted.mkdir()
        destination = restricted
        alias = outside / "alias"
    else:
        destination = outside
        alias = restricted
    (destination / "synthetic.png").write_bytes(b"synthetic")
    _winapi.CreateJunction(str(destination), str(alias))
    try:
        cfg = HostScopeConfig(host_roots=[HostRoot(
            id="registered", path=root, label="registered", added_at="2026-01-01T00:00:00Z",
        )], deny_globs=["private/**", "private"])
        target = alias / "synthetic.png" if target_kind == "file" else alias
        result = resolve_chat_image_paths(
            f'Please look at "{target}"', paths=paths, session=session, config=cfg,
        )
        assert result.items == []
        assert result.choices == []
        assert result.failed == 1
        assert not list(paths.workspace.rglob("synthetic.png"))
    finally:
        alias.rmdir()


def test_multi_image_directory_never_exposes_names_denied_by_host_glob(tmp_path, agent_env):
    paths, session = agent_env
    root = tmp_path / "registered"
    private_dir = root / "private"
    private_dir.mkdir(parents=True)
    (private_dir / "visible.png").write_bytes(b"synthetic-visible")
    (private_dir / "secret-name.png").write_bytes(b"synthetic-secret")
    config = HostScopeConfig(host_roots=[HostRoot(
        id="registered", path=root, label="registered", added_at="2026-01-01T00:00:00Z",
        read=True, write=False,
    )], deny_globs=["private/**"])

    result = resolve_chat_image_paths(
        f'Please look at directory "{private_dir}" images',
        paths=paths, session=session, config=config,
    )

    assert result.choices == []
    assert "visible.png" not in result.choices
    assert "secret-name.png" not in result.choices
    assert result.restricted == 2
    assert result.failed == 0
    assert result.items == []


def test_explicit_all_directory_request_resolves_more_than_one_batch(tmp_path, agent_env):
    paths, session = agent_env
    directory = tmp_path / "all-images"
    directory.mkdir()
    for index in range(41):
        (directory / f"image-{index:03}.png").write_bytes(f"synthetic-{index}".encode())

    result = resolve_chat_image_paths(
        f'Please read all images in directory "{directory}"',
        paths=paths, session=session, config=HostScopeConfig(),
    )

    assert sum(map(len, result.vision_batches)) == 41
    assert [path.name for batch in result.vision_batches for path in batch] == [f"image-{i:03}.png" for i in range(41)]
    assert result.paths_truncated is False


def test_explicit_all_directory_runs_complete_transient_vision_batches(tmp_path, agent_env, monkeypatch):
    import asyncio
    import json
    import server
    import websockets
    from websockets.asyncio.server import serve

    paths, _session = agent_env
    directory = tmp_path / "many synthetic images"
    directory.mkdir()
    historical_image = tmp_path / "historical.png"
    historical_image.write_bytes(b"historical-image-must-not-repeat")
    manual_image = tmp_path / "manual.png"
    manual_image.write_bytes(b"current-manual-image-must-remain")
    for index in range(101):
        (directory / f"image-{index:03}.png").write_bytes(f"synthetic-{index}".encode())
    client = _CapturingVisionClient()
    built_repls = []
    original_build_repl = server._build_repl

    def build_repl(*args, **kwargs):
        args[0].set_llm_model("0x567-flash")
        repl = original_build_repl(*args, **kwargs)
        repl.session.set_llm_model("0x567-flash")
        from file_stage import compose_user_message, stage_absolute_path
        historical_attachment = stage_absolute_path(
            str(historical_image), paths=paths, session=repl.session, config=HostScopeConfig(),
        )
        repl.session.append_message({
            "role": "user",
            "content": compose_user_message(text="historical image", attachments=[historical_attachment]),
        })
        repl.session.append_message({"role": "assistant", "content": "Old image summary."})
        client.set_cancel_event(repl.agent.cancel_event)
        repl.agent.llm = client
        built_repls.append(repl)
        return repl

    monkeypatch.setattr(server, "_build_repl", build_repl)
    handler = server.WsSessionHandler(paths)

    async def exercise():
        listener = await serve(handler.handle, "127.0.0.1", 0, max_size=65536)
        port = listener.sockets[0].getsockname()[1]
        ws = await websockets.connect(f"ws://127.0.0.1:{port}", open_timeout=5, close_timeout=5)
        events = []
        try:
            while True:
                event = json.loads(await asyncio.wait_for(ws.recv(), 5))
                if event.get("type") == "session.banner":
                    break
            await ws.send(json.dumps({"type": "file.stage", "paths": [str(manual_image)]}))
            while True:
                event = json.loads(await asyncio.wait_for(ws.recv(), 5))
                if event.get("type") == "file.staged":
                    manual_attachment_id = event["items"][0]["id"]
                    break
            await ws.send(json.dumps({
                "type": "user.message",
                "text": f'Please read all images in directory "{directory}"',
                "attachments": [manual_attachment_id],
            }))
            while True:
                event = json.loads(await asyncio.wait_for(ws.recv(), 10))
                events.append(event)
                if event.get("type") == "turn.end":
                    break
        finally:
            await ws.close()
            listener.close()
            await listener.wait_closed()
        return events

    events = asyncio.run(exercise())
    image_calls = [
        call for call in client.calls
        if any(isinstance(message.get("content"), list) for message in call)
    ]
    assert len(image_calls) == 7
    image_counts = [sum(
        block.get("type") == "image_url"
        for message in call for block in (message.get("content") if isinstance(message.get("content"), list) else [])
    ) for call in image_calls]
    assert image_counts == [20, 20, 20, 20, 20, 1, 1]
    assert client.models == ["0x567-flash"] * 7
    summary_status = "\n".join(
        str(message.get("content", "")) for message in image_calls[-1]
        if message.get("role") == "system"
    )
    assert "101" in summary_status and "0" in summary_status
    assert "\u5df2\u6210\u529f\u63d0\u4ea4" in summary_status
    assert len([event for event in events if event.get("type") == "turn.notice" and "\u6279\u6b21" in event.get("text", "")]) == 6
    persisted_user = [message for message in built_repls[0].session.messages if message.get("role") == "user"][-1]
    assert isinstance(persisted_user["content"], str)
    assert "image-000.png" not in persisted_user["content"]
    assert "manual.png" in persisted_user["content"]
    assert "untrusted" in image_calls[0][0]["content"].lower()
    current_blocks = [
        block for message in image_calls[-1] if isinstance(message.get("content"), list)
        for block in message["content"] if block.get("type") == "image_url"
    ]
    assert len(current_blocks) == 1
    current_data = current_blocks[0]["image_url"]["url"]
    assert base64.b64decode(current_data.split(",", 1)[1]) == b"current-manual-image-must-remain"
    assert b"historical-image-must-not-repeat" not in base64.b64decode(current_data.split(",", 1)[1])


def test_explicit_all_directory_mixed_permissions_reads_only_allowed_children(tmp_path, agent_env):
    paths, session = agent_env
    root = tmp_path / "registered"
    directory = root / "private"
    directory.mkdir(parents=True)
    (directory / "visible.png").write_bytes(b"synthetic-visible")
    (directory / "secret-name.png").write_bytes(b"synthetic-secret")
    config = HostScopeConfig(host_roots=[HostRoot(
        id="registered", path=root, label="registered", added_at="2026-01-01T00:00:00Z",
        read=True, write=False,
    )], deny_globs=["private/secret*"])

    result = resolve_chat_image_paths(
        f'Please read all images in directory "{directory}"',
        paths=paths, session=session, config=config,
    )

    selected = [image for batch in result.vision_batches for image in batch]
    assert [image.name for image in selected] == ["visible.png"]
    assert result.restricted == 1
    assert "secret-name.png" not in result.choices


def test_chinese_explicit_all_phrase_uses_bounded_byte_batches(tmp_path, agent_env, monkeypatch):
    import chat_image_input

    paths, session = agent_env
    directory = tmp_path / "byte-batched-images"
    directory.mkdir()
    (directory / "a.png").write_bytes(b"1234567")
    (directory / "b.png").write_bytes(b"1234")
    monkeypatch.setattr(chat_image_input, "MAX_VISION_BATCH_BYTES", 10)

    result = resolve_chat_image_paths(
        f'\u628a\u8fd9\u4e2a\u76ee\u5f55\u91cc\u7684\u56fe\u7247\u5168\u90e8\u8bfb\u53d6/\u770b\u5b8c "{directory}"',
        paths=paths, session=session, config=HostScopeConfig(),
    )

    assert result.all_requested is True
    assert [[path.name for path in batch] for batch in result.vision_batches] == [["a.png"], ["b.png"]]
    assert sum(path.stat().st_size for batch in result.vision_batches for path in batch) == 11


@pytest.mark.parametrize("request_prefix", [
    "\u628a\u6587\u4ef6\u5939 {path} \u91cc\u7684\u56fe\u7247\u5168\u90e8\u8bfb\u5b8c",
    "\u8bfb\u53d6\u76ee\u5f55 {path} \u4e0b\u9762\u6240\u6709\u7167\u7247",
])
def test_chinese_natural_directory_requests_preserve_paths_with_spaces(tmp_path, agent_env, request_prefix):
    paths, session = agent_env
    directory = tmp_path / "photos with spaces"
    directory.mkdir()
    (directory / "a.png").write_bytes(b"a")
    (directory / "b.png").write_bytes(b"b")

    result = resolve_chat_image_paths(
        request_prefix.format(path=directory), paths=paths, session=session, config=HostScopeConfig(),
    )

    assert result.all_requested is True
    assert sum(map(len, result.vision_batches)) == 2


def test_directory_symlink_cannot_escape_selected_root_even_on_batch_read(tmp_path, agent_env):
    from chat_image_input import build_authorized_vision_batch

    paths, session = agent_env
    directory = tmp_path / "selected"
    directory.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside-secret")
    link = directory / "linked.png"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("file symlink creation unavailable")
    config = HostScopeConfig()

    result = resolve_chat_image_paths(
        f'Please read all images in directory "{directory}"',
        paths=paths, session=session, config=config,
    )
    assert result.vision_batches == []
    assert result.restricted == 1
    with pytest.raises(PermissionError):
        build_authorized_vision_batch(
            [link], paths=paths, config=config, directory_root=directory, user_request="describe",
        )


def test_per_child_stat_failure_is_counted_without_aborting_remaining_images(tmp_path, agent_env, monkeypatch):
    from pathlib import Path

    paths, session = agent_env
    directory = tmp_path / "stat-failure-images"
    directory.mkdir()
    failing = directory / "vanished.png"
    succeeding = directory / "available.png"
    failing.write_bytes(b"first")
    succeeding.write_bytes(b"second")
    original_stat = Path.stat

    def flaky_stat(self, *args, **kwargs):
        if self == failing:
            raise PermissionError("synthetic stat failure")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", flaky_stat)
    result = resolve_chat_image_paths(
        f'Please read all images in directory "{directory}"',
        paths=paths, session=session, config=HostScopeConfig(),
    )

    assert result.image_resolution_failures == 1
    assert result.failed == 1
    assert [[image.name for image in batch] for batch in result.vision_batches] == [["available.png"]]


def test_host_scope_revocation_stops_next_batch_and_emits_failure_notice(tmp_path, agent_env, monkeypatch):
    import asyncio
    import json
    import server
    import websockets
    from host_scope import save_host_scope
    from websockets.asyncio.server import serve

    paths, _session = agent_env
    directory = tmp_path / "revocable-images"
    directory.mkdir()
    for index in range(21):
        (directory / f"image-{index:02}.png").write_bytes(f"image-{index}".encode())
    allowed = HostScopeConfig(host_roots=[HostRoot(
        id="revocable", path=directory, label="revocable", added_at="2026-01-01T00:00:00Z",
        read=True, write=False,
    )])
    save_host_scope(paths, allowed)
    client = _CapturingVisionClient()
    original_build_repl = server._build_repl

    def build_repl(*args, **kwargs):
        args[0].set_llm_model("0x567-flash")
        repl = original_build_repl(*args, **kwargs)
        repl.session.set_llm_model("0x567-flash")
        client.set_cancel_event(repl.agent.cancel_event)
        repl.agent.llm = client
        return repl

    def revoke_after_first_vision_call(messages, _model, _call_number):
        has_image = any(
            isinstance(message.get("content"), list)
            and any(block.get("type") == "image_url" for block in message["content"])
            for message in messages
        )
        if has_image:
            save_host_scope(paths, HostScopeConfig(host_roots=[HostRoot(
                id="revocable", path=directory, label="revocable", added_at="2026-01-01T00:00:00Z",
                read=False, write=False,
            )]))
            client.after_chat = None

    client.after_chat = revoke_after_first_vision_call
    monkeypatch.setattr(server, "_build_repl", build_repl)
    handler = server.WsSessionHandler(paths)

    async def exercise():
        listener = await serve(handler.handle, "127.0.0.1", 0, max_size=65536)
        port = listener.sockets[0].getsockname()[1]
        ws = await websockets.connect(f"ws://127.0.0.1:{port}", open_timeout=5, close_timeout=5)
        events = []
        try:
            while True:
                event = json.loads(await asyncio.wait_for(ws.recv(), 5))
                if event.get("type") == "session.banner":
                    break
            await ws.send(json.dumps({
                "type": "user.message",
                "text": f'Please read all images in directory "{directory}"',
            }))
            while True:
                event = json.loads(await asyncio.wait_for(ws.recv(), 10))
                events.append(event)
                if event.get("type") == "turn.end":
                    break
        finally:
            await ws.close()
            listener.close()
            await listener.wait_closed()
        return events

    events = asyncio.run(exercise())
    image_call_count = sum(
        any(isinstance(message.get("content"), list)
            and any(block.get("type") == "image_url" for block in message["content"])
            for message in call)
        for call in client.calls
    )
    assert image_call_count == 1
    warnings = [event.get("text", "") for event in events
                if event.get("type") == "turn.notice" and event.get("level") == "warn"]
    assert any(f"20 \u5f20" in text and f"1 \u5f20" in text and f"0 \u5f20" in text for text in warnings)


def test_cancelled_directory_read_emits_counts_without_waiting_for_model_summary(tmp_path, agent_env, monkeypatch):
    import asyncio
    import json
    import server
    import websockets
    from websockets.asyncio.server import serve

    paths, _session = agent_env
    directory = tmp_path / "cancelled-images"
    directory.mkdir()
    for index in range(21):
        (directory / f"image-{index:02}.png").write_bytes(f"image-{index}".encode())
    client = _CapturingVisionClient()
    original_build_repl = server._build_repl

    def build_repl(*args, **kwargs):
        args[0].set_llm_model("0x567-flash")
        repl = original_build_repl(*args, **kwargs)
        repl.session.set_llm_model("0x567-flash")
        client.set_cancel_event(repl.agent.cancel_event)
        repl.agent.llm = client
        return repl

    def cancel_after_first_vision_batch(messages, _model, _call_number):
        if any(isinstance(message.get("content"), list) for message in messages):
            client.cancel_event.set()
            client.after_chat = None

    client.after_chat = cancel_after_first_vision_batch
    monkeypatch.setattr(server, "_build_repl", build_repl)
    handler = server.WsSessionHandler(paths)

    async def exercise():
        listener = await serve(handler.handle, "127.0.0.1", 0, max_size=65536)
        port = listener.sockets[0].getsockname()[1]
        ws = await websockets.connect(f"ws://127.0.0.1:{port}", open_timeout=5, close_timeout=5)
        events = []
        try:
            while True:
                event = json.loads(await asyncio.wait_for(ws.recv(), 5))
                if event.get("type") == "session.banner":
                    break
            await ws.send(json.dumps({
                "type": "user.message",
                "text": f'Please read all images in directory "{directory}"',
            }))
            while True:
                event = json.loads(await asyncio.wait_for(ws.recv(), 10))
                events.append(event)
                if event.get("type") == "turn.end":
                    break
        finally:
            await ws.close()
            listener.close()
            await listener.wait_closed()
        return events

    events = asyncio.run(exercise())
    warnings = [event.get("text", "") for event in events
                if event.get("type") == "turn.notice" and event.get("level") == "warn"]
    assert any(
        f"20 \u5f20" in text and f"1 \u5f20" in text and "\u53d6\u6d88" in text
        for text in warnings
    )
