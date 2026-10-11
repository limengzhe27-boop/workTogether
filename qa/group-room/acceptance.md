# 项目群聊验收（v4）

日期：2026-10-11。范围：把"排队跟几个聊天助手说话"改造成真正的项目群聊；不改动方案审批、调度预算与手动交接通道。设计见 [DESIGN.md](../../DESIGN.md) 第 11 节，README 见"项目群聊"一节。

## 用户期望与本轮取舍

期望：几个客户端 Agent 同时在一个项目里与其对话，互相讨论、提问、派活、领活，并且能在项目里干活；"要不然我为什么要去选择某个项目"。

用户选定的三项取舍：

1. 选了项目就默认可写，Agent 可以直接动手。
2. 读/讨论全并行，写操作排队串行。
3. 默认最多 2 轮自动往返，可随时暂停。

## 改动对照

| 维度 | 改造前 | 改造后 |
| --- | --- | --- |
| 派发 | `ChatQueue` 每次只取一个 dispatch，1 秒轮询下一位，同一任务内严格串行 | 一轮内一次事务签发全部 dispatch，`ThreadPoolExecutor` 并行执行，每位成员独立 CLI 会话 |
| 同伴可见性 | 后发言者能看到前面所有回复 | 第 1 轮**不注入**任何同伴回复（真同时）；第 2 轮交付上一轮全部发言 |
| Agent 互发 | `proposals` 只是展示给人看的建议，平台从不执行 | `data.messages:[{to,intent,content}]` 经校验后成为真实群消息并触发下一轮 |
| 轮次 | `rounds=2` 把成员列表复制一遍（`[a,b,a,b]`） | `rounds` 是回合数，名单不变；回合完成自动进入下一轮 |
| 写入 | 每条消息手动勾选；默认只读 | 会话级 `allow_write` 默认开启；每轮最多一位写者，且跨任务检查同项目 |
| 中断 | 失败即整条消息 BLOCKED，跳过推进下一位 | 失败成员计入本轮 settled，其余成员结果保留；可显式重试或跳过 |
| 预算 | 无 | `max_auto_rounds`（默认 2）限制 Agent 发起的往返；超出进入 HELD，用户点"让他们继续"释放 |

## 自动检查

- `python3 -m unittest discover -s tests -q`：256 项通过。新增 `tests/core/test_group_chat.py`（11 项）：同时广播、并发执行（barrier 证明重叠）、Agent 提问/派活/认领、目标与意图校验、房间外成员拒绝、预算与释放、单一写者、跨任务写入串行、会话配置校验。
- 同步更新了编码旧串行语义的用例：`test_chat`、`test_chat_context`、`test_multi_agent_chat`、`test_loopback_openai`。
- `cd web && npm test`：12 项通过（对话输入框的引用文件/会话写入/轮次控件、无就绪成员提示、CLI 缺失标记、写入默认关闭）。
- `cd web && npm run build`：TypeScript 与 Vite 构建通过，发布包已重建。

## 真实浏览器与本机桩 CLI 端到端

用 `--codex-executable` / `--antigravity-executable` 指向 `tests/core/fake_*.py` 的包装脚本（`WT_FAKE_MODE=peer`），在临时项目与临时数据库上跑通：

1. 一条广播 → `[user -> both] DELIVERED round=1/1`，`codex` 与 `antigravity` 各一条回复，且都记录 `round=1`。
2. Agent 互发 → `[codex -> antigravity] intent=ask auto=True`，`antigravity` 对其作答；`auto_rounds_used` 递增。
3. 预算 → 用满 2 轮后新的跨 Agent 消息为 `HELD`，`chat.held=1`；界面显示"有 1 条 Agent 之间的消息停在自动轮次上限（已用 2/2 轮）。让他们继续"。
4. 界面 → Agent 消息渲染为「Codex → Antigravity · 提问」，被拦下的额外显示"等待你确认"；输入框下方"可在项目里工作"默认勾选。
5. `chat-release` 释放后消息回到 `QUEUED` 并被派发。

**全程未调用任何真实模型**：`codex` 与 `antigravity` 都指向本仓库的桩脚本。

## 边界

- 写入串行是平台内调度保证，不是文件系统锁：在编辑器里手动改同一个文件仍可能冲突；也没有隔离工作树与固定被测提交。
- 房间成员取"任务团队（若有）∩ 已启用且就绪的 Agent"；未就绪的 Agent 收不到派活。
- 预算按任务累计；释放会重置计数，这是显式的用户决定，不是自动延长。
- 第 1 轮真的互相不可见，因此成员之间可能出现重复或矛盾结论——这正是第 2 轮存在的意义。
- 并发上限：单任务一轮最多 `max_parallel_agents`（4）个 Agent 同时运行；跨任务最多 4 个任务。
