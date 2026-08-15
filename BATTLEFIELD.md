# 主战场 · Desktop 编码工作台

> **2026-08-13** 从 `D:\my-agent` 完整源码复制（**不含** `workspace/`、`data/`、`node_modules/`）。

## 分工

| 目录 | 角色 |
|------|------|
| **`D:\my-agent-main`**（本目录） | **唯一开发工作区** — 只推进 Desktop / 编码工作台 |
| **`D:\my-agent`** | **留念冻结** — Terminal 狂野模式时代快照，见 `MEMORIAL.md` |

## 开发纪律

- **Terminal**：功能冻结；仅 P0 修复；继续作产品卖点（`start-terminal.bat`）
- **Desktop**：主战场；按教科书流程（需求 → 设计 doc → 单 task → IT/S → 发布）
- **范围**：个人本地 **编码 Agent**；不追大厂全场景

## 首次在本目录启动

```powershell
pip install -r requirements.txt
cd desktop && npm install && cd ..
.\start-desktop.bat
```

`workspace/`、`data/` 需在本机按需自行创建（不进 Git，未从留念目录复制）。

## Git

复制时含 `.git` 历史，可在此目录继续 commit / push。首次 push 前确认 `git remote -v` 与分支策略。
