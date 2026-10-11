# jt/dev-1 真实问数复验

本轮不需要新增或修改业务表；原完整 Alembic、独立 MySQL 初始化和只补缺表的备用脚本保持不变。只新增八院专用受限账号 sqlbot4，只读登记源 1 的 orders；原成本账号与 sqlbot1–3 授权不变。凭据保存在操作机账号说明和服务器受保护审计目录，不进仓库。

运行入口为 20170，代码在 /data/code/jt/SQLBot-cx，运行配置在 /data/service/dev-1/sqlbot。审计目录为 /data/code/jt/deployment-audit/20260921-sqlbot-acceptance。

## 重复验证

在 host2 运行当前目录中的 verify-real-questions.py --batch（10 题各 3 次），随后运行 verify-query-security.py。问答脚本只使用两个虚构测试源；保留原失败记录。

用 collect-query-audit.py 从容器只读导出指定 SQLBOT_ACCEPT_RECORD_IDS 的日志身份与耗时。summarize-query-acceptance.py --min-record <本轮第一条记录> 核对最近 30 个固定问题的实际结果、权限候选、执行入口和 P95；该脚本不会把 finish 事件当作结果正确。

撤权使用 verify-query-revocation.py before/revoked/restored 三个步骤。仅在中间临时变更 dataease_dev1.dmgt_user_role 的 id=12361、user_id=361、role_id=12、tenant_id=124 这一条专用测试授权的 deleted 值，必须在 finally 中恢复 0。脚本复用同一令牌，检查目录、旧结果、日志、导出和续问；restored 清理临时令牌文件。不要操作其他账号授权。

隔离自动化覆盖权限 HTTP 超时/503/无效 JSON/错误身份、第二次校验撤权、绝对截止时间、CTE/子查询/UNION/写入拒绝、索引单表更新/删除/去重/恢复。此类故障注入只在隔离测试执行，不重启共享服务模拟故障。

## 发布与回退

发布只更新变更的 Data JAR、SQLBot 后端/前端版本化镜像。中台前端、Cost、Gateway、System、Infra 和公共服务不变。部署前必须比对源码 SHA、镜像 ID、JAR 和静态入口哈希；不能把本地开发环境文件打进产物。

需要回退时先停用 SQLBot 两个容器，防止旧 Data 的宽目录接口再次对外提供问数。恢复审计目录的 data-before.jar 到 dev-1 Data 当前 JAR，仅重启 dev-1-yudao-data；恢复所选备份镜像标签及 backend-before.env/compose-before.yml。旧版本存在本轮修复的权限缺陷，未重新完成权限复验前保持问数停用。保留独立库、索引、配置和证据，不删库、不降级 Alembic、不重启公共数据库。

准确发布 SHA、产物哈希、最终测试数量与页面截图记录在本轮验收报告及 release-manifest.json；早期失败结果不能当作最终通过结果。
