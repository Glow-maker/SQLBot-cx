# Phase 切换草案 · commit 1c7ad14 + 0eba692（verify 脚本 + 启动脚手架）

> 本文件是 §三 Phase 切换 Prompt 的输出草案，待用户拍板后再落到 `docs/integration/`。
> 生成时间：2026-05-07
> 覆盖 commit：`1c7ad14`（Phase 2a/3a Mock 自验脚本 + start.ps1）、`0eba692`（主线一 verify + 报告三栏格式化）
> 性质：二者同属"契约自验体系建设"，合并为一次 Phase 切换处理

---

## 0. 实际改动（`git show --stat` 归纳）

`1c7ad14`：

```
backend/scripts/mock_platform.py     | +221
backend/scripts/verify_phase2a_3a.py | +320
backend/start.ps1                    |  +11
```

`0eba692`：

```
backend/scripts/mock_platform.py     | +8     新增 /admin-api/system/tenant/get-id-by-name
backend/scripts/verify_phase2a_3a.py | +504/-127（重写为三栏结构 + 自动 md 报告）
backend/scripts/verify_phase_auth.py | +528   新增主线一 42 项自验
```

**只动了 `backend/scripts/` 和新增一个 `backend/start.ps1`，apps/ 与 common/ 生产代码一行未动。**

---

## A. 需要新建的文件

| 文件 | 一句概述 | 是否必要 |
|---|---|---|
| `docs/integration/变更/Phase-契约自验与启动脚手架.md` | 记录本次改动"做了什么 + 为什么"：为什么引入 mock_platform / 为什么 verify 改三栏 / start.ps1 的两个 env 变量意义 | **建议新建**。与已有的 `Phase2a-3a-Mock验证报告.md`（测了什么）是姐妹文档，职责不重叠 |

理由：`变更/` 目录下目前只有"各 Phase 代码说明"和"mock 报告"两类。本次产物是基础设施（脚手架 + 验证框架），属于前者的空白位。不新建则未来维护者无法从文档里理解 `start.ps1` 为什么那样写、mock 为什么用 query string 切模式。

**[需确认]** 文件名建议 `Phase-契约自验与启动脚手架.md`（无 Phase 数字，因为这是横切关注点）。另一方案：塞进现有 `Phase2a-3a-Mock验证报告.md` 末尾作为"附录：验证基础设施"。我倾向前者。

---

## B. 需要 diff 的文件

### B.1 `docs/integration/项目状态与待办.md`

**§二 已完成清单（第 33-39 行附近）** —— 新增一行：

| 位置 | 旧 | 新 |
|---|---|---|
| 表格末尾新增第 6 行 | — | `| 6 | \`1c7ad14\`, \`0eba692\` | **验证体系** | Mock 中台 + Phase 2a/3a 自验 24/24 + 主线一自验 42/42 + start.ps1 启动脚手架 |` |

**§八 版本历史（第 150-154 行附近）** —— 新增两行（按时间倒序）：

| 位置 | 旧 | 新 |
|---|---|---|
| 插在"Phase 3a"行之前 | — | `| 2026-05-07 | 验证体系 | 主线一自验 42 项 + Phase 2a/3a 三栏报告 + 中台需开发内容附录（commit \`0eba692\`） |` |
| 接上一行之后 | — | `| 2026-05-07 | 验证体系 | Mock 中台 + Phase 2a/3a 自验 24 项 + start.ps1 启动脚手架（commit \`1c7ad14\`） |` |

**§一 总览表（第 19-27 行附近）** —— "Mock 自验" 行已是 ✅，但可补一句"主线一 42/42"：

| 位置 | 旧 | 新 |
|---|---|---|
| 第 26 行 | `| Mock 自验 | ✅ Phase 2a/3a 24/24 通过（2026-05-07） |` | `| Mock 自验 | ✅ Phase 2a/3a 24/24 + 主线一 42/42（2026-05-07） |` |

**§四 P0 待办 —— 不动**。本次没解决 P0-1/P0-2/P0-3/P0-4/P0-5 任何一项（它们都等外部）。但可将 P0-3 的"备注"列加一句"mock 报告 + 主线一报告已就绪，见变更/"，避免下次有人忘了材料在哪。

| 位置 | 旧 | 新 |
|---|---|---|
| §四 P0 表第 P0-3 行 备注列 | `一次会议拉齐；mock 验证已暴露 5 个待对齐点` | `一次会议拉齐；mock 验证已暴露 5 个待对齐点（材料见 变更/Phase2a-3a-Mock验证报告.md 与 Phase-主线一-验证报告.md §五）` |

**§三 流程图完成度 —— 不动**。本次未改流程节点。

### B.2 `docs/integration/用户手册.md`

**第 234 行附近（故障排查节）** —— 已提到 `start.ps1` 和 HF 离线两个 env，内容正确不必改。

**新增一小节：§X.Y 自验脚本（位置建议在"运维排障"章节之后、"开发者"章节之前）**：

```markdown
### X.Y 契约自验脚本（运维 / 开发自查用）

不连真 DB、不调 LLM 即可验证 SQLBot 侧改造是否回归。分两组：

- **主线一（token 访问）**：`python backend/scripts/verify_phase_auth.py` → 42 项
- **主线二（数据源 + 权限）**：先起 `python backend/scripts/mock_platform.py`，再 `python backend/scripts/verify_phase2a_3a.py` → 24 项

报告自动生成到 `docs/integration/变更/Phase-主线一-验证报告.md` 和 `Phase2a-3a-Mock验证报告.md`。
三栏格式：传入 / 期望 / 实际，出差异一眼可见。
```

**[需确认]** 具体插哪一节，取决于用户手册现有章节层级。我没读全文不敢乱插，用户指一下位置即可。

### B.3 `docs/integration/配置手册-环境变量.md`

检查结果：`PLATFORM_DATASOURCE_*` / `PLATFORM_PERMISSION_*` 都已登记（第 121-132 行），本次未新增 `.env` 字段。

但 `start.ps1` 注入了 `HF_HUB_OFFLINE` / `TRANSFORMERS_OFFLINE` / `HF_HOME` 三个环境变量，属于"**不在 `.env` 里但影响启动行为**"的隐藏变量，建议补一小节：

| 位置 | 旧 | 新 |
|---|---|---|
| 文末或"其它"节新增 | — | `## 附：启动期 env（不在 .env 中，由 start.ps1 注入）` + HF_HUB_OFFLINE=1 / TRANSFORMERS_OFFLINE=1 / HF_HOME 三条说明 |

### B.4 `docs/integration/踩坑记录.md`

已记录"PG localhost IPv6 超时"与"less 4.6.4 不兼容"。本次新增的踩坑点有两个：

- mock 用 `os.environ` 切模式在子进程不生效 → 改 query string（可补）
- xpack 循环依赖导致 verify 脚本 ImportError → 复刻 main.py import 顺序（可补）

两条都是"只有自验基础设施维护者会遇到"的冷门问题，**[需确认]** 是否值得写入公开踩坑记录。建议写入，毕竟以后改 verify 脚本还是会撞。

---

## C. 不需要动的检查项（已核对，避免漏检）

| 检查项 | 结论 |
|---|---|
| 是否新增 `.env` 配置？ | **否**。`PLATFORM_*` 在 Phase 1 已登记齐 |
| 是否改了 SSE 事件类型？ | **否**。`datasource_not_found` / `permission_denied` 维持 Phase 2a/3a 现状 |
| 是否改了 chat 主流程行为？ | **否**。仅加测试脚本 |
| 是否动了流程图节点？ | **否**。看板 §三 无需改 |
| 是否影响前端？ | **否**。前端契约无变化 |
| 是否影响中台接口协议？ | **否**。协议仍以 Phase2a-3a-Mock验证报告 §五 为准 |
| 是否动了生产代码？ | **否**。`backend/apps/` / `backend/common/` 一行未改 |
| 是否需要新增 Phase 编号？ | **否**。这是跨 Phase 的验证体系，不占 Phase 数字位（所以文件名建议 `Phase-契约自验与启动脚手架.md` 不带数字） |

---

## D. 额外提醒（不属于 Phase 切换 prompt 的输出范围，但值得说）

1. `docs/integration/` 整目录 gitignored（`485c503`），所以本次 §B 的 diff **即使写了也不会进 git**。本次 commit 的命题就是"代码进 git / 文档本地维护"。
2. 本草案写在 `docs/summary/`，该目录**未 gitignored**。要不要让 summary 进 git，下一步要决定。
3. §A 建议新建的 `Phase-契约自验与启动脚手架.md` 会落到 `docs/integration/变更/`，**仍 gitignored**，只本地存。

---

## 用户待拍板事项

- [ ] §A 新建 `变更/Phase-契约自验与启动脚手架.md`？文件名认可吗？还是并入 mock 报告附录？
- [ ] §B.2 用户手册插入位置（用户指一下章节号）
- [ ] §B.3 配置手册"启动期 env"小节内容是否按上面三条写
- [ ] §B.4 两条冷门踩坑点是否写入踩坑记录
- [ ] `docs/summary/` 是否应也加入 `.gitignore`，或者相反——哪些 summary 允许进 git
