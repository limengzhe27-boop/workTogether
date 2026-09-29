# Web Workspace Delivery (v0.3)

日期：2026-09-14。交付内容：**WorkTogether 本地交互式网页工作台 (Web Workspace)**，包括 Python 标准库 HTTP 服务、REST API、零依赖前端 Bundle 以及全套 React/TypeScript 源码与自动化测试。

---

## 1. 架构与交付清单

### 后端与 API 服务 (`worktogether/server.py`)
- **零外部依赖**：基于 Python 标准库 `http.server.ThreadingHTTPServer`、`json`、`urllib`、`sqlite3`，开箱即用，无需 pip 安装三方框架。
- **命令行集成 (`worktogether/__main__.py`)**：新增 `web` 子命令，支持参数 `--port` (默认 8765)、`--host` (默认 127.0.0.1)、`--db`、`--static`。
- **14 个标准 RESTful 接口**：
  1. `GET /api/health`：健康检查。
  2. `GET /api/projects` & `POST /api/projects`：项目列表与新建。
  3. `GET /api/projects/{id}`：获取项目信息及健康度排查 (Doctor)。
  4. `GET /api/projects/{id}/tasks`：项目级任务过滤。
  5. `GET /api/tasks` & `POST /api/tasks`：全任务列表与新建任务。
  6. `GET /api/tasks/{id}`：任务详情（阶段、状态、步骤、在途派发、修复计数）。
  7. `POST /api/tasks/{id}/dispatch`：推进调度（生成 PLAN/PLAN_REVIEW/CODE/VERIFY 派发）。
  8. `POST /api/tasks/{id}/submit`：接收结构化交付报告（带原子回滚与强校验）。
  9. `POST /api/tasks/{id}/pause` & `POST /api/tasks/{id}/resume`：任务暂停与恢复。
  10. `POST /api/tasks/{id}/retry` & `POST /api/tasks/{id}/expire`：超时处理与重试。
  11. `GET /api/tasks/{id}/template/{dispatch_id}`：获取报告 JSON 模板。
  12. `GET /api/tasks/{id}/handoff/{dispatch_id}`：获取结构化交接包（任务上下文、阶段目标、报告模板）。
  13. `POST /api/tasks/{id}/run`：Codex 单步异步后台执行，不阻塞 HTTP 线程。
  14. `GET /api/tasks/{id}/runs`：查看真实 Codex 历史运行与 Token 遥测。
  15. `GET /api/runs/{run_id}/artifacts/{name}`：受控查看产物。
  16. `POST /api/demo`：一键创建模拟演示任务（明确标注 `[SIMULATED]`）。

### 安全边界与防护
- **地址隔离**：严格绑定 `127.0.0.1` 本地回环地址，杜绝外部网络访问。
- **产物穿越防护**：产物查看仅限于安全白名单（`prompt.txt`、`command.json`、`events.jsonl`、`stderr.log`、`result.json`），使用绝对路径边界检查禁止任何 `../` 目录穿越。
- **无任意命令执行**：服务端不提供任何任意 Shell 命令执行端点。
- **请求体防爆**：限制 JSON 请求体上限为 5MB，超出直接返回 `PAYLOAD_TOO_LARGE`。

### 前端双轨交付
1. **零依赖生产静态包 (`worktogether/static/`)**：
   - `index.html`、`app.css` (9.7KB)、`app.js` (50.9KB)。
   - 无需 Node.js / npm，启动 Python 服务即可在浏览器中完整使用全部功能。
2. **完整 React + TypeScript 工程源码 (`web/`)**：
   - 类型契约定义：`web/src/types.ts`。
   - API 客户端封装：`web/src/api.ts`。
   - 模块化组件：`ProjectSidebar`、`TaskHeader`、`ActionBar`、`TimelineView`、`StepsView`、`RunsView`、`ReportModal`、`HandoffModal`。
   - 样式表：`web/src/styles/main.css`。

---

## 2. 交互功能与页面设计

- **项目与任务导航**：左侧树状视图，支持多项目切换、新建任务与项目健康度 Doctor 检查。
- **任务状态感知**：实时感知 ACTIVE、PAUSED、COMPLETED、BLOCKED（如达到 2 次代码修复上限时触发 `REPAIR_LIMIT`）。
- **操作面板 (Action Bar)**：
  - **派发 (Dispatch)**：根据状态自动触发下一步派发。
  - **Codex 运行 (Run)**：后台异步唤起本地已登录的 Codex 执行，自动轮询刷新状态。
  - **一键交接 (Handoff)**：一键生成结构化交接 Markdown 并复制到剪贴板，方便 Antigravity 手动协作。
  - **提交报告 (Submit)**：预填充模板的 JSON 录入弹窗，支持语法自检。
  - **暂停/恢复/重试/过期**：提供任务控制流操作。
- **三重视图**：
  1. **时间线 (Timeline)**：可视化展示事件流、在途派发卡片，支持查看原始事件 Payload。
  2. **步骤与验收 (Steps)**：阶段步骤卡片、依赖约束标记、修复次数计数器（`step.repair_count`）、验收标准与问题发现。
  3. **运行与产物 (Runs & Telemetry)**：
     - **客观数据原则**：完整呈现 Codex 运行原始 Token（input / cached / output），不编造虚拟计费，无数据时明确显示“暂无数据”。
     - **产物审查**：点击即可安全查看 `prompt.txt`、`result.json` 等原始执行产物。

---

## 3. 测试与验证结果

全量测试套件共 43 项自动化测试，全部通过（`Ran 43 tests in 1.757s, OK`）：
- **11 项验收测试** (`tests/acceptance/antigravity/`)：覆盖 ACC-001 至 ACC-010 核心场景（生命周期、修复上限、幂等重试、版本校验、项目隔离、暂停在途处理、崩溃重启恢复等）。
- **18 项核心引擎测试** (`tests/core/test_engine.py`)：覆盖并发排他锁、两轮修复上限、原子回滚与模板校验。
- **8 项运行时测试** (`tests/core/test_runtime.py`)：覆盖真实子进程 fixture、session 恢复、超时阻断、孤儿进程处理。
- **6 项 HTTP Server 测试** (`tests/core/test_server.py`)：使用内存模拟流（SimulatedSocket）测试路由分发、CRUD、状态机转换、路径穿越拦截及静态文件交付。

---

## 4. 启动与使用方式

```bash
# 启动本地服务
python3 -m worktogether web --port 8765 --db .collab/state.sqlite3
```

在浏览器访问：**http://127.0.0.1:8765/**

---

## 5. 当前局限性与下一阶段路线

1. **Antigravity 自动化执行**：目前 Antigravity 端仍通过“一键交接 (Handoff) 剪贴板复制 + 报告提交”模式进行；待本机 headless 登录与权限打通后接入自动化子进程运行。
2. **Token 差分与用量模型**：当前保留 Codex 返回的原始计数（可能包含上下文累积）；后续里程碑计划增加基于单次运行增量的差分统计。
3. **工作区隔离**：当前执行在共享项目路径下进行；后续规划按任务创建 Git worktree / 隔离快照。
