# SQLBot 双形象动态助手

## 当前实现

问答页默认显示已确认的圆顶 Rocket，可在右上角“助手外观”切换 Bloub 或关闭动态效果。偏好仅保存在当前浏览器的 `sqlbot.assistant-appearance.v1`，不发送到后端。

欢迎角色为 112px（窄屏 88px），跟随问答区域内的鼠标；回答头像保持 28px，仅当前请求运行动画。历史头像静止。机身使用 SQLBot 的 `--ed-color-primary`，独立预览回退到 `--el-color-primary`。火箭使用独立二维眼睛，保持安全边界，尾焰为淡珊瑚红。

Bloub 复用了上游 `face.ts`、`math.ts` 的面部投影和眨眼核心，配以 SQLBot 自己的业务状态渲染器。没有迁移演示站的自动轮播、编辑器或导出依赖。上游版本和完整 MIT 声明随静态包保存在 `licenses/bloub.txt`。

## 状态契约

2026-09-08 UI 升级：预览改为主体验区与设置侧栏，问答页增加仅填入草稿的问法模板，欢迎角色支持点击回应与输入聚焦反馈。详见 [UI_UPGRADE.md](./UI_UPGRADE.md)，包含本轮验证结果和仍未完成的真实环境验收。

待机新增“自动换动作 / 安静呼吸 / 弹一弹 / 摇一摇 / 转一圈 / 轻轻飘”，在助手外观中选择并保存在本浏览器。自动模式初始等待 2 秒，之后每 9 秒依次触发一个约 2 秒短动作，转圈每 36 秒一次。预览页动作按钮支持立即试播和重播。只对大尺寸待机角色生效，28px 回答头像及业务处理中不播放这些动作。采用整体刚性平移/旋转，不缩放或挤压眼睛；动作中眼睛平滑回正，动作后恢复指针跟随。切换动作由当前位姿弹簧衔接，隐藏或关闭动态效果后停止。

`AssistantAvatar` 接收 `variant / state / size / animated / followPointer`。回答组件发送 `avatarState` 前端事件：`{chatId, recordIndex, requestId, state}`，不修改后端 SSE/HTTP 接口。

| 状态 | 来源 |
| --- | --- |
| thinking | 当前回答组件开始请求 |
| responding | SQL、图表、分析或预测的实际内容；不包含隐藏的推理文本 |
| success | finish / analysis_finish / predict-success；仅反馈 800ms |
| error | 错误事件、权限拒绝、跨源拦截、HTTP 业务码或传输异常 |
| stopped | 主动停止，或流结束但未收到明确完成标记 |
| idle | 欢迎页、历史头像、完成反馈结束 |

停止立即 abort；请求和记录、会话绑定，切换会话时取消旧请求，迟到事件不会修改新会话。已有异常提示和权限申请流程继续使用原组件。

默认开启动效，不跟随系统减少动态效果设置；用户可通过“动态效果”开关关闭，保留当前浏览器已保存的选择。页面隐藏、角色不在视口或关闭动态效果时停止 rAF；卸载清理监听器、观察器及定时器。

## 本地启动与验证

在 `frontend` 中运行：

```powershell
npm run test:assistant
npm run build
npm run dev -- --host 127.0.0.1 --port 5186 --strictPort
```

原项目 Vite 配置读取环境变量，预览地址随 `VITE_PUBLIC_BASE_URL` 变化。本轮开发预览以 `/` 启动，地址为 `http://127.0.0.1:5186/assistant-preview.html`。按 Ctrl+C 停止。预览独立于登录和后端，只展示状态，不查询业务数据。

2026-09-08 验证：

- 13 项测试通过：待机动作边界和转圈方向、坐标与边界、状态优先级、真实三个回答组件的流式夹具、权限/网络失败、截断、停止、会话切换、重复挂载清理。
- Vue 类型检查和 `/sqlbot-app/` 生产构建通过。构建仍有原项目的大分包、VueUse 注释和 public-path URL 警告。
- 浏览器实操确认四向跟随、离开回正、800ms 完成后静止、外观切换和刷新保存、关闭动画、减少动态效果及 390px 视口下 88px 角色、无横向溢出。
- 待机扩展后浏览器采样确认完整 360° 转圈后停稳、轻弹、切换业务状态回正；关闭动态效果时所有整体变换清除且动画循环数为 0。
- 生命周期测试用受控 rAF、EventTarget 和观察器验证连续 5 次挂载卸载无残留。业务组件测试使用模拟流，不等于真实业务数据库验收。
- 实际本地问答路由进入登录页；中台入口访问超时，未完成真实登录后的独立/qiankun 问数验收。
- 现有 SSH 别名连接 `sht808@100.78.50.94` 返回 `Permission denied (publickey,password)`，本轮未发布或重启服务器，未取得旧前端镜像 ID。

## 发布与回滚

静态包按 `VITE_PUBLIC_BASE_URL=/sqlbot-app/`、`VITE_API_BASE_URL=/api/v1` 构建。仅部署前端；本工作区存在另一个任务的索引后端改动，不能将整个未提交工作区直接打包发布。

获得 SSH 访问后，先核对服务器当前前端代码、同源路径及运行镜像。将当前 `sqlbot-frontend` 的实际镜像 ID 保存并打唯一回滚标签，再将本次前端提交合入服务器对应工作区。先构建新镜像，再使用以下命令只重建前端：

```sh
# 在服务器 SQLBot 仓库中；此前必须先保存旧镜像 ID 与回滚标签。
docker build --network=host -f deploy/Dockerfile.frontend \
  --build-arg VITE_PUBLIC_BASE_URL=/sqlbot-app/ \
  --build-arg VITE_API_BASE_URL=/api/v1 \
  --build-arg VITE_PLATFORM_LOGICAL_DATASOURCE_ENABLED=true \
  --build-arg VITE_SHOW_REASONING=true \
  -t sqlbot-cx-frontend:latest .
docker compose -f deploy/docker-compose.host.yml up -d --no-deps --force-recreate sqlbot-frontend
sh deploy/verify-frontend.sh
```

随后在中台 `/sqlbot/chat/index` 登录验证欢迎页、当前/历史头像、问数/分析/预测/重试/停止，以及反复进入退出页面。只把完成这些检查的版本认定为已联调。

回滚：把已保存的旧前端镜像 ID 重新标记为 `sqlbot-cx-frontend:latest`，执行同一个 `up -d --no-deps --force-recreate sqlbot-frontend`。后端镜像和数据不参与本次前端回滚。
