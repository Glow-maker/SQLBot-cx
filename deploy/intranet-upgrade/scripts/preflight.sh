#!/bin/sh
# 只读。不会建表、停止服务、导入镜像、显示密码或打印完整Compose/env。
set -eu
fail() { printf '停止升级：%s\n' "$*"; exit 2; }
case "$(uname -m)" in x86_64|amd64) ;; *) fail '仅验证Linux amd64/x86_64';; esac
[ "$(uname -s)" = Linux ] || fail '仅支持Linux现场部署'
for c in docker df java mysql; do command -v "$c" >/dev/null 2>&1 || fail "缺少$c，请使用既有部署主机或授权检查流程"; done
docker info --format '{{.OSType}}/{{.Architecture}}' || fail 'Docker不可用'
docker compose version || fail '请由运维确认现有Compose命令，本脚本使用Docker Compose v2'
: "${DEPLOY_DIR:?设置原SQLBot部署绝对目录}"
: "${COMPOSE_PROJECT:?设置原Compose项目名，不要创建新项目}"
: "${COMPOSE_FILE:?设置原Compose文件绝对路径}"
: "${DATA_JAR:?设置现有data-server JAR绝对路径}"
: "${PLATFORM_STATIC_DIR:?设置现有中台静态目录}"
: "${DATA_SERVICE_UNIT:?设置已核实的data-server systemd单元名；非systemd按教程人工检查}"
case "$DEPLOY_DIR" in /*) ;; *) fail 'DEPLOY_DIR必须绝对路径';; esac
[ -d "$DEPLOY_DIR" ] && [ -f "$COMPOSE_FILE" ] && [ -f "$DATA_JAR" ] && [ -d "$PLATFORM_STATIC_DIR" ] || fail '原部署文件/目录缺失'
free_kb=$(df -Pk "$DEPLOY_DIR" | awk 'END {print $4}')
[ "$free_kb" -ge 12582912 ] || fail '应用目录可用空间不足12GiB；另需数据库备份空间'
docker_root=$(docker info --format '{{.DockerRootDir}}')
if [ -d "$docker_root" ]; then
  docker_free=$(df -Pk "$docker_root" | awk 'END {print $4}')
  [ "$docker_free" -ge 12582912 ] || fail 'Docker数据目录可用空间不足12GiB'
fi
docker compose -p "$COMPOSE_PROJECT" -f "$COMPOSE_FILE" config --services
docker compose -p "$COMPOSE_PROJECT" -f "$COMPOSE_FILE" ps
java -version 2>&1 | head -n 3
systemctl show "$DATA_SERVICE_UNIT" --property=LoadState --property=ActiveState --property=FragmentPath --property=MainPID
printf '%s\n' '由运维在受控终端确认原JVM参数、profile、Nacos地址及启动方式；不要把含凭据的ExecStart/env贴进日志。'
: "${MYSQL_DEFAULTS_FILE:?设置只读检查账号的mysql客户端配置文件绝对路径}"
: "${MYSQL_DATABASE:?设置中台元数据库名，不是被问数业务库}"
[ -f "$MYSQL_DEFAULTS_FILE" ] || fail 'MySQL客户端凭据文件不存在'
mysql_read() { mysql --defaults-extra-file="$MYSQL_DEFAULTS_FILE" --batch --skip-column-names "$MYSQL_DATABASE" -e "$1"; }
mysql_version=$(mysql_read 'SELECT VERSION()')
printf 'MySQL版本：%s\n' "$mysql_version"
case "$mysql_version" in 8.0.*) ;; *) fail '本包仅实测MySQL8.0.39，其他版本须先做兼容验证，不自动升级MySQL';; esac
required=$(mysql_read "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=DATABASE() AND ((table_name='dmgt_namespace' AND column_name IN ('id','tenant_id')) OR (table_name='dmgt_category' AND column_name IN ('id','namespace_id','status','approval_status','mount_status')) OR (table_name='dmgt_table' AND column_name IN ('id','category_id','data_source_id','table_name')) OR (table_name='dmgt_table_field' AND column_name IN ('id','table_id','name','alias','type','remark','status','content_visible')))")
[ "$required" = 19 ] || fail '不符合六月元数据结构；交由部署方比对，不运行历史DDL'
if [ -n "${PG_CONTAINER:-}" ]; then
  : "${PG_USER:?设置PG只读检查用户}"; : "${PG_DATABASE:?设置SQLBot PG库名}"
  pg_read() { docker exec "$PG_CONTAINER" psql -X -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$PG_DATABASE" -Atc "$1"; }
else
  : "${PGSERVICE:?设置已有libpq service名，凭据使用现场.pgpass/PGPASSFILE}"
  command -v psql >/dev/null 2>&1 || fail '缺少psql，改用PG_CONTAINER或既有授权检查流程'
  export PGSERVICE
  pg_read() { psql -X -v ON_ERROR_STOP=1 -Atc "$1"; }
fi
pg_version=$(pg_read 'SHOW server_version_num')
printf 'PG server_version_num：%s\n' "$pg_version"
case "$pg_version" in 17????) ;; *) fail '本包隔离集成仅验证PG17，其他版本须先验证，不替换数据库镜像';; esac
revision=$(pg_read 'SELECT version_num FROM alembic_version')
printf 'Alembic：%s\n' "$revision"
case "$revision" in 8ff90df7871d|a6e066b1c001) ;; *) fail '迁移起点不在已验证范围，先审核实际迁移链';; esac
vector=$(pg_read "SELECT count(*) FROM pg_available_extensions WHERE name='vector'")
[ "$vector" = 1 ] || fail '现有PG没有pgvector扩展文件，请走授权安装流程'
pg_read "SELECT 'vector installed='||coalesce((SELECT extversion FROM pg_extension WHERE extname='vector'),'not installed'); SELECT 'schema-create='||has_schema_privilege(current_user,'public','CREATE')::text;"
printf '%s\n' '只读预检完成。仍须确认备份、服务账号DDL权限、旧镜像ID和真实认证链路；本结果不是上线验收。'
