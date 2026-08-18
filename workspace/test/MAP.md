# Music Dreamer 悦享音乐 · 代码地图

## 入口
- `web/`：用户端 Vue 3 应用。
- `creator-web/`：创作者中心 Vue 3 应用。
- `admin-web/`：后台管理 Vue 3 应用。
- `gateway-service/`：网关与统一鉴权。
- `user-service/`、`music-service/`、`interaction-service/`、`notification-service/`、`admin-service/`：后端微服务。
- 详细技术边界见 `TECH-DESIGN.md`，验证矩阵见 `VERIFY.md`。

## 现在卡在哪
- 当前前沿为 Phase 1：工程初始化与基础设施，首项为 T-001 初始化 Spring Cloud Alibaba 父工程；Nacos、Gateway、Vue3 前端、数据库 schema、Redis/MinIO/RabbitMQ 基础连接均待创建。
