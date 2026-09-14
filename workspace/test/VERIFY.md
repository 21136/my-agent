# Music Dreamer 悦享音乐 · 验证矩阵

> 状态：首批工程骨架验证通过；其余业务验收仍待执行。

| 验证编号 | 对应需求/任务 | 验证方法 | 预期结果 | 结果 | 证据 |
|---|---|---|---|---|---|
| V-000 | REQ-001 / AC-001 / T-001 | scaffold_music_dreamer dry-run 后实际生成 | dry-run 只返回文件清单；实际生成成功，父工程和服务边界文件存在 | 通过 | `.venv\\Scripts\\python.exe` 驱动 `run_command`；dry-run/实际均 exit 0；生成 `workspace/test/.acceptance/music-dreamer-skeleton` 共 15 个文件 |
| V-001 | AC-001 / T-013 | GET /recommend/songs | 返回至少 10 条推荐且字段完整 | 待执行 | 未记录 |
| V-023 | AC-001 / T-013 | 检查 RecommendationService/RecommendationController 实现 GET /recommend/songs、标签过滤、播放热度降序、标题稳定排序、limit 边界；RecommendationServiceTest；执行 `mvn -q clean test` | exit 0；接口实现、测试与边界条件均通过 | 通过 | 最近 `mvn -q clean test` exit 0；代码检查满足推荐接口、标签/热度排序与 limit 边界 |
| V-002 | AC-002 / T-014 | 收藏与歌单接口+前端 | 收藏状态和歌单顺序正确 | 待执行 | 未记录 |
| V-003 | AC-003 / T-015,T-012 | 播放历史接口+DB | 历史写入、查询、清空持久化生效 | 待执行 | 未记录 |
| V-004 | AC-004 / T-016 | 通知接口+前端 | 未读标记出现，查看后未读数减一 | 待执行 | 未记录 |
| V-005 | AC-005 / T-017,T-011,T-019 | 上传审核端到端测试 | 待审核、通过可播放、驳回有原因 | 待执行 | 未记录 |
| V-006 | AC-006 / T-018,T-019 | 管理接口+前端 | 可检索禁用用户并下架歌曲 | 待执行 | 未记录 |
| V-007 | REQ-005/006 / T-009,T-011,T-019 | 角色权限测试 | 未审核不可上传/搜索，禁用不可登录 | 待执行 | 未记录 |
| V-008 | REL-001 / T-020,T-021,T-022,T-023 | 发布/回滚演练 | 服务可访问且发布回滚通过 | 待执行 | 未记录 |
| V-009 | REL-002 / T-017,T-006 | 分片上传测试 | 可续传、URL 鉴权生效 | 待执行 | 未记录 |
| V-010 | REQ-001/AC-001 / T-013 | 推荐接口测试 | 使用热度与标签规则 | 待执行 | 未记录 |
| V-011 | T-002 | 构建与配置检查：在 workspace/test/.acceptance/music-dreamer-skeleton 执行 mvn -q -DskipTests package | exit 0；Nacos discovery/config 依赖、music-song bootstrap.yml、基础设施模板与约定文档均存在 | 通过 | 工作区执行 mvn -q -DskipTests package exit 0；发现 music-song/src/main/resources/bootstrap.yml 配置 Nacos config/discovery |
| V-012 | T-003 | gateway-service 构建与测试：检查 gateway-service 模块、Spring Cloud Gateway/Nacos 依赖、基础路由与 health 配置、GatewayApplicationTest；执行 `mvn -q test` | exit 0；gateway-service 模块存在，Spring Cloud Gateway/Nacos 依赖、基础路由与 health 配置齐全，GatewayApplicationTest 通过 | 通过 | 在 gateway-service 执行 `mvn -q test` exit 0 |
| V-013 | T-004 | frontend 构建：Vue3/Vite/Pinia/Vue Router 页面、路由和 store 已实现；执行 `npm run build` | exit 0；Vue3/Vite/Pinia/Vue Router 页面、路由和 store 存在且构建通过 | 通过 | 在 frontend 执行 `npm run build` exit 0 |
| V-014 | T-005 | database/schema.sql 初始化 music_user/music_music/music_interaction/music_notification/music_admin 五个 MySQL schema、核心业务表和幂等字典数据；database/mysql8-check.sql 检查脚本；执行 `mvn -q test` | 静态 schema 检查 exit 0；`mvn -q test` exit 0；MySQL 客户端可用但本机 root 无密码导致实际连接验证 exit 1（不伪装为通过） | 通过（静态与构建） | schema.sql 与 mysql8-check.sql 存在；静态 schema 检查 exit 0；`mvn -q test` exit 0；实际连接验证 exit 1，未伪装为通过 |
| V-015 | T-006 | 检查 music-song 已接入 Spring Boot Redis、RabbitMQ、MinIO 客户端配置，application.yml 环境变量约定、StorageProperties/InfrastructureConfiguration、infrastructure/docker-compose.yml；执行 `mvn -q test`、基础设施配置静态检查、`docker compose -f infrastructure/docker-compose.yml config` | exit 0；music-song 的 Redis、RabbitMQ、MinIO 客户端配置与基础设施约定均存在 | 通过 | `mvn -q test` exit 0；基础设施配置静态检查 exit 0；`docker compose -f infrastructure/docker-compose.yml config` exit 0 |
| V-016 | T-007 | user-service 注册/登录/登出接口、BCrypt 密码哈希、JWT token、重复用户名/错误密码测试；执行根目录 `mvn -q clean test` | exit 0；注册/登录/登出接口存在，密码使用 BCrypt 哈希，JWT token 签发/校验通过，重复用户名/错误密码测试通过 | 通过 | 根目录 `mvn -q clean test` exit 0；user-service 已签发 JWT token；JWT token 签发/校验通过；重复用户名/错误密码测试通过 |
| V-017 | T-008 | 检查 user-service JwtService 使用 JJWT 签发/解析 JWT 并携带 role；gateway-service JwtAuthenticationFilter 的 /auth/**、/health 白名单，缺失/非法 token 返回 401；RoleAuthorizationFilter 对 /admin/** 要求 ADMIN、/creator/** 要求 ARTIST/ADMIN；确认 JWT_SECRET 外置配置；执行 `mvn -q clean test` | exit 0；JwtService 签发/解析 JWT 并携带 role；网关白名单放行 /auth/**、/health；缺失/非法 token 返回 401；/admin/** 仅 ADMIN、/creator/** 仅 ARTIST/ADMIN；JWT_SECRET 外置配置 | 通过 | 最近 `mvn -q clean test` exit 0；代码检查满足上述 JWT 鉴权、网关白名单与角色要求 |
| V-018 | T-009 | 检查 Role/RbacService/RbacController 实现 USER/ARTIST/ADMIN 三角色、默认 USER、角色分配；对 /admin/** 与 /creator/** 路径授权测试；执行 `mvn -q clean test` | exit 0；Role/RbacService/RbacController 存在，USER/ARTIST/ADMIN 三角色可用，默认 USER；/admin/** 仅 ADMIN、/creator/** 仅 ARTIST/ADMIN；`mvn -q clean test` exit 0 | 通过 | 最近 `mvn -q clean test` exit 0；代码检查满足 RBAC 角色与路径授权要求 |
| V-019 | T-010 | 检查 admin-service AdminAccountService 幂等初始化 admin、BCrypt 密码校验、AdminAuthController 登录、AuditLogService 登录成功/失败审计及相关测试；执行 `mvn -q clean test` 和 T-010 静态检查 | exit 0；AdminAccountService 幂等初始化 admin，BCrypt 密码校验通过，AdminAuthController 登录可访问，AuditLogService 记录登录成功/失败审计，测试通过；`mvn -q clean test` exit 0，T-010 静态检查 exit 0 | 通过 | 最近 `mvn -q clean test` exit 0；T-010 静态检查 exit 0 |
| V-020 | T-011 | music-song ArtistApplicationService/Controller 实现歌手申请、PENDING 状态幂等、管理员 APPROVED/REJECTED 审核、驳回原因校验、ArtistApplicationServiceTest；执行 `mvn -q clean test` | exit 0；歌手申请接口存在，PENDING 状态幂等，管理员 APPROVED/REJECTED 审核有效，驳回原因校验通过，ArtistApplicationServiceTest 通过 | 通过 | `mvn -q clean test` exit 0；music-song 实现 ArtistApplicationService/Controller、PENDING 状态幂等、管理员 APPROVED/REJECTED 审核、驳回原因校验、ArtistApplicationServiceTest |

| V-023 | T-013 | 检查 RecommendationService/RecommendationController 实现 GET /recommend/songs，支持标签过滤、播放热度降序、标题稳定排序、limit 边界；RecommendationServiceTest；执行 `mvn -q clean test` | exit 0；接口实现满足标签过滤、热度降序、稳定排序和 limit 边界，测试通过 | 通过 | 最近 `mvn -q clean test` exit 0；代码检查与 RecommendationServiceTest 通过 |
| V-023 | T-013 | 检查 RecommendationService/RecommendationController 实现 GET /recommend/songs，支持标签过滤、播放热度降序、标题稳定排序、limit 边界；执行 RecommendationServiceTest 与 `mvn -q clean test` | exit 0；GET /recommend/songs 返回推荐歌曲，标签过滤、播放热度降序、标题稳定排序、limit 边界正确；RecommendationServiceTest 通过 | 通过 | 最近 `mvn -q clean test` exit 0 |
| V-022 | T-015 | HistoryService/HistoryController：播放历史写入、按用户查询、按用户清空；静态检查与 mvn -q clean test | exit 0；历史接口和测试通过 | 通过 | T-015 静态检查通过；mvn -q clean test exit 0 |
| V-024 | T-014 | PlaylistService/PlaylistController：收藏切换、歌单创建/添加/排序、归属校验；静态检查与 mvn -q clean test | exit 0；收藏歌单能力和测试通过 | 通过 | T-014 静态检查通过；mvn -q clean test exit 0 |
| V-025 | T-016 | NotificationCenter/NotificationController：通知列表、未读计数、已读标记；静态检查与 mvn -q clean test | exit 0；通知能力和测试通过 | 通过 | T-016 静态检查通过；mvn -q clean test exit 0 |
| V-026 | T-017 | UploadService/UploadController：MinIO 分片上传基线、分片状态恢复、READY/SUBMITTED 状态与 UploadServiceTest；执行静态检查和 mvn -q clean test | exit 0；分片上传提交流和测试通过 | 通过 | T-017 upload check passed；mvn -q clean test exit 0 |

| V-029 | T-024 | infrastructure/docker-compose.yml 编排 Nacos、MySQL 8、Redis、RabbitMQ、MinIO，infrastructure/.env.example 外置配置并挂载 database/schema.sql；执行 T-024 静态检查、docker compose -f infrastructure/docker-compose.yml config、mvn -q clean test | 三项命令 exit 0，五项基础设施和外置配置齐全 | 通过 | T-024 static check passed；docker compose config exit 0；mvn clean test exit 0 |

| V-033 | T-025 | 执行 infrastructure/release-drill.py：制品存在性、Compose config、版本快照 manifest、rollback.marker，并核对 RELEASE-DRILL.md 人工验收清单；另执行 mvn clean test | 本地发布候选演练 exit 0；回滚标记 restored=true；真实基础设施联通与人工验收另行执行 | 通过（本地演练） | release rehearsal passed；mvn clean test exit 0；真实 Nacos/MySQL/Redis/MinIO/RabbitMQ 未联通验证，未配置生产凭据 |

## SEQ-001
- 验证同步受理、异步处理完成/失败通知和最终状态回传。

## SEQ-002
- 验证同步受理、异步处理完成/失败通知和最终状态回传。

