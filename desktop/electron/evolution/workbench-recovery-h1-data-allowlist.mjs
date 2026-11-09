/** Top-level `data/` names permitted for workbench-crash-reopen-recovery-v1 (seed + after real sidecar). */
export const WORKBENCH_RECOVERY_ALLOWED_DATA_ROOT_ENTRY_LIST = [
  "sessions",
  "state.json",
  ".file-guard",
  ".session-guard",
  "logs",
  "auto-recognition-preference.sqlite3",
];

export const WORKBENCH_RECOVERY_ALLOWED_DATA_ROOT_ENTRIES = new Set(WORKBENCH_RECOVERY_ALLOWED_DATA_ROOT_ENTRY_LIST);
