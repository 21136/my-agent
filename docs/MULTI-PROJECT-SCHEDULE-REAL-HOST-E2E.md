# Multi-project schedule — real `run_project_workers` host E2E

## Coverage map (honest)

| Layer | What it proves | Model / host |
| --- | --- | --- |
| `schedule-workbench-multi-project-*` (#60–#72) | UI + WS **fixture** ack only | No Agent, no worker host |
| `project-worker-batch-browser-e2e` | Chat `user.message` → real host, two projects, mixed UI text | Mock HTTP transport |
| `project-worker-requirement-full-e2e` | Single project requirement + pytest | Mock HTTP transport |
| **`test_multi_project_schedule_dispatch_real_ws_e2e.py`** | Companion **`work-records.schedule.dispatch`** → real host, 2 mapped projects | Mock HTTP transport |
| **`multi-project-schedule-real-host-harness.test.mjs`** | Same path + desktop `buildScheduleDispatchWorkers` | Mock HTTP transport |
| **`multi-project-schedule-real-host-browser-e2e.test.mjs`** | Full unified shell 当前工作 schedule tab | Mock HTTP transport; **Win32** |

**Not claimed:** paid live LLM, Electron tray, merge/split 事项 UI.

POSIX CI uses a **test-only** parent-binding publish shim (production bind publication is Win32-native). Win32 local runs use native publication.

The GUI fixture keeps **real** `WsBridge.emit_session_state` (same as `project-worker-batch-browser-fixture.py`) so the unified shell receives `session.banner`, `session.list`, and the initial `work-records.overview.result` probe.

Unlike the **synthetic** schedule WS fixture, the real host answers `project.list` only after the client sends it (mount-time `listProjects()` runs before the socket is open and is skipped). The browser E2E explicitly calls `client.listProjects()` after `session.banner`, matching production companion-home behavior.

On **unbound** companion-home, local `work-records.overview.result` is often `no_records` with `unread_status: known`, which normally hides the 当前工作 topbar pill. When mapped project sessions exist, `buildCurrentWorkOverviewPanel` keeps the entry visible (`mappedMultiProjectSchedule`) so the schedule overlay remains reachable without binding the companion session.

Multi-project **只读概况** and **schedule.dispatch** each gate on host `confirm.request` / `confirm.response`. On companion-home the actionable surface is the composer **等待你的确认** strip (`data-pending-confirm-hint`); the browser E2E waits for `confirm.request`, clicks **同意** there, then waits for `confirm.done` before expecting `selected_project_sessions` overview or dispatch.

Real `work-records.schedule.dispatch` re-runs **project work-record source authorization** and then asks for a **launch preview** confirm (`confirm_with_receipt` on the normalized worker list). The GUI harness accepts each **new** `confirm.request` in order (count-based), not the stale overview confirm still present in `receivedEvents`. The synthetic WS fixture skips these server-side confirms.

## Win32 GUI opt-in

```bat
set REAL_MULTI_PROJECT_SCHEDULE_OPT_IN=1
set REAL_MULTI_PROJECT_SCHEDULE_GUI_OPT_IN=1
node --test desktop\tests\multi-project-schedule-real-host-browser-e2e.test.mjs
```

Or (opt-in contract + browser E2E, same env flags):

```bat
desktop\_run79-schedule-real-host.cmd
```

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

- Live-LLM multi-project GUI green (needs local keys + opt-in run above with real transport).
- Merge/split 事项 UI.
- Tray double-click automation.
