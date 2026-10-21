# Chat Image Generation Tool Design

**Date:** 2026-10-21
**Status:** Implemented; automated tests run; live provider verification not performed.
**Scope:** Add image generation as a first-party my-agent tool. It is not an MCP client/server or a Skill.

## Goal

Allow my-agent to generate an image from a prompt and, when requested, use an image attached in the current or any prior local my-agent conversation as the reference image. Before any image bytes are sent to the provider, show the exact source conversation and attachment for user confirmation.

## User Flow

1. The user asks for an image. They may also describe the prior conversation/image to reuse.
2. The `generate_image` tool searches local session message logs on demand for image attachments whose surrounding conversation context matches the user's reference hint. Current-turn attachments are included in the same search.
3. If there are no candidates, return a clear not-found/unavailable result. If more than one candidate is plausible, return candidate IDs with conversation title/date, attachment filename, and nearby user text; the assistant asks the user to choose.
4. For a unique or explicitly selected candidate, show a confirmation describing the prompt, provider, possible credit use, and exact source conversation/attachment. A rejected confirmation sends no image bytes.
5. After approval, read the selected image from its local reference and call the 0x567 image API. Save the resulting PNG without overwriting existing files and return its path and metadata.

## Architecture

- Register one built-in `generate_image` tool through the existing ToolRegistry, LLM tool schema, and ToolExecutor confirmation path.
- Implement a small local-history resolver that scans `data/sessions/*/messages.jsonl` on demand, extracts user image-attachment references and nearby dialogue, validates that the referenced file still exists under the agent root or an already configured read-authorized HostScope root, and ranks by textual context. Host paths are considered only when a local session message explicitly references them. Terminal-harness records are included if they contain such attachment references. Do not add a persistent image catalog, embeddings, background indexing, or OS-wide file scanning.
- Keep candidate IDs opaque and reproducible from session/message/attachment identity; never expose absolute local paths to the model. On selection, resolve the ID again from current local logs and revalidate the path.
- Call the 0x567 image-generation/edit endpoints directly from the built-in tool; do not start or depend on the existing MCP process. Keep `LLM_0x567_KEY` backend-only, using my-agent's existing secret configuration and environment-variable precedence.
- Save outputs under the current project `artifacts/generated-images/`; in an unbound chat, use that conversation's local workspace drop area. Use exclusive creation so existing files are never overwritten.

## Safety and Failure Behavior

- The history search is read-only and limited to local my-agent session records and their exact referenced files, resolved under the agent root or an existing read-authorized HostScope root; it never scans host folders.
- Reference images are not sent to the provider until the user approves the confirmation for the exact selected source.
- Missing/deleted references, invalid paths, unsupported image formats, size limits, missing credentials, HTTP failures, malformed image payloads, and timeouts produce explicit tool errors; none is reported as successful generation.
- Do not log or return the API key. Do not include raw image bytes/base64 in prompts, tool logs, or conversation history.
- Keep the tool unavailable in ask/read-only mode and require an explicit confirmation for paid generation.

## Initial API Contract

- Tool name: `generate_image`.
- Required: non-empty `prompt`.
- Optional: `reference_context` for locating a prior/current image, `reference_id` returned by an ambiguous search, `model`, `size`, and `quality`.
- Supported reference media: PNG, JPEG, WebP; validate file signature and a bounded byte length before upload.
- Output: PNG path, model, byte length, and whether a reference image was used.
- Models/endpoints and request validation should follow the locally installed 0x567 image implementation; no live request is part of automated tests.

## Acceptance Criteria

1. The LLM receives a documented built-in `generate_image` schema and the executor dispatches it only in execute-capable mode.
2. Current-turn and historical user image attachments, including existing read-authorized host references and terminal-harness records, are found by nearby conversation text across all local sessions without an index or folder scan.
3. Ambiguous matches do not generate or upload; they return selectable candidates. Missing files are excluded or reported unavailable.
4. The exact selected source is visible in the confirmation preview; cancel/reject results in zero provider requests.
5. Accepted generation uses the 0x567 API with the configured server-side key, validates the returned PNG, and writes a unique non-overwriting output file in the intended workspace.
6. Unit tests cover resolver ranking/ambiguity/path validation, API payload and error handling, secret handling, confirmation gating, and output writing. Live paid generation is not run unless explicitly requested.
