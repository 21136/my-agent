# Music Dreamer 悦享音乐 · 代码地图

## 入口
- `web/`：用户端 Vue 3 应用。
- `creator-web/`：创作者中心 Vue 3 应用。
- `admin-web/`：后台管理 Vue 3 应用。
- `gateway-service/`：网关与统一鉴权。
- `user-service/`、`music-service/`、`interaction-service/`、`notification-service/`、`admin-service/`：后端微服务。
- 详细技术边界见 `TECH-DESIGN.md`，验证矩阵见 `VERIFY.md`。

## 现在卡在哪
- 当前前沿为 Phase 4：核心交互与通知，已完成 T-001 初始化 Spring Cloud Alibaba 父工程、T-002 接入 Nacos 注册/配置中心并输出 bootstrap.yml 约定、T-003 建立 API Gateway 统一入口与基础路由、T-004 初始化 Vue3 前端工程、路由、Pinia、Axios 封装、T-005 初始化 MySQL 8 各服务 schema 与基础字典数据（V-014）、T-006 接入 Redis、MinIO、RabbitMQ 基础连接与客户端配置（V-015）、T-007 实现用户注册/登录/登出与密码加密（V-016）、T-008 实现 JWT 鉴权与网关白名单（V-017）、T-009 建立 RBAC 用户/管理员/歌手角色权限（V-018）、T-010 实现管理员账号初始化与登录审计（V-019）、T-011（V-020）、T-012（V-021）、T-013（V-023）、T-014（V-024）；下一项正式任务为 T-015。
