# 文档阅读体验调研（2026-09-14）

对照开源 / 现代产品，反哺 my-agent Desktop「查看文档」。

## 我们现状（痛点）

- Overlay 文件列表：emoji + 文件名 →「主区阅读」整页 markdown
- 阶段区暴露 `PROJECT.md` / revision / completeness 等内部词
- 像文件浏览器 + 状态机，不像「读懂项目」

## 赢家模式（按优先级）

1. **人话标题优先于路径**  
   Mintlify / Docusaurus：`title` / `sidebarTitle` / `sidebar_label` 主导航；路径只是实现细节。缺省时才从文件名推导标题。  
   - https://www.mintlify.com/docs/organize/pages  
   - https://docusaurus.io/docs/sidebar/items

2. **阅读面 = 三栏经典结构**  
   左：分组导航（类别/角色）；中：阅读正文；右（或可折叠）：章节 Outline。Obsidian Outline + Reading view 是本地 markdown 标杆。  
   - https://obsidian.md/help/plugins/outline  
   - https://obsidian.md/help/edit-and-read

3. **对话里先预览，再进完整阅读**  
   OpenHands Agent Canvas：agent 写出的 md **内联富文本预览**（限高+滚动），一键 `View` 进 Files；聊天里提到的文件可点开定位。富文本 / 纯文本可切换。  
   - https://docs.openhands.dev/openhands/usage/agent-canvas/conversations  
   - https://www.openhands.dev/blog/new-in-agent-canvas-august-2026

4. **Specs 不当成「文件柜」，当成工作面**  
   - SpecKit Assistant：Kanban / DAG / Agent runs + WYSIWYG，文件系统仍是真相源但 UI 不暴露原始文件感。 https://github.com/dmux/speckit-assistant/  
   - SDD Visualizer：需求→设计→任务可追溯矩阵，**不靠读 raw md**。 https://marketplace.visualstudio.com/items?itemName=sheikh-suhail-khursheed.sdd-visualizer  
   - Backlog.md：任务是 md，Web UI 是看板+表单，搜索跨 tasks/docs。 https://github.com/apetersson/Backlog.md  
   - YorZ：明确问题是「Agent 写太快 → 人不再读 spec」→ 用可视化降过载。 https://github.com/hughfenghen/yorz

5. **元数据分层：用户层 vs 调试层**  
   OpenHands：默认隐藏 LLM 元数据 / 保留标签；路径行可复制但可折叠。Completeness / revision 应进「详情」或开发者模式，不要顶栏主文案。

6. **Continue 路线提示**  
   旧 `@Docs` 上下文已弃用，转向 Agent 工具读文档 + rules；产品趋势是「Agent 自己去读」，UI 更要让人**快速核对**，不是堆文件列表。  
   - https://docs.continue.dev/guides/codebase-documentation-awareness

## Demo 感反模式（我们中招的）

| 反模式 | 为何像 demo |
|--------|-------------|
| 列表项 = `PROJECT.md` + emoji | 像资源管理器，不像产品对象 |
| 顶栏刷 revision / completeness | 像运维面板 |
| 整页倾倒 raw markdown | 没有大纲、没有「现在该看哪」 |
| 新建 = 输入文件名 | 用户要的是「加一份说明」，不是造路径 |
| 文档与任务脱节 | 任务卡不链到段落/验收 |

## 对 my-agent 的直接建议

**信息架构**

| 内部文件 | 用户标题 | 角色一句话 |
|----------|----------|------------|
| PROJECT.md | 项目说明 | 这是什么、成功长什么样 |
| SCOPE.md | 范围 | 做什么 / 不做什么 |
| DESIGN.md | 方案 | 怎么做 |
| TECH-DESIGN.md | 技术要点 | 关键技术取舍 |
| TASKS.md | 任务 | 拆成可执行项 |
| VERIFY.md | 验收 | 怎么证明做完 |
| ENV.md | 环境与质量 | 怎么跑、测什么 |
| RELEASE.md | 发布 | 怎么交出去 |

导航按**阶段角色分组**（了解 / 方案 / 执行 / 验收），不要按文件系统排序。

**浏览手感（OpenHands + Obsidian 杂交）**

1. 聊天 / 阶段卡：短预览卡片（标题 + 摘要 2–3 行 +「打开」），不是先跳文件列表  
2. 打开后：**左栏文档角色列表（人话）+ 中栏阅读 + 右栏大纲**；路径放次要、可复制  
3. `Rich / 源码` 切换（对齐 OpenHands Files rich/plain）  
4. 任务 / blocker chip → 深链到对应文档章节（对齐 SDD Visualizer CodeLens 思路）  
5. 内部字段（revision、completeness、textbook）默认折叠进「文档状态」

## 建议 MVP 切片

1. **IA 改名层**：列表与顶栏全部人话标题；`*.md` 降为 subtitle  
2. **阅读壳**：固定三区（列表 / 正文 / 大纲），干掉整页倾倒 + inline style overlay  
3. **内联预览**：agent 写/改文档后在对话里出限高预览卡  
4. **任务深链**：从 TASKS / blocker 点进文档锚点  
5. **元数据降噪**：completeness 等进二级面板

## 不必照搬

- 完整第二套 Kanban（已有任务面板）— 先把「读」做好  
- 另起 docs 站点（Mintlify）— 我们是项目内工件，不是对外 docs  
- 立刻 WYSIWYG 编辑 — 先阅读面，编辑可第二期
