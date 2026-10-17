---
name: resume-incomplete-work
description: Recover unfinished project work from disk, TASKS, Git diff, session evidence, and last verification without fabricating progress.
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
verification: current task, changed paths, last failure, and next safe action are explicit
version: 1.0.0
---

# Resume incomplete work

1. Reopen the actual project root/session first; do not rely on an old chat summary alone.
2. Read open tasks, changed files, Git status/diff, recent test output, and acceptance evidence. Treat missing evidence as unknown.
3. Separate file-level continuation from conversation-level continuation: existing code can survive a lost session, but the previous model context may not.
4. Recommend one next action: inspect, repair, test, plan, or execute. Do not auto-set `confirmed`, mark tasks done, publish, or resume a dangerous command.
5. After the user’s current request is execute, continue through the ordinary request-intent and path/risk policy.
