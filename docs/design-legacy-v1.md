# WorkTogether Web APP 设计决策说明书 (DESIGN.md)

- **版本**: v1.0.0
- **目标**: 交付可实际操作的本地网页工作台 (Python HTTP + React/TS 网页前端)，支持项目与任务管理、单步调度、运行记录、产物查看与手动交接。

---

## 一、 核心架构原则

1. **权威数据源单一化**：
   - 后端 Python (`worktogether.core.Engine` + `worktogether.runtime.Runtime`) 和 SQLite 是唯一的真理源与状态机。
   - 前端不复制状态流转逻辑，所有按钮点击均触发后端对应 API 调用，并通过轮询（活跃期 2s，空闲期 5s，用户操作后立即刷新）同步权威状态。
2. **零外部重型依赖与本地优先**：
   - 服务端使用 Python 标准库 `http.server.ThreadingHTTPServer`，多线程非阻塞响应，支持并发长耗时后台任务。
   - 本地服务默认仅监听 `127.0.0.1`。
3. **安全性与沙箱隔离**：
   - **防路径穿越**：日志和产物查看（`/api/runs/{id}/artifacts/{name}`）严格限定在服务管理的 `runs/{id}/` 目录白名单文件名，任何 `..` 或越界路径直接 403 拦截。
   - **防 CSRF 篡改**：只允许来自 `127.0.0.1` 或 `localhost` 的同源写请求。
   - **绝无任意命令执行**：后端不设通用 shell 执行接口；Codex 运行严格通过 `Runtime.run` 派发沙箱进程。
   - **凭据零泄露**：不向前端下发任何系统/服务账号私钥或 Token。
4. **诚实的可观测性与用量展示**：
   - 缺失数据显示“暂无数据”，严禁伪造数字或盲目将累计 Token 与单次 Token 相加。
   - 模拟执行任务（Demo）在界面醒目标记 `[SIMULATED]`。

---

## 二、 界面布局与交互设计 (UI / UX)

采用经典、高效的**双栏工作台布局**，专为专业开发者设计，中文原生文案：

```text
+-----------------------------------------------------------------------------------+
|  WorkTogether 本地协同工作台                   [项目: 我的项目 (git: clean)]  [状态]  |
+----------------------+------------------------------------------------------------+
| 导航侧边栏 (280px)   | 任务主舞台 (Main Stage)                                    |
|                      |                                                            |
| [项目列表]           | [任务标题] 优化视频进度持久化       [ACTIVE] [PLANNING]    |
| + 添加本地项目       | 需求描述: 刷新页面后自动恢复播放位置...                    |
| > 当前项目           |                                                            |
|                      | [操作栏] [派发下一步] [运行 Codex] [暂停] [导入报告]       |
| [任务列表]           |                                                            |
| + 新建任务           | [标签页]                                                   |
| - 视频进度优化 (RUN) |   [1. 协同时间线]   [2. 步骤与验收]   [3. 执行与用量]      |
| - 样式重构 (DONE)    |                                                            |
| - 缓存穿透修复 (BLK) | 时间线:                                                    |
|                      |  Codex 提交 PLAN (v1) -> Antigravity 审查通过 -> ...       |
|                      |                                                            |
|                      | 证据与日志查看 (默认折叠长文本，支持格式化高亮)            |
+----------------------+------------------------------------------------------------+
```

### 1. 侧边栏 (Sidebar)
- **项目管理**：展示项目名称、本地绝对路径、关联任务总数、Git 状态快速提示；支持快速输入路径添加本地项目。
- **任务切换**：按项目严格筛选任务列表；每个任务项显示标题、当前 phase/status 状态胶囊、阻塞原因（若有）；切换项目时自动重置选中任务，绝不串线。

### 2. 主舞台 (Main Stage)
- **任务状态区**：
  - 任务标题、项目标签、核心状态徽标（`ACTIVE`, `PAUSED`, `BLOCKED`, `COMPLETED`）。
  - 遇到 `BLOCKED` 时，顶部弹出黄色/红色警告条，清晰展示阻塞原因（如 `REPAIR_LIMIT`、`TIMEOUT` 或外部环境不可用），并提供“重试 (Retry)”或“人工介入说明”。
- **顶置控制栏 (Action Bar)**：
  - **派发下一项 (Dispatch)**：请求调度器释放下一个符合 DAG 依赖的待办工单。
  - **运行 Codex (Run Codex)**：当存在待运行的 Codex 派发时，一键触发后台子进程执行，按钮自动切换为“执行中 (Running...)”并禁用，防重复点击。
  - **暂停 (Pause) / 继续 (Resume)**：在暂停期间，展示“已暂停（在途执行将保留入库）”。
  - **手动交接辅助 (Manual Handoff)**：
    - 一键复制 `handoff` 完整指令（包含上下文与输入数据）；
    - 快速导入 `submit` 验收报告（提供带语法高亮和合法性预检的 JSON 弹窗）。
- **主体内容标签页 (Tabs)**：
  - **Tab 1: 协作时间线与看板 (Timeline)**：
    - 步骤流程卡片展示（当前处于哪个 step，每个 step 的验收项、状态及修复轮次 `repair_count: x/2`）。
    - 双方 Agent 的流转事件流（按时间倒序/正序，明确标注操作者 Codex / Antigravity / 系统）。
  - **Tab 2: 交付物与验收详情 (Verification & Evidence)**：
    - 展开展示验收结果、PASS/FAIL 状态、检查清单、失败复现步骤（Reproduction Steps）与证据引用。
  - **Tab 3: 运行记录与用量统计 (Runs & Telemetry)**：
    - 运行历史表格（Run ID、PID、Exit Code、Session ID、执行时长、创建时间）。
    - 产物日志浏览器（查看 `prompt.txt`、`result.json`、`stderr.log`、`events.jsonl`，超过 20 行默认折叠）。
    - 真实用量展示（保留 input_tokens, cached_tokens, output_tokens 原样字段，不臆造虚假费用）。

---

## 三、 接口设计规范 (HTTP API Contract)

所有接口统一前缀 `/api`，响应均为 `application/json`，错误时统一返回 HTTP 4xx/5xx 及 `{"error": {"code": "...", "message": "..."}}`：

| 方法 | 路径 | 功能说明 | 错误码示例 |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/health` | 探活与版本信息 | - |
| `GET` | `/api/projects` | 获取所有已注册项目列表及任务数统计 | - |
| `POST` | `/api/projects` | 添加本地项目目录 `{"name": "...", "path": "..."}` | `INVALID_INPUT` |
| `GET` | `/api/projects/{id}` | 获取项目详情及 `doctor` 诊断（git 状态等） | `NOT_FOUND` |
| `GET` | `/api/projects/{id}/tasks` | 获取指定项目下的所有任务列表（严格隔离） | `NOT_FOUND` |
| `POST` | `/api/tasks` | 创建新任务 `{"project_id": "...", "title": "...", "requirement": "..."}` | `INVALID_INPUT` |
| `GET` | `/api/tasks/{id}` | 获取任务完整聚合状态（含 steps, dispatches, events） | `NOT_FOUND` |
| `POST` | `/api/tasks/{id}/dispatch` | 触发一次任务派发 | `NOT_FOUND` |
| `POST` | `/api/tasks/{id}/submit` | 提交 Agent 报告 `{"report": {...}}` | `INVALID_REPORT`, `CONFLICT` |
| `POST` | `/api/tasks/{id}/pause` | 暂停任务 | `INVALID_STATE` |
| `POST` | `/api/tasks/{id}/resume` | 恢复任务 | `INVALID_STATE` |
| `POST` | `/api/tasks/{id}/retry` | 重试阻塞的派发 `{"dispatch_id": "..."}` | `INVALID_STATE`, `REPAIR_LIMIT` |
| `POST` | `/api/tasks/{id}/expire` | 检查并作废超时派发 | - |
| `GET` | `/api/tasks/{id}/template/{dispatch_id}` | 获取指定派发的结果模板 JSON | `NOT_FOUND` |
| `GET` | `/api/tasks/{id}/handoff/{dispatch_id}` | 获取供 Agent 使用的完整交接包 | `NOT_FOUND` |
| `POST` | `/api/tasks/{id}/run` | 异步启动 Codex 执行 `{"dispatch_id": "...", "timeout": 300}` | `INVALID_STATE` |
| `GET` | `/api/tasks/{id}/runs` | 获取任务关联的所有运行执行记录 | `NOT_FOUND` |
| `GET` | `/api/runs/{run_id}/artifacts/{name}` | 安全读取运行产物文本内容 | `NOT_FOUND`, `FORBIDDEN` |
| `POST` | `/api/demo` | 运行一次受控模拟全流程（明确标记 SIMULATED） | - |
| `GET` | `/*` | 提供编译打包后的前端 SPA 静态页面及资源 | - |

---

## 四、 前后端工程组织

```text
worktogether/
├── core.py              # 现有状态机核心
├── runtime.py           # 现有 Codex 运行管理
├── server.py            # [NEW] 生产 HTTP Web 服务（ThreadingHTTPServer + API 路由 + 静态托管）
└── static/              # [NEW] 前端静态资源发布目录 (index.html, app.js, app.css)

web/                     # [NEW] 前端源码目录（React + TypeScript 标准工程）
├── package.json
├── tsconfig.json
├── src/
│   ├── index.html
│   ├── main.tsx
│   ├── App.tsx
│   ├── types.ts
│   ├── api.ts
│   ├── components/
│   │   ├── ProjectSidebar.tsx
│   │   ├── TaskHeader.tsx
│   │   ├── ActionBar.tsx
│   │   ├── TimelineView.tsx
│   │   ├── StepsView.tsx
│   │   ├── RunsView.tsx
│   │   ├── ReportModal.tsx
│   │   └── ArtifactViewer.tsx
│   └── styles/
│       └── main.css
```
