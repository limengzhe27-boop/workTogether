# 原生团队实验契约

这是独立 AO 实验分支的后台协议，不是 WorkTogether 现有 Python 服务的新接口，也不是已经完成的群聊产品。

## 构建与保留

上游固定 v0.14.10 / 033d597032e448a0ea16c298350e36b099f74106。
依次应用 `ao-scoped-peer-mcp.patch` 以及本目录 experiments 下按编号顺序排列的补丁。当前源码已位于 `~/Applications/worktogether-evaluation/source`，分支 `codex/scoped-peer-mcp`。
Go 1.27.1，在 backend 目录执行 `go build -o /absolute/evaluation/ao-room ./cmd/ao`。不替换签名应用内二进制，不覆盖用户真实 ~/.ao 数据。所有测试使用独立 AO_DATA_DIR 和 AO_RUN_FILE。

## 房间与消息

本机后台 `/api/v1` 下：

| 方法与路径 | 含义 |
| --- | --- |
| GET /peer-rooms/{roomId} | 读取成员、epoch、预算与已用次数 |
| PUT /peer-rooms/{roomId} | 用旧 epoch 做比较更新；首次 epoch=0；每次成功递增 |
| POST /peer-rooms/{roomId}/broadcast | 用户消息按成员顺序预占预算，再并行投递，逐个返回结果 |
| POST /peer-rooms/{roomId}/send | 事务性预占预算后投递，失败/不确定不重放 |
| GET /peer-rooms/{roomId}/history | 读取获准投递的持久记录及最新接收状态 |
| GET /peer-rooms/{roomId}/tasks | 读取任务清单和 revision |
| PUT /peer-rooms/{roomId}/tasks | 创建开放任务，需唯一 id 与 goal |
| POST /peer-rooms/{roomId}/tasks | claim / submit / accept / reject |
| POST /peer-rooms/{roomId}/integrate | 创建候选集成分支 |

房间配置：projectId、members（明确会话 ID）、epoch、budget（1–100 次）、paused。成员数量上限16；修改不清零 used。暂停时允许清空成员，以保证失效成员不会阻止暂停。
发送：sender、target、id（稳定投递标识）、epoch、message（最多2000字节）。相同 ID 变更正文或目标被拒绝；pending/uncertain 保留等待排查，不自动再次调用模型。预占之后暂停不承诺取消已准入操作。失败也消耗一个预算位。

## 原生工具绑定

实验后台显式设置 AO_PEER_PROJECT、AO_PEER_ROOM、AO_PEER_EPOCH 后，新建/恢复的原生工具进程继承固定绑定。
- peer_send：id、session、message；发送者取 AO_SESSION_ID，模型不能通过工具参数冒充。
- peer_tasks：读取任务。
- peer_task：id、revision、action，以及交付/验收所需 evidence、commit。
这些工具通过原生 MCP 执行；没有开放 Codex 终端网络，也没有替代客户端自身的编辑/执行工具。

## 任务与集成

open → claimed → submitted → accepted。claim 是原子竞争；submit 只能由所有者发起，提供证据与完整40位候选提交；accept/reject 必须由另一名房间成员发起并附证据；reject 回到 claimed。每次操作检查房间 epoch 和任务 revision，并追加历史事件。

integrate 请求：epoch、base（固定40位基线提交）、name（候选名称）。全部任务必须已独立验收；后台验证候选提交属于同一仓库、存在于所有者历史且派生于 base。使用 Git 对象合并生成 `codex/integration-{name}` 新分支；不触碰工作目录/索引、不覆盖主分支、同名分支不覆盖。冲突时不发布分支。结果是待测试/审查的候选版本，不是最终发布。

## 信任边界与未完成部分

AO 本机管理 API 继承其可信本地进程模型；本实验不把它宣传成抵御恶意本机进程的认证层。绑定过房间的会话，其普通发送、追加指令、编辑重发、切换模型发送及侧边提问入口会返回 PEER_ROOM_REQUIRED；从房间移除也不恢复这些旧入口。绑定记录持久保存，目前没有解除绑定入口。已准入的房间发送凭后台内部上下文通过检查，HTTP 请求无法直接提供该上下文。此限制不是操作系统权限控制，也不代表所有本地管理能力均受房间约束。普通原生终端的能力保持原配置，不能把工作目录隔离说成整个操作系统隔离。

已新增默认项目房间 UI，尚未打包交付桌面应用；仍没有自动拆分/依赖调度或候选分支集成测试调度。不要把可调用后台 API 当作完整产品交付。正式迁移前须补齐这些入口和统一授权语义。

## 用户广播与重试语义

`POST /peer-rooms/{roomId}/broadcast` 请求包含 `id`（1–100 字节稳定标识）、`epoch` 和 `message`（非空、最多 2000 字节）。用户广播不是模型 MCP 工具。每位接收者消耗同一房间总预算中的一次；预算不足时按成员顺序准入，返回部分成功，不假装全体接收。

返回 `deliveries` 数组，每项包含 `session`、`state` 和可选 `detail`：
- `accepted`：原生发送入口接受，不等于 Agent 已完成回答。
- `already_accepted`：同一广播、成员和内容之前已接受，没有重复发送。
- `blocked`：未获准入，包括预算不足、成员/代次变化及未决投递。
- `uncertain`：发送失败或结果未能落库，禁止自动重放，需人工核查。

收件人快照来自当前房间；每次预占还会重新验证房间状态、代次、成员与项目。并行发送已经获准的成员，暂停不强杀在途调用。重复请求须保持相同 id 与正文；不要为了绕过 uncertain 自动生成新 id。房间更新后旧 epoch 失效。

## 持久投递记录

`history` 返回当前房间的投递数组，字段为 `id / sender / target / message / epoch / state / createdAt`。正文和成员信息与预算预占处于同一事务；接收状态更新复用同一记录，不新增第二份状态。按时间和 ID 稳定排序，房间终身最多准入 100 次，因此读取有界。

状态 `pending` 表示已预占但结果未确认，进程重启后不能假定未发送，更不能自动重放；`accepted` 仅表示原生发送入口接受；`uncertain` 需核查。迁移前的记录保留状态，未知的正文、成员、时间留空，不做推测回填。未获准的请求没有投递记录，当前只能看本次调用的失败结果。

该接口只记录发往成员的消息，不记录或推断模型回复归属。界面默认显示房间投递，其他原生会话历史在明确标注的折叠区域查看。它仍不是完整的房间消息时间线。

## 第七轮补充（2026-11-12）

- `POST /peer-rooms/{roomId}/bind` 接收 `{epoch}`，用户显式授予当前成员当前版本的工具访问；`GET /sessions/{sessionId}/peer-binding` 只读取仍有效的授权。暂停、成员变化和版本更新均使旧授权失效，读取不会续权。同一会话不自动转绑另一房间。
- 默认 MCP 工具由 AO_SESSION_ID 确定发送身份，再查询有效授权；模型不能自行填写发送者。旧 AO_PEER_* 固定环境模式仍保留其固定版本语义。
- `peer_tasks` 返回房间成员与任务，`peer_task` 新增 `propose`：使用新任务 ID、非空 goal、revision=0 创建开放任务；之后仍走 claim/submit/review，最多 100 项，不允许自我验收。
- `MessageDeliveryOptions.ClientMessageID` 贯穿发送入口和原生会话。房间投递键为 `peer:` 加房间 ID 与投递 ID 的 SHA-256；历史 API 返回同键用户消息所在轮次的助手消息，检查实际处理会话，排除回滚或迁移轮次。每项最多 100 条，超限 repliesTruncated=true。
- 群聊界面提供明确工具连接、任务面板、逐投递原生回复、原生授权入口与候选集成入口。任务面板创建待办不会替 Agent 认领，也不会直接唤醒模型。

## 可用成员发送与协作上下文（2026-11-12）

- 广播请求新增可选 `targets`（明确的非空、无重复房间成员子集）及 `connectPeers`（显式连接当前版本同伴工具）。不传 targets 保留全房间行为；空数组、外部成员、重复成员在授权与预占前拒绝。
- 桌面发送按钮展示接收人数，输入框上方展示接收人与跳过原因，只给本次可用成员发送，不自动恢复停止成员或补发旧消息。发送同时连接当前房间同伴工具，沿用当前项目权限、版本与预算限制。
- 实际原生输入追加房间、自身身份、接收人列表、工具说明和禁止循环/重放的提示；投递历史保留原始用户正文。模型互发仍必须经过 peer_send 的身份绑定与事务性准入，提示词不取代后台检查。
- 多个模型不共享隐式思维或完整私聊历史；通过明确同伴消息和任务记录交换信息。是否发起讨论仍由实际模型决定，不能把收到消息等同于自主完成整个项目。
