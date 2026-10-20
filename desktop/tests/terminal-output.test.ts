import assert from "node:assert/strict";
import test from "node:test";

const formatterModule = import("../src/shells/unified/terminal-output.ts").catch(() => undefined);

test("removes terminal ANSI control sequences but preserves visible output", async () => {
  const module = await formatterModule as { formatTerminalOutputForDisplay?: (value: string) => string } | undefined;
  assert.ok(module, "terminal output formatter should be available");
  assert.equal(typeof module.formatTerminalOutputForDisplay, "function");
  assert.equal(
    module.formatTerminalOutputForDisplay!("\u001b[0mTURN_TERMINAL_QA_OK\u001b[0K\r\n\u001b[?25h"),
    "TURN_TERMINAL_QA_OK\n",
  );
});
