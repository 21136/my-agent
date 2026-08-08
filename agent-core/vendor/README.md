# Vendored terminal UI (MIT)

## clawcodex_prompt.py

Prompt/input UX adapted from [chennanli/clawcodex](https://github.com/chennanli/clawcodex) `src/repl/core.py` (MIT License).

ClawCodex itself is a full Claude Code rebuild (agent + tools + providers). We only vendor the **prompt_toolkit** input contract:

- Shift+Enter / Meta+Enter / `\`+Enter multiline
- Dark input row styling
- Bottom status toolbar hook

my-agent keeps its own `Agent`, sessions, and harness; only the input chrome is borrowed.
