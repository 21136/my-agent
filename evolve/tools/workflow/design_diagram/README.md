# `design_diagram`

`design_diagram` 是软件工程图表源文件的统一入口，不负责当前阶段的图片渲染。

支持图类型：`use_case`、`sequence`、`activity`、`state`、`class`、`component`、`deployment`、`er`、`flowchart`、`context`。

默认引擎：

- PlantUML：用例图、类图、组件图、部署图
- Mermaid：时序图、活动图、状态图、ER 图、流程图、上下文图

工具会检查图类型、引擎和文件后缀，并自动补充 Mermaid 元数据或 PlantUML 的 `@startuml` / `@enduml` 包装。
