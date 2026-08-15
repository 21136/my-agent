# 设计文档（design）

## 工具

| 工具 | 作用 |
|------|------|
| `design_document` | 统一生成需求分析、概要设计、数据库设计、详细设计的 Markdown 或真正的 DOCX |
| `design_diagram` | 生成并校验 Mermaid 或 PlantUML 图表源文件 |
| `render_diagram` | 使用本地 Mermaid CLI 或 PlantUML 将图源渲染为 PNG、SVG 或 PDF |

## 调用

通过 `run_evolved` 调用，`tool_name` 分别使用 `design_document`、`design_diagram` 或 `render_diagram`。

必填参数：

- `path`：输出路径；项目内推荐 `workspace/<project>/docs/<name>.md` 或 `.docx`
- `doc_type`：`requirements`、`high_level_design`、`database_design`、`detailed_design`

常用参数：

- `project_name`：项目名称
- `title`：文档标题
- `summary`：文档说明或背景摘要
- `output_format`：`auto`、`markdown`、`md`、`docx`；默认 `auto`
- `on_conflict`：`skip`、`rename`、`overwrite`；默认 `skip`
- `dry_run`：只预览路径，不写文件

`output_format = "auto"` 时，`.docx` 后缀生成真正的 Word 文档，其它后缀生成 UTF-8 Markdown。显式指定 `docx` 时，`path` 必须以 `.docx` 结尾；DOCX 输出依赖项目环境中的 `python-docx`。

`design_diagram` 必填 `path`、`diagram_type`、`source`；可选 `engine`（`auto`、`mermaid`、`plantuml`）、`title`、`on_conflict` 和 `dry_run`。用例/类/组件/部署图默认 PlantUML，其余支持类型默认 Mermaid；工具当前生成图源，不伪造渲染图片。

`render_diagram` 必填 `source_path`、`output_path`；可选 `engine`、`mermaid_command`、`plantuml_command`、`plantuml_jar`、`on_conflict` 和 `dry_run`。它只调用真实本地渲染器，找不到依赖时返回安装或配置提示。

`design_document` 可选 `figures` 数组；每项包含 `path`、`section`、`caption`、`alt_text` 和 `width_inches`。Markdown 写入图片引用，DOCX 嵌入 PNG/JPEG。

示例：

```json
{
  "tool_name": "design_document",
  "arguments": {
    "path": "workspace/demo/docs/database-design.docx",
    "doc_type": "database_design",
    "project_name": "demo",
    "output_format": "auto"
  }
}
```
