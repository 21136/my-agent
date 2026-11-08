<!-- prompt_id: project · version: 2.0.0 · phase: 47 -->

# 项目模式入口

本段在会话绑定项目（`active_shell=project` / 有 `project_root`）时注入 system（PROJECT-MODE §7b），并与常驻的 `core.txt` 共同生效。项目模式提示由项目边界与交付规范组成；不会根据会话 topics 自动加载 coding 提示。
产品形态：**项目窗口** = 普通窗口中的项目聊天 + 左侧会话/文档/项目侧栏。

**项目模式注入组成**：`project-boundaries.md` + `project-delivery-{solo|ritual}.md`（由 `project_delivery_profile` 决定，见 overlay）。Coding 主题的注入由独立主题选择决定。
