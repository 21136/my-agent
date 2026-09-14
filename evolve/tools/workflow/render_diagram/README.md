# `render_diagram`

`render_diagram` 将 `design_diagram` 生成的 Mermaid 或 PlantUML 图源渲染为真实的 PNG、SVG 或 PDF 文件。

## 依赖

- Mermaid：本机可执行 `mmdc`，或通过 `MMDC_COMMAND` / `mermaid_command` 指定命令。
- PlantUML：本机可执行 `plantuml`，或提供 `PLANTUML_JAR` / `plantuml_jar`；JAR 模式需要 Java。

工具不会在找不到渲染器时伪造图片，而是返回缺少依赖、可直接修复的错误。

## 输入示例

```json
{
  "source_path": "workspace/demo/docs/login-sequence.mmd",
  "output_path": "workspace/demo/docs/login-sequence.png",
  "engine": "auto",
  "on_conflict": "overwrite"
}
```

## 调用顺序

1. 使用 `design_diagram` 生成并校验图源。
2. 使用 `render_diagram` 输出 PNG/SVG/PDF。
3. 将 PNG 作为 `design_document` 的 `figures` 输入，组装 Markdown 或 DOCX。
