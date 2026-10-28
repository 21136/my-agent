# 项目配方

项目配方由 `agent-core/scaffold_recipes.py` 定义，用于选择/生成项目脚手架内容。配方只是创建辅助，不授予后续写入或执行权限；生成结果应核对实际文件与用户目标。

项目测试和质量检查分别见 `agent-core/project_verify.py`、`agent-core/project_quality.py`。不要求 TASKS 队列、Progress Gate 或旧阶段权限链。脚手架失败、依赖安装、服务启动和外部副作用仍受相应工具策略及确认约束。
