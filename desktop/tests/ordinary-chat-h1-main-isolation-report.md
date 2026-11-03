# ordinary-chat H1 main-process isolation hardening

Date: 2026-10-31

## Changes

- H1 sidecar launch is pinned to the verified profile interpreter and sealed `app_root`; a mismatched root or missing private bootstrap now fails before process spawn.
- The launch script is pinned to the sealed app's `agent-core/server.py`, with `cwd` pinned to the sealed app root.
- H1 candidate-sidecar switching and CLI switching are rejected. The existing T4 recovery profile path is left enabled.
- The ordinary-chat H1 runner and its unconditional isolation-repair prohibition were not edited.

## Focused verification

- `node --check desktop/tests/ordinary-chat-h1-main-isolation.test.mjs` — passed.
- `node --test desktop/tests/ordinary-chat-h1-main-isolation.test.mjs` — 3 passed, 0 failed.
- `git -c core.whitespace=cr-at-eol diff --check -- desktop/electron/main.ts desktop/tests/ordinary-chat-h1-main-isolation.test.mjs` — passed.

No GUI, service, model, or full test suite was started. No user data was accessed or changed. These source-contract tests do not establish live Electron/Host acceptance.
