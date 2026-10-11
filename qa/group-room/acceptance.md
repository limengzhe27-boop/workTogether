# 项目群聊验收（v4）

> 本文保留当时的真实与模拟验收证据。后续 P0-4 已收紧房间范围和授权：新会话默认只读，
> 后续派活限于发送时所选成员，单条只读与撤销写入均沿消息链生效。以下“默认可写”及
> 单独点名后向未选成员派活是历史行为，当前规则见 [群聊契约](../../docs/contracts/chat-v1.md)。

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

- `python3 -m unittest discover -s tests -q`：260 项通过。新增 `tests/core/test_group_chat.py`（15 项）：同时广播、并发执行（barrier 证明重叠）、Agent 提问/派活/认领、目标与意图校验、房间外成员拒绝、拒绝型回复不算正常回答、预算与释放、不可用同伴可跳过、会话配置严格校验、单一写者、跨任务写入串行。
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

## 自查发现并修复的缺陷

群聊上线后做了一轮对抗式自查，找到三个真实缺陷：

| 缺陷 | 影响 | 处理 |
| --- | --- | --- |
| Agent 明确回报 `status: BLOCKED`（例如“需要新授权，我做不了”）时，回复被记为 `ok:false` 但从未使用 | 消息被判为 **DELIVERED**，看起来像正常回答；`failed_roles` 为空、没有错误提示、界面不提供重试/跳过。只有任务整体变 BLOCKED，用户无从知道是谁拒绝了 | `_round_state` 把 `ok:false` 的回复计入失败；`submit` 用报告里的 `reason` 生成消息错误文案。现在显示 `codex: 需要新授权`，并可显式重试/跳过 |
| 被 `assign` 的 Agent 在派发前被停用（历史/导入数据） | 该消息永久停在 QUEUED，界面只有“查看详情”，没有任何出口 | 界面在 `AGENT_UNAVAILABLE`/`MEMBER_UNRESOLVED` 的 QUEUED 消息上也提供“跳过并继续” |
| `chat-config` 接受任意键 | 与其它接口的严格校验不一致，拼错的键会被静默忽略 | 只允许 `allow_write` 与 `max_auto_rounds` |
| `skip` 时失败角色列表可能重复 | 同一角色可能被追加两条“已跳过”占位 | 去重后再写入 |

另外补上了**回合分隔**：同一轮里第 1 轮回复标为“第 1 轮 · 各自独立回答”，第 2 轮标为“第 2 轮 · 看过同伴后回应”，这样“他们先各自表态、再互相回应”在界面上是看得见的。

## 真实模型端到端（2026-10-11，Codex 0.162 + Antigravity 1.2.8）

用 ChatGPT 桌面应用自带的 Codex CLI（已用 ChatGPT 账号登录）和本机 Antigravity 做了一次真实闭环，项目是临时目录里的一个小 Python 服务：

1. **项目上下文对真实模型生效**：问 Codex「这个项目用什么方式存数据？有什么硬性规矩？」，它从注入的 README 答出「普通 JSON 文件，不使用数据库」「改 JSON 结构必须提供迁移」。
2. **并行群聊**：一次广播给两位，各自独立作答，都指出 `tests/test_app.py` 的 `assertTrue(True)` 是恒真占位测试。
3. **真实第 2 轮互相回应**：Antigravity 回复「**我完全同意 codex 的意见**：当前项目最应优先改进的是补全真实的单元测试」——它读到的是同伴上一轮的实际发言。
4. **派活并真的改代码**：让只读的 Antigravity 去修占位测试，它分析后通过 `data.messages` 以 `assign` 把任务交给 Codex；平台生成 `[antigravity -> codex] intent=assign write=True`；Codex 拿到写入回合后改写 `tests/test_app.py` 并自己跑了 `python3 -m unittest`（Ran 2 tests, OK）。事后在磁盘上核对：`git diff` 确实改了该文件，测试独立复跑通过。

### 这一轮暴露并修掉的真实缺陷

| 缺陷 | 证据 | 处理 |
| --- | --- | --- |
| Codex 明明随 ChatGPT 应用安装，平台却报「未找到」 | PATH 无 `codex`，实际二进制在 `ChatGPT.app/Contents/Resources/codex-cli/`；`codex login status` 显示已用 ChatGPT 登录 | 新增 `resolve_codex_executable`：PATH 优先，其次探测应用自带副本，显式路径始终优先 |
| **Agent 之间的 `assign` 永远拿不到写权限** | 真实运行中两个 Agent 都回报「本轮只读，待取得写权限后实施」——`_record_agent_messages` 创建的消息没有 `allow_write`，`_pick_writer` 直接返回 None | 自动消息继承会话写入授权 |
| 平台会把写入回合授予根本不能写的适配器 | Antigravity 适配器固定 `--sandbox --mode plan`，却可能被选为唯一写者 | 新增 `AgentConfig.writable`；`_pick_writer` 只考虑「有 code 能力且适配器可写」的成员，否则不给写权限，而不是给一个用不了的授权 |
| Antigravity 在对话里执行 `ls` 被权限拒绝，整轮丢弃 | `denied_commands: ["ls -la …/proj"]`，33k token 白烧 | CHAT 提示明确要求使用只读文件工具（list_dir/view_file）、命令被拒就用手上能读到的内容继续；并且**命令被拒不再丢弃可用回答**，改为在回答开头如实标注「⚠️ 有命令被权限拒绝」，仅 VERIFY 保持严格失败 |

## 改动归因（2026-10-11 补）

真实运行暴露的体验缺口：Codex 真的改写了 `tests/test_app.py`，但群里只显示它的文字，用户必须自己去敲 `git diff` 才知道发生了什么。

现在每个会改动文件的回合（CODE/FIX，以及拿到写入的 CHAT）在运行前后各拍一次工作区快照（`workspace.file_digests`：路径 → 大小+修改时间+开头 4 KB 的摘要），差集写入该 dispatch，并在回复上呈现：`改了 N 个文件` 徽章 + 「本轮改动 · N 个文件」清单（M/A/D）。有变化才写 `WORKSPACE_CHANGED_BY_AGENT` 审计事件，没变化不写、不显示。

快照对**每一轮对话**都拍，而不只是有写入授权的那一轮：如果某一轮没有授权却仍然改了文件，同样会被记录并标出 `writable: false`，而不是悄悄放过——因为只读轮次的沙箱本该阻止写入。

验证：新增 `tests/core/test_change_attribution.py`（4 项，用会真的写文件的桩 CLI）；HTTP 与真实浏览器核对：拿到写入的 codex 显示 2 个文件（`M README.md`、`A fixture-wrote.txt`），只读的 antigravity 不显示徽章。

## 让三个 CLI 适配器都真正能写（2026-10-11）

用户要求"不只是 Antigravity，还有 DeepSeek"。排查后确认三个适配器此前都不认写入授权：

| 适配器 | 之前 | 现在 |
| --- | --- | --- |
| Antigravity | 硬编码 `--sandbox --mode plan`，永远只读 | 写入回合切到 `--mode accept-edits --dangerously-skip-permissions`（无人在场必须预授权工具请求）；只读回合保持 `--sandbox --mode plan` |
| DeepSeek Harness | CHAT 一律 `read-only`，且会话作用域固定 `:read` | 按授权决定 `sandbox-policy.mode`；`:write`/`:read` 作用域随授权变化，避免用只读会话去写 |
| 写入者选择 | 只挑"有 code 角色且可写"的成员，非编码成员被点名也拿不到写权限 | 多候选人时优先编码成员；**当本轮只点名一个可写成员时，由它持有**——`assign` 交接才有意义 |
| `AgentConfig.writable` | 只有 Codex/Harness | 三个本机 CLI 适配器都可写；OpenAI 兼容接口仍为否 |

真实模型验证（2026-10-11）：

- **DeepSeek Harness**：启用后问它项目用什么存数据，它**实际打开了 `notes.py`** 并指出"目前只有 `load(path)`，备份规矩还只是文档约定"；再让它创建 `IMPROVEMENTS.md`，它真的写了三条并回读确认。运行策略实测为 `sandbox mode: workspace-write`、`workspaceRoot` 指向项目、`approval: never`。用量：读 5098/733，写 1802/725（输入/输出）。
- **Codex**：此前已验证改写 `tests/test_app.py` 并自跑测试。
- **Antigravity**：写模式参数**已被 CLI 接受**（该轮改用 `view_file` 成功、不再出现 `ls` 权限拒绝），但真实验证被 **429 配额用尽**中断（`Individual quota reached … Resets in 2h10m`）。因此"Antigravity 能不能真写文件"目前是**未完成验证**，不是已证否；配额恢复后需重跑同一条指令。

## 边界

- 写入串行是平台内调度保证，不是文件系统锁：在编辑器里手动改同一个文件仍可能冲突；也没有隔离工作树与固定被测提交。
- 房间成员取"任务团队（若有）∩ 已启用且就绪的 Agent"；未就绪的 Agent 收不到派活。
- 预算按任务累计；释放会重置计数，这是显式的用户决定，不是自动延长。
- 第 1 轮真的互相不可见，因此成员之间可能出现重复或矛盾结论——这正是第 2 轮存在的意义。
- 并发上限：单任务一轮最多 `max_parallel_agents`（4）个 Agent 同时运行；跨任务最多 4 个任务。
