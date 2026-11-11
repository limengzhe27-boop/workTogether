# 原生团队实验契约

这是独立 AO 实验分支的后台协议，不是 WorkTogether 现有 Python 服务的新接口，也不是已经完成的群聊产品。

## 构建与保留

上游固定 v0.14.10 / 033d597032e448a0ea16c298350e36b099f74106。
依次应用 `ao-scoped-peer-mcp.patch` 以及本目录 experiments 下三个编号补丁。当前源码已位于 `~/Applications/worktogether-evaluation/source`，分支 `codex/scoped-peer-mcp`。
Go 1.27.1，在 backend 目录执行 `go build -o /absolute/evaluation/ao-room ./cmd/ao`。不替换签名应用内二进制，不覆盖用户真实 ~/.ao 数据。所有测试使用独立 AO_DATA_DIR 和 AO_RUN_FILE。

## 房间与消息

本机后台 `/api/v1` 下：

| 方法与路径 | 含义 |
| --- | --- |
| GET /peer-rooms/{roomId} | 读取成员、epoch、预算与已用次数 |
| PUT /peer-rooms/{roomId} | 用旧 epoch 做比较更新；首次 epoch=0；每次成功递增 |
| POST /peer-rooms/{roomId}/send | 事务性预占预算后投递，失败/不确定不重放 |
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

AO 本机管理 API 继承其可信本地进程模型；本实验不把它宣传成抵御恶意本机进程的认证层。房间门禁尚未覆盖原有 send/steer 通道。普通原生终端的能力保持原配置，不能把工作目录隔离说成整个操作系统隔离。

当前没有房间 UI、自动拆分/依赖调度、统一广播入口或候选分支集成测试调度。不要把可调用后台 API 当作完整产品交付。正式迁移前须补齐这些入口和统一授权语义。
