# Multi-project schedule — real `run_project_workers` host E2E

## Coverage map (honest)

| Layer | What it proves | Model / host |
| --- | --- | --- |
| `schedule-workbench-multi-project-*` (#60–#72) | UI + WS **fixture** ack only | No Agent, no worker host |
| `project-worker-batch-browser-e2e` | Chat `user.message` → real host, two projects, mixed UI text | Mock HTTP transport |
| `project-worker-requirement-full-e2e` | Single project requirement + pytest | Mock HTTP transport |
| **`test_multi_project_schedule_dispatch_real_ws_e2e.py`** | Companion **`work-records.schedule.dispatch`** → real host, 2 mapped projects | Mock HTTP transport |
| **`multi-project-schedule-real-host-harness.test.mjs`** | Same path + desktop `buildScheduleDispatchWorkers` | Mock HTTP transport |
| **`multi-project-schedule-real-host-browser-e2e.test.mjs`** | Full unified shell 当前工作 schedule tab | Mock HTTP transport; **Win32** (`_run79`) |
| **`multi-project-schedule-real-host-live-browser-e2e.test.mjs`** | Same GUI path, real **0x567-flash** worker turns | Paid live LLM; **Win32 opt-in only** (`_run80`) |

**Not claimed:** Win32 live-LLM green in CI (needs keys + explicit `_run80`); Electron tray; merge/split 事项 UI.

POSIX CI uses a **test-only** parent-binding publish shim (production bind publication is Win32-native). Win32 local runs use native publication.

The GUI fixture keeps **real** `WsBridge.emit_session_state` (same as `project-worker-batch-browser-fixture.py`) so the unified shell receives `session.banner`, `session.list`, and the initial `work-records.overview.result` probe.

Unlike the **synthetic** schedule WS fixture, the real host answers `project.list` only after the client sends it (mount-time `listProjects()` runs before the socket is open and is skipped). The browser E2E explicitly calls `client.listProjects()` after `session.banner`, matching production companion-home behavior.

On **unbound** companion-home, local `work-records.overview.result` is often `no_records` with `unread_status: known`, which normally hides the 当前工作 topbar pill. When mapped project sessions exist, `buildCurrentWorkOverviewPanel` keeps the entry visible (`mappedMultiProjectSchedule`) so the schedule overlay remains reachable without binding the companion session.

Multi-project **只读概况** and **schedule.dispatch** each gate on host `confirm.request` / `confirm.response`. On companion-home the actionable surface is the composer **等待你的确认** strip (`data-pending-confirm-hint`); the browser E2E waits for `confirm.request`, clicks **同意** there, then waits for `confirm.done` before expecting `selected_project_sessions` overview or dispatch.

Real `work-records.schedule.dispatch` re-runs **project work-record source authorization** and then asks for a **launch preview** confirm (`confirm_with_receipt` on the normalized worker list). Additional work-record source-read confirms may follow before the host applies dispatch. The GUI harness **polls** for unsettled `confirm.request` entries (no matching `confirm.done`) and clicks composer **同意** as soon as the button is enabled — interleaved with waiting for `dispatch.done`, so a fourth `WORK_RECORD_SOURCE_READ_CONFIRM` is not left on the bar while the test blocks on a long single `waitForFunction`. Clicks run **in-page** (`acceptComposerConfirmById`) with re-query each attempt so a strip re-render (new `confirm.request` replacing the prior hint) does not detach Playwright locators mid-accept. The synthetic WS fixture skips these server-side confirms.

## Win32 GUI opt-in (mock model HTTP — `_run79`)

```bat
set REAL_MULTI_PROJECT_SCHEDULE_OPT_IN=1
set REAL_MULTI_PROJECT_SCHEDULE_GUI_OPT_IN=1
node --test desktop\tests\multi-project-schedule-real-host-browser-e2e.test.mjs
```

Or (opt-in contract + browser E2E, same env flags):

```bat
desktop\_run79-schedule-real-host.cmd
```

Shared Playwright flow: `desktop/tests/multi-project-schedule-real-host-browser-shared.mjs` (composer confirm interleave from #99/#100).

## Win32 GUI opt-in (paid live LLM — `_run80`)

**Honest label:** real provider HTTP for **project worker** turns (`0x567-flash` / gpt-6-luna). Not mock `httpx.MockTransport`. Companion session remains **unbound**.

**Credentials:** fixture resolves `ModelRegistry` → `resolve_api_key` on the discovered agent root (same vault/env pattern as other `*_live_optin.py` tests). No keys in repo or argv.

```bat
desktop\_run80-schedule-real-host-live.cmd
```

Equivalent:

```bat
set REAL_MULTI_PROJECT_SCHEDULE_LIVE_OPT_IN=1
set REAL_MULTI_PROJECT_SCHEDULE_GUI_OPT_IN=1
node --test desktop\tests\multi-project-schedule-real-host-live.optin.test.mjs
node --test desktop\tests\multi-project-schedule-real-host-live-browser-e2e.test.mjs
```

Optional timeouts (defaults inside fixture / drain loop):

| Variable | Default | Role |
| --- | --- | --- |
| `REAL_MULTI_PROJECT_SCHEDULE_LIVE_TIMEOUT_SEC` | `120` | Per-request `LLM_TIMEOUT_SEC` for workers |
| `REAL_MULTI_PROJECT_SCHEDULE_LIVE_DISPATCH_CONFIRM_TIMEOUT_MS` | `600000` | Composer confirm interleave while waiting for `dispatch.done` |

**Cost / duration (expectation, not a guarantee):** two bounded no-tool worker tasks, up to **2** model requests per worker (`max_requests=2`), plus overview/dispatch confirms — typically on the order of **tens of seconds to a few minutes** depending on provider latency; budget **~10–15 minutes** wall clock for the browser test timeout.

**Success looks like:**

- `work-records.schedule.dispatch.done` with `status: "applied"` and `tool_ok: true`
- `workers[]` length **2** with terminal statuses (`model_turn_finished`, `failed`, `limit_reached`, … — not asserting mock’s alpha/beta split)
- `session.banner` / companion `project_id` still empty
- No `user.message.persisted` on companion

**Not claimed:** POSIX/CI or agent green without Win32 `_run80` and configured provider credentials.

Edge launches with Local Network Access checks disabled so loopback WebSocket to the fixture host is allowed (same flags as `schedule-workbench-multi-project-browser-e2e.test.mjs`).

Evidence: dispatch `status=applied`, per-project `workers[]` statuses (e.g. `model_turn_finished` + `failed`), companion session remains **unbound** (`project_id` empty).

## POSIX / CI (no Playwright)

```bash
python3 -m pytest agent-core/tests/test_multi_project_schedule_dispatch_real_ws_e2e.py -q
node --test desktop/tests/multi-project-schedule-real-host-harness.test.mjs
```

## Product fix bundled here

`agent.py` schedule-preview rebuild for `schedule_candidates` now includes requirement **`text`**, so `work-records.schedule.dispatch` can authorize the same candidates the overview preview showed.

## Still open

- Win32 **`_run80`** green evidence after local paid run (harness ships; green is operator-owned).
- Merge/split 事项 UI.
- Tray double-click automation.
