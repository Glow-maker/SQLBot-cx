# SQLBot Docker Host 部署说明

本部署方案用于 Linux 服务器，容器使用 `network_mode: host`。因此容器内访问 `127.0.0.1:5432`、`127.0.0.1:48080`、`127.0.0.1:6379` 等同于访问服务器本机服务。

## 1. 修改配置

先修改 `deploy/sqlbot-backend.env`：

- `SECRET_KEY`：换成固定长随机字符串。
- `FRONTEND_HOST`：改成 `http://服务器IP:5173`。
- `BACKEND_CORS_ORIGINS`：填 SQLBot 前端地址和数据中台前端地址。
- `POSTGRES_USER` / `POSTGRES_PASSWORD`：填服务器 PostgreSQL 账号密码。
- `SERVER_IMAGE_HOST`：改成 `http://服务器IP:5173/images/`。

## 2. 可选：拷贝本地 embedding 模型

如果构建机不能拉取 `ghcr.io/1panel-dev/maxkb-vector-model:v1.0.1`，或希望强制使用本地模型：

```powershell
cd H:\light\project\SQLBot-cx
mkdir deploy\models
robocopy H:\opt\sqlbot\models deploy\models /E
```

镜像内最终路径应包含：

```text
/opt/sqlbot/models/embedding/shibing624_text2vec-base-chinese
```

## 3. 构建镜像

Linux 服务器上推荐直接使用脚本：

```bash
cd /opt/sqlbot/sqlbot

# 全量构建 backend + frontend，并启动两个容器
sh deploy/build-and-up.sh

# 2.0 同源代理部署：前端构建使用 /sqlbot/ 和 /api/v1 相对路径
sh deploy/build-and-up-same-origin.sh

# 后续只改 backend 时，只重打后端并重启 sqlbot-backend
sh deploy/build-backend-only.sh
```

脚本默认：

- 使用 `latest` 镜像标签。
- 如果 `deploy/models` 下存在本地模型文件，自动使用 `deploy/Dockerfile.runtime-local-model`。
- 否则使用根目录 `Dockerfile` 拉取远程模型镜像。
- 前端默认使用 `VITE_API_BASE_URL=/api/v1`，避免服务器 IP 变化后重打前端镜像。
- 构建时默认把 PyPI 源替换为 `https://mirrors.aliyun.com/pypi/simple`。

如需手工指定：

```bash
SQLBOT_RUNTIME_DOCKERFILE=deploy/Dockerfile.runtime-local-model \
SQLBOT_PYPI_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
VITE_API_BASE_URL=/api/v1 \
VITE_PUBLIC_BASE_URL=http://服务器IP:5173/sqlbot/ \
sh deploy/build-and-up.sh
```

下面是手动构建方式。

同源代理模式的 nginx 配置见：

```text
deploy/README.same-origin.md
deploy/nginx-sqlbot-same-origin.locations.conf
```

PowerShell：

```powershell
cd H:\light\project\SQLBot-cx
$env:DOCKER_BUILDKIT=1

docker build -t sqlbot-cx-runtime:latest .

docker build -t sqlbot-cx-backend-host:latest `
  -f deploy/Dockerfile.backend-host .

docker build -t sqlbot-cx-frontend:latest `
  -f deploy/Dockerfile.frontend `
  --build-arg VITE_API_BASE_URL=/api/v1 `
  --build-arg VITE_PUBLIC_BASE_URL=http://服务器IP:5173/sqlbot/ `
  --build-arg VITE_PLATFORM_LOGICAL_DATASOURCE_ENABLED=true `
  --build-arg VITE_SHOW_REASONING=false `
  .
```

如果不能拉取 `ghcr.io/1panel-dev/maxkb-vector-model:v1.0.1`，先按第 2 步准备 `deploy\models`，然后把 runtime 构建命令替换为：

```powershell
docker build -t sqlbot-cx-runtime:latest -f deploy/Dockerfile.runtime-local-model .
```

Linux：

```bash
cd /path/to/SQLBot-cx
export DOCKER_BUILDKIT=1

docker build -t sqlbot-cx-runtime:latest .
docker build -t sqlbot-cx-backend-host:latest -f deploy/Dockerfile.backend-host .
docker build -t sqlbot-cx-frontend:latest \
  -f deploy/Dockerfile.frontend \
  --build-arg VITE_API_BASE_URL=/api/v1 \
  --build-arg VITE_PUBLIC_BASE_URL=http://服务器IP:5173/sqlbot/ \
  --build-arg VITE_PLATFORM_LOGICAL_DATASOURCE_ENABLED=true \
  --build-arg VITE_SHOW_REASONING=false \
  .
```

如果不能拉取 `ghcr.io/1panel-dev/maxkb-vector-model:v1.0.1`，先按第 2 步准备 `deploy/models`，然后把 runtime 构建命令替换为：

```bash
docker build -t sqlbot-cx-runtime:latest -f deploy/Dockerfile.runtime-local-model .
```

## 4. 打部署包

```bash
docker save sqlbot-cx-backend-host:latest sqlbot-cx-frontend:latest \
  -o sqlbot-cx-images-latest.tar

tar -czf sqlbot-cx-deploy-latest.tar.gz \
  sqlbot-cx-images-latest.tar \
  deploy/docker-compose.host.yml \
  deploy/sqlbot-backend.env
```

## 5. 服务器准备

PostgreSQL 中先准备数据库和 pgvector：

```sql
CREATE DATABASE sqlbot;
\c sqlbot
CREATE EXTENSION IF NOT EXISTS vector;
```

## 6. 服务器启动

```bash
tar -xzf sqlbot-cx-deploy-latest.tar.gz
docker load -i sqlbot-cx-images-latest.tar

cd deploy
mkdir -p data/sqlbot/excel data/sqlbot/file data/sqlbot/images data/sqlbot/logs
docker compose -f docker-compose.host.yml up -d
```

查看日志：

```bash
docker logs -f sqlbot-backend
docker logs -f sqlbot-frontend
```

## 7. 验证

服务器本机执行：

```bash
curl http://127.0.0.1:8011/openapi.json
curl http://127.0.0.1:5173/sqlbot/
curl http://127.0.0.1:5173/api/v1/openapi.json
curl "http://127.0.0.1:48080/admin-api/system/tenant/get-id-by-name?name=八院"
```

校验前端静态包是否已经切到当前镜像：

```bash
sh deploy/verify-frontend.sh
```

如果需要从其他机器校验服务器地址：

```bash
SQLBOT_FRONTEND_URL=http://服务器IP:5173/sqlbot/ sh deploy/verify-frontend.sh
```

如果只改了前端代码，不需要重建 runtime/backend，可以只重建并重启前端：

```bash
VITE_PUBLIC_BASE_URL=http://服务器IP:5173/sqlbot/ sh deploy/build-frontend-only.sh
```

浏览器访问：

```text
http://服务器IP:5173/sqlbot/chat/index
```

数据中台前端配置 SQLBot 微应用入口：

```text
http://服务器IP:5173/sqlbot/
```
