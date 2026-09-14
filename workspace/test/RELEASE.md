# Music Dreamer 悦享音乐 · 发布

## REL-001 部署依赖与环境
- 基础组件：Nacos 2.x、MySQL 8、Redis 7、MinIO、RabbitMQ 3.x。
- 运行时：JVM 17；前端 Node 18+；Nginx 托管前端与反向代理。
- 服务：gateway、user、music、interaction、notification、admin。

## REL-002 密钥与配置管理
- `NACOS_PASSWORD`、`DB_PASSWORD`、`REDIS_PASSWORD`、MinIO 密钥、RabbitMQ 密钥和 `JWT_SECRET` 只能通过环境变量或加密配置注入。
- 生产环境禁止 MinIO 公共写；对象 URL 使用签名 URL 或受限桶策略。

## REL-003 数据库迁移与启动顺序
- 初始化 Nacos、RabbitMQ、MinIO 后按 user、music、interaction、notification、admin 顺序执行 schema。
- 启动顺序：Nacos → MySQL → Redis → MinIO → RabbitMQ → 后端服务 → 前端 Nginx。

## REL-004 健康检查与发布验收
- 各服务暴露 `/actuator/health`，Nacos 能看到全部实例；执行 VERIFY.md V-001 至 V-010 并记录证据。

## REL-005 回滚
- 发布前保留上一版本 jar、dist 和配置快照；摘流量后回滚后端、前端和缓存，随后执行健康检查与冒烟测试。
