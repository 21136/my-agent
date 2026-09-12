# interactive_terminal

Experimental P0 tool for a persistent local PTY session.

- `start`: create a session and return `session_id`.
- `input`: write text to the same session.
- `read`: read output from a byte cursor without rereading old output.
- `signal`: send `ctrl_c`, `eof` (Ctrl-D input convention), or `terminate`.
- `status` / `list` / `close`: inspect or close sessions.

The worker uses `pywinpty` on Windows. Session state and bounded output are
stored below `data/interactive-terminals/`; user-facing tool calls remain
routed through the normal executor confirmation and path policy. The normal
`run_evolved` agent/server host loads this adapter in-process so the worker is
owned by the long-lived host and survives later tool calls. Running
`main.py` or one-shot CLI commands directly cannot provide cross-process
session persistence on hosts that reap short-lived tool descendants.

Install the optional Windows backend with:

```text
python -m pip install -r requirements-optional.txt
```

PTY output is one merged stream, not separate stdout/stderr channels. `read`
uses a logical byte cursor and may report `cursor_reset` after old output has
been trimmed. On Windows, line-feed input is normalized to CRLF before it is
written to the console. `eof` sends `Ctrl-D` as input; programs that require a
platform-specific console EOF sequence may not treat it as EOF.
