# WorkTogether

本地 Codex + Antigravity 协同研发平台，包含调度核心、命令行及 React 网页工作台。目前处于原型阶段。

当前支持有限轮规划讨论、方案审批、自动开发与验收调度（每轮最多 6 次调用、最多两次修复）、任务内消息和保守的异常恢复。真实开发及自动交接已验证；Antigravity 无人值守验收仍受工具权限限制，尚未完成完整端到端验收。方案审批不会自动授予 Agent 的工具权限。

模型调用需要自行安装并登录相应 CLI；可通过 `--codex-executable` 和 `--antigravity-executable` 指定路径。下方部分界面说明记录早期版本，当前流程以 [自动执行说明](docs/execution-v1.md) 为准。本地凭据、运行数据库和原始实验记录不随仓库发布。

## 快速启动 Web 工作台

Python 3.9+，运行 Web 工作台与调度核心无需安装第三方依赖（开箱即用）：

```bash
# 启动本地 Web 服务器（默认端口 8765，绑定 127.0.0.1）
python3 -m worktogether web --port 8765 --db .collab/state.sqlite3
```

启动后在浏览器打开：**http://127.0.0.1:8765/**

### Web 工作台特性
- 🗂️ **项目与任务中心**：左侧多项目管理、新建任务、项目 Doctor 健康度检查（Git 干净度/在途任务排查）。
- 🧭 **状态与阶段感知**：实时展示任务状态（ACTIVE / PAUSED / COMPLETED / BLOCKED）、阶段徽章、在途派发提示及阻塞告警。
- ⚡ **操作面板与安全交互**：
  - **派发 (Dispatch)**：根据状态自动派发 PLAN、PLAN_REVIEW、CODE 或 VERIFY。
  - **Codex 后台执行 (Run)**：一键异步启动真实本机已登录的 Codex 单步执行，前台自动轮询刷新状态。
  - **交接包 (Handoff)**：一键复制结构化交接 Markdown（包含阶段目标、约束及报告模板）至剪贴板，供 Antigravity 手动交接。
  - **提交报告 (Submit)**：预填充模板的 JSON 报告录入弹窗，支持语法自检与错误提示。
  - **暂停/恢复/重试/过期**：提供任务控制流操作。
- 📊 **三重视图**：
  - ⏱️ **时间线 (Timeline)**：可视化展示事件流、在途派发卡片，支持查看原始事件 Payload。
  - 📋 **步骤与验收 (Steps)**：按阶段展示子任务、依赖关系、修复次数计数器（最多 2 次 FIX）、验收标准及问题列表。
  - 🚀 **运行与产物 (Runs & Telemetry)**：完整展示 Codex 运行历史、原始真实 Token 消耗指标（从不编造费用），内置安全产物查看器（`prompt.txt`、`command.json`、`events.jsonl`、`stderr.log`、`result.json`），具备严格路径穿越防护。
- 🔒 **安全边界**：仅监听 `127.0.0.1` 本地回环地址，无任意 Shell 执行接口，产物文件白名单受控。

---

## 命令行与自动化

核心与自测同样无需安装外部依赖：

```bash
# 查看帮助
python3 -m worktogether --help

# 运行模拟演示（明确标记 SIMULATED，不消耗模型 Token）
python3 -m worktogether --db /tmp/worktogether-demo.sqlite3 demo

# 运行全量测试套件（涵盖核心、运行时、验收及 HTTP Server）
python3 -m unittest discover -s tests -v
```

## 命令行操作

```bash
python3 -m worktogether project-add --name 我的项目 --path /absolute/project/path
python3 -m worktogether task-add --project PROJECT_ID --title 优化进度 --requirement '刷新后恢复任务状态'
python3 -m worktogether dispatch --task TASK_ID
python3 -m worktogether handoff --task TASK_ID --dispatch DISPATCH_ID
python3 -m worktogether template --task TASK_ID --dispatch DISPATCH_ID
python3 -m worktogether submit --task TASK_ID --file /absolute/result.json
python3 -m worktogether show --task TASK_ID
```

显式执行 Codex 派发：

```bash
python3 -m worktogether run --task TASK_ID --dispatch DISPATCH_ID --timeout 300
python3 -m worktogether runs --task TASK_ID
```

任务控制命令：

```bash
python3 -m worktogether pause --task TASK_ID
python3 -m worktogether resume --task TASK_ID
python3 -m worktogether expire --task TASK_ID
python3 -m worktogether retry --task TASK_ID --dispatch DISPATCH_ID
python3 -m worktogether doctor --project PROJECT_ID
```

## 前端开发（可选）

工作台内置零依赖静态包（位于 `worktogether/static/`）。若需基于 TypeScript / React 源码调试前端：

```bash
cd web
npm install
npm run dev
```

## 开发交接与文档

- 架构设计与 UI 决策：[DESIGN.md](DESIGN.md)
- 接口契约：[docs/contracts/v1.md](docs/contracts/v1.md)
- 运行时契约：[docs/contracts/runtime-v1.md](docs/contracts/runtime-v1.md)
- Web 工作台交付报告：[docs/handoff-web-v1.md](docs/handoff-web-v1.md)
- 演进路线：[docs/roadmap.md](docs/roadmap.md)
