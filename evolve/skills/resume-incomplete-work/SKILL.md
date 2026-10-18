---
name: resume-incomplete-work
description: Recover unfinished project work from disk, Git diff, session evidence, and the last verification run without fabricating progress.
triggers:
  - 写了一半
  - 继续项目
  - 续接项目
  - 接着做
  - resume work
  - continue the project
allowed_tools:
  - read_file
  - list_dir
  - grep
  - codebase_search
  - run_command
  - run_tests
risk_level: medium
verification: changed paths, last verify result, and next safe action are explicit
version: 1.1.0
---

# Resume incomplete work

1. Reopen the actual project root/session first; do not rely on an old chat summary alone.
2. Read Git status/diff, recently changed files, the project docs (PROJECT/DESIGN/VERIFY), and the most recent test or build output. Treat missing evidence as unknown. Do not rely on a `TASKS.md` checklist; it is no longer maintained.
3. Separate file-level continuation from conversation-level continuation: existing code can survive a lost session, but the previous model context may not.
4. Re-run the verify command if it is cheap; its exit code tells you where things really stand.
5. Recommend one next action: inspect, repair, test, or implement. Do not publish or resume a dangerous command on your own.
