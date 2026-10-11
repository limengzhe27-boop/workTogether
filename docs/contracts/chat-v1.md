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
| `allow_write` | 可选布尔；省略或 null 继承会话配置，false 使本条及其全部后续委派只读；会话关闭时请求 true 返回 APPROVAL_REQUIRED |
| `context_files` | 可选，最多 10 个非空文件路径字符串 |
| `tool_files` | 可选，默认无授权；最多 10 个唯一、显式的相对文件路径，仅本条用户消息的 API 回复可按需读取，不继承给 Agent 派活。拒绝隐藏/凭据路径、绝对路径、空段及 `..` |
| `referenced_task_ids` | 可选，最多 5 个非空任务 ID 字符串；仅注入同项目引用 |

拒绝额外字段，包括伪造 sender、intent、round、auto、held、room_members、write_epoch、write_authorized。
both 指 Codex 和 Antigravity，两者均须就绪；all 指当前就绪成员，与已配置团队
取交集；显式收件人不得超出团队。人数须为 1–12，不包括停用/未就绪 Agent。
就绪判断不检查真实登录、额度和全部权限。首次发送时将解析后的收件人固定为
room_members；之后的提问/派活只能在这个集合内流转，不能因启用新 Agent 而扩大。
前端发送明确的 ID 清单，而非动态 all，避免点击发送时加入用户尚未见到的新成员。

相同任务、相同 ID、相同 recipient/content 返回原消息；二者任一不同返回 409。
当前幂等比较**不包含** rounds / allow_write / tool_files / 引用字段，修改它们不会覆盖原消息。
重试请保持完整原请求；改变发送设置须使用新 ID，不要假定重发能更新授权。

## 消息与会话响应

`GET /api/tasks/{task_id}/messages` 返回 `{messages:[...],chat:{...}}`。
chat 为 `{allow_write,max_auto_rounds,auto_rounds_used,held,available_members}`。
held 是等待释放的消息数；available_members 是本任务可选择的就绪成员（若有团队则取交集），
不是旧消息可以扩展到的新房间。输入框展示本次所选房间与写入状态。
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
| `room_members` | 发送时确定的成员范围；后续 Agent 消息继承并可缩小，不能添加其他成员 |
| `write_epoch` | 服务端内部授权代次，随消息保存；不得作为客户端申请扩权的字段 |
| `write_authorized` | GET 时派生的当前有效写入授权；与历史请求 allow_write 区分，撤销后为 false |
| `context_files` / `referenced_task_ids` | 用户选择的上下文引用（非空时保存） |
| `tool_files` | 用户本条消息授予 API 的显式读取文件清单；默认不保存，不复制到后续 Agent 消息 |
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

新任务会话 allow_write:false、max_auto_rounds:2、auto_rounds_used:0。缺少 allow_write
的旧任务按 false 处理；已有明确保存的 true / false 保留。前端先读取授权再允许发送，
打开写入需要用户确认；关闭不需要确认。每轮仅一个可写 CLI 成员获写入派发，优先
具备编码能力者，其余并行只读。CODE/FIX/VERIFY 与持写 CHAT 在项目内互斥；
ACTIVE / BLOCKED 的占用都须先解决，正式执行也不能绕过未结束的群聊写者。

关闭写入会递增授权代次，旧消息的写授权永久失效：重新打开、显式重试或释放 HELD
均不会恢复它。未签发的旧消息可继续只读讨论；已经签发可写派发但尚未启动的进程，
在启动锁内复核失败后保存 PERMISSION_REVOKED / BLOCKED，必须显式处理，不自动重放。
需要重新写入时，开启会话写入并发送**新消息**。已经启动的进程不会被强杀；关闭开关
不承诺回收它已持有的操作系统权限，但其后续委派不能再使用被撤销的授权。
这不是 worktree 隔离或防外部编辑锁。API Agent 默认只用传入材料；显式 tool_files
可启用下述只读工具，不能获取写入权限。
写入仍受 CLI 沙箱/权限约束。Antigravity 写模式保留 sandbox，真实写入边界待独立验收。

暂停、阻塞、在途派发/运行或团队作业可能阻止新聊天派发。暂停不杀在途进程，允许
其结果入库；旧需求版本未交付消息进入 NEEDS_REVIEW。已完成任务可继续聊天，
不因此重启正式开发计划。失败不自动重试；没有写入权不等于完成了文件修改。

## Agent 互发消息与预算

CHAT 报告采用 [Report envelope](v1.md)，DONE 的 summary 为回复；suggestions 最多三条。
data 可为空，或包含 proposals / messages。proposals 是分工建议，不自动创建正式步骤；
每条为 `{title,work_kind,assignee,reason}`，最多三条。
messages 最多三条，每条为 `{to,intent,content}`，content 非空且 ≤4000 字符。

- ask / assign 必须点名另一位就绪房间成员；to:all 被拒绝，不能广播派活。
- claim / report 是告知型，保存为 DELIVERED，participants 为空，不调用或计预算。
- sender、ID、parent_message_id 由平台生成，不执行模型提供的命令字符串。
- 后续房间范围 = 原消息的所选成员 ∩ 当前团队允许的就绪成员。单独点名 Codex 后，
  它不能邀请未选中的 Antigravity；要它们合作，请在发送前同时选择两者。
- 衍生写授权 = 父消息仍有效的授权 ∩ 当前会话开关。用户单条只读限制不能通过派活
  绕过；原本可写的请求中，暂时只读的同伴仍可向房间内另一位成员交接写工作。
- 旧用户消息缺少 room_members 时仅使用其已保存的收件人；旧自动消息缺少范围时
  保守停在 ROOM_SCOPE_CHANGED，提供“跳过后重新选择成员发送”的恢复入口。
- 派发时、CLI 启动前和 API 请求准入前都重新检查成员范围；已失效的范围不能启动新调用。
  API 在请求体/凭据准备完成后复查任务、派发、成员与只读限制，再登记 RUNNING。
  准入失败留下 FAILED/BLOCKED 与原因，不发 HTTP、不自动重放。API 不接受写入派发。
  RUNNING 是 API 的准入边界：网络等待不持数据库锁，用户仍可暂停；已准入的请求不能
  撤回，返回结果可保存，但暂停期间不会因此发起新调用。清除唯一密钥导致成员不再就绪
  时，尚未准入的聊天请求不能继续使用内存中的旧密钥。

max_auto_rounds 名称沿用历史，实际按每条入队的自动 ask/assign 消息计数，
不是模型内部轮次、Token 或总费用上限。每条消耗一次，超限进入 HELD。
`POST .../chat-config` 仅接受 allow_write（布尔）和 max_auto_rounds（整数 0–8）；
空对象读取配置，不释放 HELD；修改上限也不重置已用计数。

`POST .../chat-release` 使用 `{}`，将**所有** HELD 消息放回 QUEUED；有释放才把
已用计数归零。释放本身不调用模型，但后台消费者随后会处理并可能产生用量。
释放不改变房间范围或授权代次。可以反复释放，所以预算不是整个会话的硬费用封顶。

## API 限定读取工具（第一阶段）

只有 CHAT 用户消息可授权，默认关闭；在输入框选中 API 成员和项目文件后，可勾选
“允许 API 按需读取所选文件”。前端发送明确 tool_files，与写入开关无关；更换成员或
文件需重新勾选。该授权涵盖本条消息选择的 1–2 轮，各 API 回复分别计预算；取消下一条
消息的勾选不会撤回旧消息，需暂停任务以阻止后续读取/请求。ask/assign 不继承读取授权，
正式分析/规划暂不启用工具。不自动启用 API Agent，也不自动做真实连通探测。

- list_files({}) 只返回授权清单（剔除运行目录），不扫描目录；read_file({path,offset?,limit?})
  仅打开清单内普通文件，offset 为字节偏移，limit 默认/最大 12000 字节。单文件最大 1 MiB。
- 每次 API 回复最多 4 次工具调用（拒绝也计数）、5 次 HTTP 请求、48000 字节读取。
  最后一次请求 tool_choice:none；仍请求工具则 TOOL_LIMIT/BLOCKED，不追加预算。
  保留原任务超时与剩余请求超时；这是有界回路，不是服务商 Token/计费硬封顶。
- POSIX 逐层目录描述符 + O_NOFOLLOW，不跟随符号链接，拒绝多硬链接、FIFO、目录、
  二进制片段、隐藏路径、常见凭据文件和状态库/运行目录。缺少安全读取 API 时失败，
  不降级成普通路径打开。此规则不是秘密扫描器：源文件/普通文档中自行写入的秘密仍需用户确认。
- 每次读文件和每次后续 HTTP 前复查房间/任务/读取范围/API 目标；已发送内容不能收回。
  修改目标或撤销范围会停止继续发送，不自动重试。只读工具没有 shell/write_file 能力，
  未知工具返回拒绝；模型无法通过参数扩大授权。
- 工具授权文件不再经旧附件读取通道注入；启用工具的 API 回复不附送自动 README 正文，
  要读取 README 请把它选进清单。既有用户文字、其它引用材料、项目记忆和讨论上下文仍会发送。
- 返回片段带字节位置、截断标记、mtime_ns 和片段 SHA-256；这是读取时证据，不是版本快照。
  events.jsonl 记录调用结果元数据而非重复文件正文；run.usage 汇总已返回的用量，失败也保留，
  未返回用量的请求不推算。run.api_requests / tool_calls 为本次运行已收到响应数/已执行工具数。

## 显式恢复与验证边界

`POST .../messages/{message_id}/resolve` 接受 skip（默认）或 retry，只能处理
BLOCKED / RUNNING / QUEUED。PREPARED/RUNNING 进程须先确认已停止。
这是**消息级**操作，处理该消息关联的未结束派发，不支持传单个 role 定向重试。
旧派发成为 SUPERSEDED；skip 保存占位，retry 重新排队，已成功结果保留。
不会自动重放失败成员，也不以恢复绕过权限。

确定性覆盖：test_group_chat.py、test_server_context.py、test_contracts.py、test_room_authorization.py、test_openai_runtime.py。
test_read_tools.py 覆盖文件边界、工具预算/撤权、HTTP 假服务的列文件→读取→回复闭环；无真实模型。
新增模拟 CLI 的 HTTP 两轮讨论与同伴跟进闭环，不是实际模型验收。
真实证据以 [group-room 验收记录](../../qa/group-room/acceptance.md) 为准。
本次契约对齐未调用真实模型，未验证 Antigravity 写权限、长会话成本或并行写隔离。
