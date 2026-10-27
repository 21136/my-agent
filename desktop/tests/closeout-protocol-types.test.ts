import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { resolve } from "node:path";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const desktopRoot = resolve(import.meta.dirname, "..");
const virtualFile = resolve(desktopRoot, "tests", "__closeout-contract.fixture.ts");

test("closeout domain types keep composer, chat, pet, and panel contracts in sync", () => {
  const fixture = `
    import type { StagedFileItem, ServerEvent } from "../src/api/ws";
    import type { ChatBlock, ChatSession } from "../src/shells/chat-state";
    import type { PetTurnSummary } from "../src/shells/unified/companion";
    import type { ProjectPanelState } from "../src/shells/unified/project-panel";

    declare const file: StagedFileItem;
    const imageCapable: boolean | undefined = file.image_input;
    declare const event: Extract<ServerEvent, { type: "tool.end" }>;
    const endDiff: string | undefined = "diff" in event && typeof event.diff === "string"
      ? event.diff
      : undefined;
    declare const chat: ChatSession;
    chat.pushReviewSubagentCard("running", { taskPreview: "review" });
    chat.updateReviewSubagentCard("done", { verdict: "pass" });
    declare const block: ChatBlock;
    const processBlocks = [block].filter(
      (item): item is Extract<ChatBlock, { kind: "process" }> => item.kind === "process",
    );
    declare const pet: PetTurnSummary;
    const runningName: string | undefined = pet.runningToolName;
    declare const panel: ProjectPanelState;
    const terminalBytes: number | undefined = panel.terminalSessions[0]?.output_bytes;
    void [imageCapable, endDiff, processBlocks, runningName, terminalBytes];
  `;
  const compilerOptions = {
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.ESNext,
    moduleResolution: ts.ModuleResolutionKind.Bundler,
    strict: true,
    skipLibCheck: true,
    noEmit: true,
    types: [],
  };
  const host = ts.createCompilerHost(compilerOptions);
  const getSourceFile = host.getSourceFile.bind(host);
  host.getSourceFile = (fileName, languageVersion, onError, shouldCreateNewSourceFile) =>
    resolve(fileName) === virtualFile
      ? ts.createSourceFile(fileName, fixture, languageVersion, true)
      : getSourceFile(fileName, languageVersion, onError, shouldCreateNewSourceFile);
  const program = ts.createProgram([virtualFile], compilerOptions, host);
  const diagnostics = ts.getPreEmitDiagnostics(program).filter((diagnostic) =>
    diagnostic.file && (
      resolve(diagnostic.file.fileName) === virtualFile
      || resolve(diagnostic.file.fileName).startsWith(resolve(desktopRoot, "src"))
    ),
  );
  assert.deepEqual(
    diagnostics.map((diagnostic) => ts.flattenDiagnosticMessageText(diagnostic.messageText, "\n")),
    [],
  );
});
