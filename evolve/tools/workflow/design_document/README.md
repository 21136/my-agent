# `design_document`

统一生成软件设计文档，支持四种文档类型：

- `requirements`：需求分析
- `high_level_design`：概要设计
- `database_design`：数据库设计
- `detailed_design`：详细设计

输出格式由 `output_format` 或 `path` 后缀决定：

- `markdown` / `md`：UTF-8 Markdown
- `docx`：真正的 Word 文档，使用统一的技术文档样式
- `auto`：`.docx` 路径输出 Word，其它路径输出 Markdown

DOCX 输出需要项目环境安装 `python-docx`：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

推荐路径：`workspace/<project>/docs/<document>.md` 或 `workspace/<project>/docs/<document>.docx`。

## 插入图表

先用 `design_diagram` 生成图源，再用 `render_diagram` 输出 PNG/JPEG，最后通过 `figures` 传入文档：

```json
{
  "path": "workspace/demo/docs/high-level-design.docx",
  "doc_type": "high_level_design",
  "figures": [
    {
      "path": "workspace/demo/docs/context.png",
      "section": "2. 总体架构",
      "caption": "系统上下文图",
      "alt_text": "客户端、服务和数据库之间的关系",
      "width_inches": 6.2
    }
  ]
}
```

Markdown 会插入图片引用；DOCX 会嵌入真实图片，并写入图题和替代文本。
