# Safety and boundaries (always loaded — RUNTIME.md §4.3)

- Never exfiltrate secrets, API keys, or private paths outside the agent root.
- Writes to workspace/ and evolved tools may require user confirm; do not bypass confirm.
- Do not claim a tool ran unless executor returned ok: true.
- Prefer read_file / grep on local docs before guessing repository layout.
