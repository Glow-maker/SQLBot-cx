# jt/dev-1 联合部署

成本树保留自己的库、权限和服务。SQLBot 只登记专用虚构客户/订单数据，不能登记成本库。

## 目录与依赖

联合业务代码统一使用以下目录与分支；成本树与 SQLBot 的中台改造已经合入，不能再从合并前的成本代码发布 Data。

| 代码 | 服务器目录 | 远端分支 |
| --- | --- | --- |
| 中台后端（含 Cost、Data） | `/data/code/jt/bcback` | `feature/costree` |
| 中台前端 | `/data/code/jt/academy8-operation-control-center-frontend` | `feature/costree2` |
| SQLBot 前后端 | `/data/code/jt/SQLBot-cx` | `feature/sqlbot` |

日常发布已构建制品使用 `/data/scripts/jt/deploy.sh <包目录> <组件>`，不要在未核对分支和本地修改时直接重新打包覆盖。服务器运行凭据继续外置；Git 同步不等于服务发布。

- 代码：`/data/code/jt/`；产物：`/data/service/dev-1/`。
- 公共数据库位于 base，使用现有 PostgreSQL 17/pgvector 与 MySQL 实例，不创建数据库容器。
- 独立库：`sqlbot_dev1`（完整 Alembic 链）、`dataease_dev1`（Data 元数据）、`sqlbot_demo_dev1` / `sqlbot_demo_alt_dev1`（虚构只读查询数据）。
- 凭据仅存在服务器受保护 env/audit 文件与本机账号说明中，不进入版本库。

## 初始化顺序

1. 在 base 执行 `bootstrap-base.py`：预检目标库和账号不存在，生成随机凭据，创建独立库、结构及虚构数据。脚本拒绝覆盖已有环境；中途失败先审查现状，不能删除凭据强行重跑。
2. `CreateSqlbotAccounts.java` 使用现有 System DM 身份体系创建三个专用测试身份；重名时停止，不重置密码。保存私有账号文件。
3. 在 base 执行 `seed-metadata.py <readerUserId> <indexerUserId>`，仅向空的 dataease_dev1 写入专用元数据与权限。
4. 在 host2 执行 `prepare-runtime.py` 生成外置环境文件；此脚本读取本次审计目录保存的原连接配置，不能移作通用环境初始化。
5. 使用版本化镜像；先执行 `preflight.sh`。将 compose.yml、frontend.conf 放入 `/data/service/dev-1/sqlbot/`，.env 设置两个镜像版本。`docker compose --project-name dev-1-sqlbot up -d`。
6. 后端首次启动执行完整 Alembic upgrade head。备用结构 SQL 仅补建结构，不写 alembic_version，不代表迁移完成；已有数据环境不得直接覆盖或降级。
7. Data 使用 dataease_dev1；共享库全量刷新与新索引定时调度均保持关闭。仅升级 dev-1 Data，不重启公共服务。
8. 执行 `python3 deploy/dev-1/install-nginx.py`，只向共享配置 `/data/deploy/nginx/conf.d/default.conf` 的 20170 server 补入 SQLBot 路由。脚本核对容器挂载、备份、校验后平滑 reload；已有完整规则时不重复添加，部分规则冲突时停止。旧 `/data/code/jt/deploy/nginx/conf.d/` 当前未挂载，不是生效配置。中台前端 scripts/dev-1/deploy.sh 保留旧哈希资源并最后替换入口。

## 监听与入口

| 服务 | 监听 |
| --- | --- |
| API | 172.17.0.1:20186 |
| 图片 | 172.17.0.1:20187 |
| SQLBot 静态资源 | 172.17.0.1:20188 |
| 内部图表渲染 | 127.0.0.1:20189 |

唯一浏览器入口为 `http://8.130.92.192:20170`。主应用 `/sqlbot/chat/index`，子应用 `/sqlbot-app/`，API `/api/v1/`，图片 `/images/`。API 代理关闭 SSE 缓冲和缓存。

## 验证与模型配置

`verify-api.py` 校验三个专用用户登录、表权限和同步配置权限；`verify-table-identity.py` 校验表 ID、名称、schema、数据源一致性。脚本不输出令牌或数据源连接密码。

Embedding 随镜像离线提供；生成模型本轮不预填。平台管理员从智能问数内的模型配置入口配置用户部署的服务，再验收真实问题生成 SQL、执行结果和图表、跨源拒绝、无权请求未执行查询、进度成功收起和失败保留。基础链路成功不能代替真实问数验收。

## 回退

1. 保持新库索引调度 disabled，停止 dev-1-sqlbot 项目两个容器。
2. 恢复本次审计目录中 data-before.jar 和 data-service.env，仅重启 dev-1-yudao-data。
3. 从前端部署产生的 backup-* 恢复原 index.html 和静态资源；恢复本次 nginx-before.conf，nginx -t 后平滑 reload。
4. 保留新数据库、账号、持久化目录供排查，不删库，不运行 Alembic downgrade，不重启公共数据库/Redis/Nacos。

准确提交、产物哈希、测试结果与待验事项见本次发布清单；上述操作说明不是成功证明。

## 完整与备用脚本

- 完整初始化：`bootstrap-base.py` + 固定版本 `sql/schema-mysql.sql` + `seed-metadata.py` + 当前镜像的完整 Alembic 链。MySQL 结构来自此次已核对的 23 张表快照，不执行历史整份 Data DDL，也不导入共享调度数据。
- MySQL 备用：`sql/backup-mysql-create-missing.sql`，只在 dataease_dev1 补建缺失表。
- PostgreSQL 备用：`sql/backup-postgresql-create-missing.sql`，要求已启用 vector；只补建缺失的 32 张应用表及所需主键、序列、索引，不改现存表、不写版本号、不补初始业务配置。
- 备用不是空库初始化的替代品：Alembic 还包含初始配置与数据迁移。禁止用 stamp 掩盖未执行的迁移。
- 两份备用脚本已在独立容器验证重复执行；PG 另验证删除单张测试表后能够补回。
