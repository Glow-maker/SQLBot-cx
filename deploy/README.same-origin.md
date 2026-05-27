# SQLBot 同源代理部署

## 结论

- 一键部署方式和以前一样，执行 `sh deploy/build-and-up-same-origin.sh`。
- 这个脚本会调用原来的 `build-and-up.sh`，只是默认补上同源前端参数。
- 只做同源访问、nginx 代理、前端入口调整时，不需要修改 runtime，也不需要修改后端业务代码。
- 如果想最省事，可以继续用一键脚本完整构建；如果只改前端，可以只重建 frontend。

## 什么时候重建

| 场景 | 操作 |
| --- | --- |
| 省事完整部署 | `sh deploy/build-and-up-same-origin.sh` |
| 只改前端或前端构建参数 | 只重建 `sqlbot-cx-frontend`，重启 `sqlbot-frontend` |
| 改后端代码或后端 env | 重建/重启 backend |
| 改 Python 依赖、基础镜像、模型目录 | 重建 runtime，再重建 backend |
| 只改数据中台 nginx 代理 | 只 reload 数据中台 nginx |

## 一键部署

```bash
cd /opt/sqlbot/sqlbot
git pull
sh deploy/build-and-up-same-origin.sh
```

默认前端参数：

```text
VITE_PUBLIC_BASE_URL=/sqlbot/
VITE_API_BASE_URL=/api/v1
```

## 数据中台 nginx

把 `deploy/nginx-sqlbot-same-origin.locations.conf` 合并到数据中台前端 nginx 的 `server` 块，然后执行：

```bash
nginx -t
nginx -s reload
```

代理关系：

```text
/sqlbot/ -> http://127.0.0.1:5173/sqlbot/
/api/v1/ -> http://127.0.0.1:8011/api/v1/
/images/ -> http://127.0.0.1:8012/images/
```

## 数据中台前端入口

```env
VITE_SQLBOT_ENTRY=/sqlbot/
```

## 验证

```bash
curl -I http://127.0.0.1:5173/sqlbot/
curl -I http://127.0.0.1/sqlbot/
```

浏览器访问：

```text
http://服务器IP/sqlbot/chat/index
```

Network 里应看到：

```text
/sqlbot/assets/xxx.js
/api/v1/xxx
```
