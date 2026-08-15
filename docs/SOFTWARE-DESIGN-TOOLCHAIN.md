# 软件工程设计文档工具链

> 状态：Phase 2 已实现，渲染器依赖待配置
> 日期：2026-08-14
> 目标：让需求分析、概要设计、数据库设计、详细设计中的文字、表格和图保持可追踪、可复用、可输出。

## 1. 背景

现有 `design_document` 可以生成四类设计文档的 Markdown 或 DOCX 模板，但图表目前只保留 Mermaid 源码，不能统一管理图源、校验图类型，也不能稳定渲染后嵌入 Word。

本工具链采用“结构化设计信息 → 图表源 → 渲染资产 → 文档”的分层方式，避免为每一种图和每一种文档格式制造孤立工具。

## 2. 目标范围

| 能力 | 目标 | 状态 |
|---|---|---|
| 文档模板 | 需求分析、概要设计、数据库设计、详细设计 | 已有 |
| 图表源 | Mermaid / PlantUML 源文件统一生成、校验和落盘 | Phase 1 |
| 图表渲染 | 调用本地 Mermaid CLI/PlantUML 输出 SVG/PNG/PDF，并记录渲染器 | Phase 2 已实现 |
| DOCX 组装 | 将图表图片、标题、表格和章节组合进 DOCX | Phase 3 已实现 |
| 设计审查 | 检查需求、用例、接口、数据表和图表之间的追踪关系 | Phase 4 |

## 3. 文档与图表矩阵

| 文档 | 常用图表 |
|---|---|
| 需求分析 | 用例图、业务流程图、活动图、状态图、需求追踪图 |
| 概要设计 | 系统上下文图、组件图、模块图、部署图、关键时序图 |
| 数据库设计 | ER 图、数据流图、表关系图、数据生命周期图 |
| 详细设计 | 类图、时序图、活动图、状态机、接口交互图、异常流程图 |

## 4. 工具分层

### 4.1 `design_document`

负责文档章节、表格、清单和最终 Markdown/DOCX 输出。它不负责推断复杂业务，也不直接承担所有图表渲染逻辑。

### 4.2 `design_diagram`

负责单张图的类型、引擎、源文件格式和安全落盘：

- `diagram_type`：`use_case`、`sequence`、`activity`、`state`、`class`、`component`、`deployment`、`er`、`flowchart`、`context`
- `engine`：`auto`、`mermaid`、`plantuml`
- `path`：`.mmd`/`.mermaid` 或 `.puml`/`.plantuml`
- `source`：图表源代码

默认引擎策略：用例图、类图、组件图、部署图优先 PlantUML；时序图、活动图、状态图、ER 图和流程图优先 Mermaid。用户显式指定引擎时覆盖默认策略。

### 4.3 `render_diagram` 与图表组装

`render_diagram` 只接收已校验的图源，调用本地 Mermaid CLI 或 PlantUML 输出 PNG/SVG/PDF；`design_document` 通过 `figures` 接收渲染资产，将图、图题、替代文本和章节引用放入 Markdown/DOCX。渲染失败必须返回可修正的错误，不得伪造“已生成图片”。

## 5. 关键决策

1. 图表源文件和最终文档分离保存，图源可审查、可版本化、可重新渲染。
2. 同一张图只能有一个源文件，Markdown 和 DOCX 共享同一图源。
3. 工具优先参数化和跨项目复用，不为“某个项目的某张图”创建专用工具。
4. 每个 active 工具必须完成 `INDEX → bucket → run_evolved` 发现链，并有专项测试。
5. DOCX 是真实 OOXML 文档；不能把 Markdown 文本伪装成 `.docx`。

## 6. 分阶段任务

- [x] 建立设计文档工具链留痕
- [x] 完成 `design_diagram` 图源生成与校验工具
- [x] 增加 Mermaid/PlantUML 渲染适配和依赖检测
- [x] 扩展 `design_document`，将 PNG/JPEG 渲染图片嵌入 DOCX
- [x] 增加设计文档与图表的章节引用关系
- [ ] 增加需求→用例→接口→数据表的追踪审查
- [ ] 建立完整 demo 和视觉验收样例

## 7. 当前验收口径

Phase 1 完成条件：

- 四类文档模板继续可用；
- `design_diagram` 能生成 Mermaid/PlantUML 源文件；
- 错误的图类型、引擎和扩展名组合会被拒绝；
- 主索引、二级桶和 `run_evolved` 调用链可发现；
- 工具 demo 和专项测试通过。

Phase 2/3 当前完成条件：

- `render_diagram` 能检测并调用真实本地渲染器，缺依赖时明确报错；
- `design_document.figures` 能生成 Markdown 图片引用，并将 PNG/JPEG 嵌入真实 DOCX；
- DOCX 图片写入替代文本和图题，便于审计和阅读；
- 当前机器未发现 `mmdc`、PlantUML 或 PlantUML JAR，因此尚未完成真实图形引擎渲染验收。

## 8. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-08-14 | 明确从文档模板扩展到软件工程设计工具链 |
| 2026-08-14 | 启动 Phase 1：图表源统一生成与校验 |
| 2026-08-14 | 完成 `design_diagram`：统一生成并校验 Mermaid/PlantUML 图表源 |
| 2026-08-14 | 完成 `render_diagram` 渲染适配器与 `design_document.figures` 图片组装 |
