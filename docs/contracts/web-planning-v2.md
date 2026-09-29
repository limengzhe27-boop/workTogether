# 网页规划与批准接口（增量 v2）

## 真实功能与限制

后台已支持有限规划与双提供方单步运行，已接入 V2 React 生产界面。
支持安全边界上的需求修订，但不支持任务内自由聊天或无人值守开发全循环。
不要把接口可用标成完整 APP 完成。

服务仅监听本机。请求必须同源，JSON 请求体必须是对象。取消通配 CORS，Vite
开发服务器应代理 `/api` 到本机后端，不要从浏览器跨源直连。Host 仅允许本机名称。
这不是多用户认证系统，不保护用户免受有本机权限的恶意进程攻击。

## 接口

- `POST /api/tasks/{task}/revisions`：请求 `{message_id, expected_revision, requirement}`。
  requirement 是用户确认的完整新需求，而不是悄悄丢弃原需求的追加片段；编辑器应预填当前需求。
  message_id 为客户端生成并在网络重试时复用的唯一 ID。返回 `{revision, duplicate}`。
  GET 任务详情包含 revision（旧记录缺省为 1）与 revisions 历史。
  只有无 ACTIVE 派发、无 PREPARED/RUNNING/EXITED 运行、无 QUEUED/RUNNING 规划作业时接受。
  发生版本冲突返回 409；不应丢弃输入。暂停不代表进程已停止，不能用暂停绕过以上限制。
  接受后：保存旧需求、计划、步骤与已有报告，撤销批准，回到 PLANNING；暂停状态保持。
  不回滚已有代码，不自动调用模型，不创建新任务。历史成功报告的重复回传只做幂等确认。
  新派发包含 requirement_revision，同一 Agent 会话明确收到以最新需求为准的指令。

- `POST /api/tasks/{task}/discuss`：请求 `{ "timeout": 300 }`（每次模型调用的秒数，最多 1800）。
  返回 202 和 `{id, task_id, status: "QUEUED", created_at, result: null, error: null}`。
  最多三轮，定稿后停在开发前。202 只说明已登记，不代表模型成功运行。
- `GET /api/tasks/{task}/planning-jobs`：返回 `{jobs: [...]}`。
  状态 QUEUED / RUNNING / COMPLETED / FAILED；失败详情见 error 或 result 中的运行记录。
  重复发起返回 409。服务重启后未知作业不自动重放；其 QUEUED/RUNNING 状态可能已经失联，
  不能仅凭该状态宣称进程仍在工作。目前没有一键恢复未知规划作业的网页接口。
- `GET /api/tasks/{task}/approval`：返回 `{digest, approved, requirement, plan, revision}`。
  前端先核对这份快照与正在展示的需求/方案一致，再启用批准。
  前端展示定稿后保存该 digest，让用户明确确认；不要在确认后重新获取摘要冒充已审阅版本。
- `POST /api/tasks/{task}/approve`：请求 `{digest: "用户看到的摘要"}`。
  仅 ACTIVE / EXECUTING 阶段允许；过期摘要返回 409。批准绑定需求和最终计划。
- `POST /api/tasks/{task}/run`：请求 `{dispatch_id, timeout}`。按派发角色选择提供方，
  先登记唯一运行，再返回 202，含 run_id 和 provider。不存在/重复/暂停/未批准的请求直接拒绝。
  后续用现有 runs 接口查询结果。CODE/FIX 未批准返回 APPROVAL_REQUIRED。
  CLI 显式运行仍沿用原授权语义；网页批准门不代替本机 CLI 权限。

提供方路径和模型只能由服务启动参数设置，不能从请求体注入。

## 启动配置

`python3 -m worktogether web --db .collab/state.sqlite3 --codex-executable /absolute/path/to/codex --antigravity-executable /absolute/path/to/antigravity --antigravity-model gemini-3.8-flash-high`

路径需对应本机已安装程序。已有后台服务不会热更新 Python 代码，需在确认在途工作状态后重启。
不要为替换界面而终止用户正在使用的旧服务。

## 前端接入要求

1. 规划阶段无在途派发时显示“开始讨论（最多三轮）”，点击后展示作业进度和各轮报告。
2. 正确区分任务阶段、作业状态和模型运行状态。完成规划不等于完成开发。
3. 定稿后展示计划和“批准此方案”按钮；未批准不得启用开发启动按钮。
4. 接口报错必须保留原因；不自动重试 POST 或重复付费调用。
5. 暂停仅阻止后续执行，不表示终止正在运行的模型。未知作业显示需人工检查，不能显示成功。

确定性测试 60 项通过，包括 HTTP 三轮规划、重复登记、重启不重放、提供方失败、
批准摘要失效、跨源拦截、修订幂等、历史保留、忙碌时拒绝修订与暂停语义。
未新增真实模型调用，不以测试桩结果替代真实模型能力验收。
