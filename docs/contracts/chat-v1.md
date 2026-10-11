# 项目群聊契约 v1（2026-10-11 对齐实现）

本契约描述当前 core / chat / server 的行为，不是新授权或真实模型能力证明。
HTTP 总表与错误见 [v1.md](v1.md)。群聊与“讨论方案 → 审批 → 执行验收”是两个入口：
群聊可在已有写入授权下工作，但聊天中的“批准”文字不等于正式计划的摘要批准，
也不会自动生成正式步骤。

## 用户消息请求

`POST /api/tasks/{task_id}/messages` 返回 202 消息对象，只代表保存成功。

| 字段 | 输入约束 / 默认值 |
|---|---|
| `message_id` | 必填，非空字符串，≤128 字符；幂等键作用域为任务 |
| `recipient` | 必填：单个 Agent ID、逗号分隔的 ID、both 或 all |
| `content` | 必填，非空字符串，≤20000 字符 |
| `rounds` | 可选，整数 1 或 2（不接受布尔值），默认 1；本条用户消息的发言轮数 |
| `allow_write` | 可选布尔；省略或 null 继承会话配置，false 仅使本条用户消息只读 |
| `context_files` | 可选，最多 10 个非空文件路径字符串 |
| `referenced_task_ids` | 可选，最多 5 个非空任务 ID 字符串；仅注入同项目引用 |

拒绝额外字段，包括伪造 sender、intent、round、auto、held。
both 指 Codex 和 Antigravity，两者均须就绪；all 指当前就绪成员，与已配置团队
取交集；显式收件人不得超出团队。人数须为 1–12，不包括停用/未就绪 Agent。
就绪判断不检查真实登录、额度和全部权限。

相同任务、相同 ID、相同 recipient/content 返回原消息；二者任一不同返回 409。
当前幂等比较**不包含** rounds / allow_write / 引用字段，修改它们不会覆盖原消息。
重试请保持完整原请求；改变发送设置须使用新 ID，不要假定重发能更新授权。

## 消息与会话响应

`GET /api/tasks/{task_id}/messages` 返回 `{messages:[...],chat:{...}}`。
chat 为 `{allow_write,max_auto_rounds,auto_rounds_used,held}`，held 是等待释放的消息数。
两者分别读取，不承诺并发更新期间的原子快照。

消息基本字段：id、task_id、sender、recipient、content、participants、revision、
status、round、rounds、replies、created_at（Unix 秒）。字段语义如下：

| 字段 | 响应语义 |
|---|---|
| `sender` | 用户消息固定 user；Agent 消息由派发角色生成，不能从发送接口指定 |
| `intent` | Agent 消息的 ask / assign / claim / report；用户消息通常无此字段 |
| `round` / `rounds` | 当前轮 / 此消息目标轮数，默认均为 1；不是自动派发预算 |
| `auto` / `parent_message_id` | Agent 衍生消息标记及来源消息 ID |
| `held` | 超预算消息的 true 标记；释放后移除，并非每条消息都有 false |
| `allow_write` | 请求写入时保存 true；缺省表示此消息未请求写入，不保证每位接收者可写 |
| `context_files` / `referenced_task_ids` | 用户选择的上下文引用（非空时保存） |
| `pending_roles` / `failed_roles` | GET 派生字段：当前轮待回复 / 失败成员 |
| `active_roles` / `active_since` / `write_role` | 运行中的派发成员、开始时间及获写授权成员（如有） |
| `wait_reason` | 如 AUTO_ROUND_BUDGET；不是成功或持续运行证明 |
| `blocked_role` / `blocked_roles` / `blocked_dispatch_id` / `error` | 阻塞定位信息（如有） |

回复通常为 `{role,content,report,round,dispatch_id,ok}`；跳过产生 skipped:true 占位，
不承诺含 report/dispatch_id。changed_files（如有）为 `[{path,status}]`，status 为
added / modified / removed。归因是时间窗的有限文件快照差异，不证明全部由该 Agent 修改。

状态：QUEUED / RUNNING / DELIVERED / BLOCKED / NEEDS_REVIEW / CANCELLED / HELD。
RUNNING 代表已有派发，进程状态以 runs 为准；重启不自动重放未知工作。
DELIVERED 代表对话交付（可以含跳过），不等于项目验收完成。

## 并行、上下文与写入

每个任务同一时间消费一条消息的一轮；一轮内共同签发派发，线程池有界并行。
当前上限为 4 个任务工作者、每个工作者 4 个 Agent，并非 12 人无限同时运行。
第 1 轮没有同伴本轮回复；第 2 轮携带上一轮未跳过回复，每条截至 2000 字符。
另附最近 4 条 DELIVERED 消息（回复各截至 2000 字符）、需求、方案、项目记忆、
README 前 3000 字符和最多 120 个路径。引用文件单个 ≤12000 字节、总计 ≤48000 字节。
任务引用取同项目最近两条消息的回复摘要（各 600 字符），无回复则取需求摘要；
不存在或跨项目引用不会注入。这不是共享其它桌面聊天窗口。

新任务会话 allow_write:true、max_auto_rounds:2、auto_rounds_used:0。当前读取逻辑也为
缺少 allow_write 的旧任务补 true；已明确保存的 false 不变。旧会话迁移的授权提示
仍需安全复核，不能把“缺省字段”理解成只读。每轮仅一个可写 CLI 成员获写入派发，优先
具备编码能力者；其余并行只读。跨任务检查同项目已有 CODE/FIX 或持写 CHAT。
这不是 worktree 隔离或防外部编辑锁。API Agent 无本地工具，只能使用传入材料。
写入仍受 CLI 沙箱/权限约束。Antigravity 写模式保留 sandbox，真实写入边界待独立验收。

暂停、阻塞、在途派发/运行或团队作业可能阻止新聊天派发。暂停不杀在途进程，允许
其结果入库；旧需求版本未交付消息进入 NEEDS_REVIEW。已完成任务可继续聊天，
不因此重启正式开发计划。失败不自动重试；没有写入权不等于完成了文件修改。

## Agent 互发消息与预算

CHAT 报告采用 [Report envelope](v1.md)，DONE 的 summary 为回复；suggestions 最多三条。
data 可为空，或包含 proposals / messages。proposals 是分工建议，不自动创建正式步骤；
每条为 `{title,work_kind,assignee,reason}`，最多三条。
messages 最多三条，每条为 `{to,intent,content}`，content 非空且 ≤4000 字符。

- ask / assign 必须点名另一位就绪房间成员，生成后续调用。当前 to:all 虽可通过
  报告形状校验，但不会生成这两种派发，不能用来广播派活。
- claim / report 是告知型，保存为 DELIVERED，participants 为空，不调用或计预算。
- sender、ID、parent_message_id 由平台生成，不执行模型提供的命令字符串。
- 房间成员 = 团队 ∩ 就绪；无团队时为全部就绪 Agent，**不等于输入框本次收件人**。
  因此被点名者可以交给另一位房间成员。显式房间范围 UX 仍是 P0-4。
- 衍生消息继承**当前会话**写入授权，不继承父消息的 false。只使一条用户消息只读
  不能保证后续委派只读；整段交流只读需先关闭会话写入开关。

max_auto_rounds 名称沿用历史，实际按每条入队的自动 ask/assign 消息计数，
不是模型内部轮次、Token 或总费用上限。每条消耗一次，超限进入 HELD。
`POST .../chat-config` 仅接受 allow_write（布尔）和 max_auto_rounds（整数 0–8）；
空对象读取配置，不释放 HELD；修改上限也不重置已用计数。

`POST .../chat-release` 使用 `{}`，将**所有** HELD 消息放回 QUEUED；有释放才把
已用计数归零。释放本身不调用模型，但后台消费者随后会处理并可能产生用量。
可以反复释放，所以预算不是整个会话的硬费用封顶。

## 显式恢复与验证边界

`POST .../messages/{message_id}/resolve` 接受 skip（默认）或 retry，只能处理
BLOCKED / RUNNING / QUEUED。PREPARED/RUNNING 进程须先确认已停止。
这是**消息级**操作，处理该消息关联的未结束派发，不支持传单个 role 定向重试。
旧派发成为 SUPERSEDED；skip 保存占位，retry 重新排队，已成功结果保留。
不会自动重放失败成员，也不以恢复绕过权限。

确定性覆盖：test_group_chat.py、test_server_context.py、test_contracts.py。
真实证据以 [group-room 验收记录](../../qa/group-room/acceptance.md) 为准。
本次契约对齐未调用真实模型，未验证 Antigravity 写权限、长会话成本或并行写隔离。
