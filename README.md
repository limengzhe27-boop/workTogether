# WorkTogether

本地多 Agent 任务对话与协同研发原型，包含调度核心、命令行及 React 网页工作台。

当前支持有限轮规划讨论、方案审批、自动开发与验收调度（每轮最多 6 次调用、最多两次修复）、任务内消息和保守的异常恢复。隔离临时项目已完成一次真实 Codex 编写文件 → Antigravity 只读文件验收的端到端试运行；第一次验收因终端权限被拒绝，改用只读文件工具后才通过。需要运行测试命令的真实项目仍可能受权限限制，尚不能声称全面无人值守。方案审批不会自动授予 Agent 的工具权限。

新团队任务还可在“详情”选择“授权团队连续执行”：一次明确授权后自动完成讨论、认领、定稿与执行/验收，总预算为成员数 × 2 + 15 次调用；不再要求定稿后逐步点击。普通讨论模式仍需审阅批准。自动作业只适用于当前需求与团队，暂停、新消息、失败或预算耗尽会停止。该入口已通过真实浏览器操作的模拟 Agent 闭环验收，不代表真实模型权限已验证。

新建任务页也可勾选“创建后授权团队讨论、分工并执行”，选择成员后一次提交启动流程；默认只创建、不调用模型。需求修订后可重新组队和授权，旧消息不会自动带入新需求。调用预算统计平台启动 Agent 的次数，不是对 Agent 内部模型请求或 Token 数量的硬上限。

模型调用需要安装并登录相应 CLI；可通过 `--codex-executable` 和 `--antigravity-executable` 显式指定路径。启动时会自动探测，找不到才报缺失：

- **Codex**：PATH 里没有 `codex` 时，会尝试 macOS 上 ChatGPT 桌面应用自带的 Codex CLI（`/Applications/ChatGPT.app/Contents/Resources/codex-cli/…`）。有 ChatGPT 订阅并登录过，就能直接用，不必额外安装。显式传入的路径始终优先。
- **Antigravity**：找不到 PATH 中的 `agy` 时，会使用本项目私有目录中的 Antigravity 1.2.8 可执行文件。

自动探测不是安装或登录。工作台会显示启动时探测到的实际路径，找不到的 CLI 会在 Agent 卡片和对话成员上明确标注。当前流程以 [自动执行说明](docs/execution-v1.md) 为准。本地凭据、运行数据库和原始实验记录不随仓库发布。

### 多 Agent 对话（当前边界）

已加入本机 **DeepSeek Harness 0.2.0-rc.2** 后台适配器。在 Agent 管理页主动启用，再加入任务团队；它不同于 DeepSeek API 模板。已通过无模型配置探测和模拟三 Agent 编码/验收测试，真实模型登录与权限尚未验收，见 [Harness 接入说明](docs/harness-adapter.md)。

新任务可先在右上角“团队”中选择成员并保存，随后点击“讨论方案”：成员并行提供首轮意见 → 协调者拆任务 → 成员并行申报可承担的步骤 → 独立方案复核 → 定稿。每位成员最多两次发言，再加三次规划调用；这不是无限群聊。协调者不再固定为 Codex，执行者与验收者也按适配器实际支持的能力选择。未指定负责人时优先匹配有效认领，其次平衡当前计划负载；最终分工及理由会显示在方案中，仍须用户批准后执行。

并行不只用于只读讨论：群聊的讨论与分析阶段完全并行，写操作在项目内串行排队（见上）。代码修改与验收的项目级互斥、以及“方案批准后才自动执行”的开发通道保持不变；尚无隔离工作树，API Agent 不能读取本地仓库、改代码或执行测试。CLI 配置“就绪”并不证明登录和权限可用——界面会依据启动时的探测结果明确标出本机找不到的 CLI。以上流程已通过模拟 CLI 的并行、互发消息、预算与写入串行回归，尚不是三种真实模型的端到端验收。协议见 [动态团队说明](docs/team-workflow.md)。

每位 Agent 可在任务对话里提出最多三条结构化分工建议，也可以直接给同伴发消息（提问/派活/认领/汇报）。建议会进入后续规划上下文；跨 Agent 的消息会真实投递并触发下一轮，但仍受房间成员、能力、依赖与轮次预算校验，不能绕过你的方案批准去做计划内开发。

### 项目群聊：多位 Agent 同时在场

进入一个任务就是一个**群聊房间**。你可以只叫一位，也可以把 Codex、Antigravity、Harness 等一起拉进来：选中成员后发出的一句话会在**同一轮内并行广播**给所有人，每人一个独立会话进程，各自同时作答，且**第 1 轮互相看不到对方的回复**——这是真正的同时，不是排队。

- **第 2 轮才互相回应**：勾选“轮次 2”后，第 1 轮全部完成后自动开第 2 轮，这时每位成员都拿到第 1 轮所有人的完整发言，可以同意、反对、追问或接手。
- **Agent 之间可以直接说话**：Agent 的回复里可以带 `data.messages`，`intent` 为 `ask`（提问）/`assign`（派活）/`claim`（认领）/`report`（汇报）。平台校验目标在本房间内、能力与预算之后，生成一条真实的群消息（形如 `Codex → Antigravity · 提问`）并触发下一轮。你不需要替它们传话。
- **有界，不会自己刷屏**：Agent 发起的往返默认最多 **2 轮**（会话配置可改）。超出后新的跨 Agent 消息进入“等待你确认”，界面显示“有 N 条…停在自动轮次上限”，点“让他们继续”才送达。随时可暂停。
- **默认可以在项目里干活**：新建会话默认开启“可在项目里工作”，Agent 可直接读写你选中的项目。**同一时刻一个项目只有一个写入者**，并且只有**真正能写的适配器**才会拿到写权限：Codex（含 Harness）可以写，Antigravity 适配器始终以只读 plan 模式运行、OpenAI 兼容 Agent 没有文件系统权限，因此它们永远不会被授予一个用不了的写入回合——需要改动时它们会用 `assign` 把活交给能写的同伴，那条消息会继承本会话的写入授权。其余成员并行只读。可在输入框下方随时关掉，回到纯只读讨论。
- **状态可见**：每一轮都显示谁正在回复、谁在等待、谁持有项目写入。
- 消息、会话、团队按任务隔离；不同任务之间不共享上下文。选用第三方云端 Agent 时，本任务需求、消息与必要上下文会发往其配置的接口，请勿放入不宜共享的敏感信息。

**边界**：写入串行依赖平台内的调度，不是文件系统锁——你在编辑器里手动改同一个文件仍可能冲突；尚无隔离工作树与固定被测提交。API Agent 仍不能读仓库或运行测试。

除内置 CLI 外，自定义 Agent 支持 **OpenAI 兼容的 `/chat/completions` 接口**，可规划、复核方案、对话和接受已批准计划中的只读分析工作（`work_kind: "analysis"`），也可**克隆本机 CLI**（`base_agent` 为 codex/antigravity/deepseek-harness）以获得对应的编码或验收能力。规划阶段收到讨论摘要与可用能力清单；分析产物必须经另一位具有验收能力的 Agent 验证才解锁依赖步骤。API Agent 只能分析传入材料，不能自动读仓库、修改代码或运行测试；配置显示“就绪”也不代表接口已实际连通。任意 CLI 尚无统一调用协议，不能仅填写名字就接入。预置 DeepSeek API 模板**不是 DeepSeek Harness 本地接入**。密钥优先读取服务环境变量（例如 `DEEPSEEK_API_KEY`），其次读取状态库同目录的 `secrets.json`（可选，权限 600）；两者都不写入注册数据库。本机回环地址可无密钥。未就绪 Agent 不参与广播。启动命令里的 `--db` 放在子命令前后都生效。历史数据库若曾直接存过 API Key，建议轮换密钥；`secrets.json` 以明文保存，请勿提交到版本库。

分工格式例子：`{"id":"research","title":"梳理接口边界","work_kind":"analysis","assignee":"deepseek-hermes","acceptance":["列出可核查的接口约束"],"depends_on":[]}`。后续编码步骤用 `"work_kind":"code"`、`"assignee":"codex"`，并在 `depends_on` 引用 `research`。Agent 可以提出分工，但平台会校验能力、依赖和就绪状态；你批准最终计划后才会运行工作项。一次自动执行仍有调用次数上限，且不会替你批准模型工具权限。

设计来源、完整流转与尚未支持的边界见 [多 Agent 分工说明](docs/multi-agent-delegation.md)。

Agent 管理页可由你主动点击“测试连接”：平台仅发送一条不含任务或代码的短请求，并检查是否返回可用的聊天响应；可能产生少量模型用量，不会在注册时自动触发。连接测试通过不等于分析能力、任务协议或实际开发流程已经验收。

后台普通消息可跨任务并行处理（最多四个任务），单任务仍按序发言且同一会话不会同时启动两次。停用的 Agent 不再由旧规划流程继续派发；有在途或未完成职责时禁止停用。研发团队会在讨论前检查是否存在可执行工作与独立验收的组合，避免纯聊天接口团队花完讨论额度后才发现无法验收。

同项目的旧聊天失败不会占用其他任务的 Agent 会话；旧失败不会自动重试。同任务的未知运行仍需先处理，代码修改与验收的项目级互斥也保持不变。消息处于排队状态时，页面会显示暂停、未解决的运行、前序消息或成员未就绪等具体原因。

如果看到两个 DeepSeek 条目：`DeepSeek API（云端接口）` 是使用 API 的讨论/分析配置，`DeepSeek Harness（本机 CLI）` 才是本机应用接入。本机 CLI 不等于离线模型。早期 API 模板曾误叫 `DeepSeek Hermes`，启动时仅纠正已知默认名称，保留旧 ID `deepseek-hermes` 以兼容历史消息；不改密钥、接口和启用状态，也不覆盖用户自定义名称。

通过注册表移除 API Agent 后，默认条目不会在重启时重新出现；仍可主动重新注册。显式选择取消待发消息时，只取消尚未启动、只发给该 Agent 的消息，保留历史和审计记录；运行中、多人消息或未完成工作会阻止移除，且整次操作回滚。被移除 Agent 的本机密钥会一并从 `secrets.json` 删除。

对话中某个 Agent 回复中断时，消息会显示失败原因和两个显式入口：“重试该 Agent”重新入队当前发言，“跳过并继续下一位”记录一条跳过说明后推进到下一位成员。两者都不会自动重放，也不会因为超时或权限拒绝而自行继续。

任务与项目可在“详情”与侧栏中重命名或删除；删除项目会级联清理其任务、消息与事件记录，存在未结束的模型进程时会拒绝删除。这些操作保留审计边界，不做静默级联以外的推测。

自定义 API Agent 的讨论与只读分析分工已通过本机假服务的真实 HTTP 闭环测试：三位 Agent 在同一任务中依次回复，API Agent 接收前序摘要；批准计划后它完成分析，Antigravity 验收，依赖步骤再交给 Codex。测试使用模拟 CLI 和假 HTTP 响应，**尚未**用真实 DeepSeek/Ollama 服务完成端到端试运行。Antigravity 对需要命令行工具权限的验收仍可能被阻断。并行修改代码、Claude Code/ZCode 编码适配器及隔离 worktree 尚未实现。

## 快速启动 Web 工作台

Python 3.9+，运行 Web 工作台与调度核心无需安装第三方依赖（开箱即用）：

```bash
# 启动本地 Web 服务器（默认端口 8765，绑定 127.0.0.1）
python3 -m worktogether web --port 8765 --db .collab/state.sqlite3
```

启动后在浏览器打开：**http://127.0.0.1:8765/**

### Web 工作台特性

- 参照 Codex 工作界面的深色布局：顶部工作栏、窄工具栏、项目任务树、宽对话区；蓝色用户气泡和底部大圆角输入框。保留 WorkTogether 品牌和原协作功能，不复制无功能的语音、上传或系统窗控。
- 项目侧栏可收起，任务支持当前项目内搜索；窄屏使用项目抽屉，并保留 Agent 设置入口。右上角“任务详情”切换视图时保留消息草稿，⌘/Ctrl + Enter 可发送。
- Agent 回复支持 Markdown 标题、列表、链接、代码块和复制；不执行原始 HTML、不自动加载回复中的远程图片。输入框的“＋”打开任务团队，成员选择用于决定消息接收者，不冒充模型切换。
- **项目上下文**：每次对话派发都会自动附带该项目的名称、路径、README 摘要（默认前 3000 字）和文件列表（默认前 120 个），因此只做对话的 Agent 也能回答“这个项目是做什么的”，而不是猜测。首页与任务详情都有“项目上下文”入口。
- **项目记忆**：可在“项目上下文”里写入本项目长期约定（技术栈、命令、禁区），它会随每次对话发送；内容保存在本机数据库，不写入你的代码仓库。
- **引用文件与写入授权**：对话输入框可「引用文件」（最多 10 个，单文件 12 KB、总计 48 KB 上限）作为本条消息的上下文；勾选「允许修改」后，本条消息的接收者才可创建或修改项目文件。两者默认关闭，且不会绕过各自 CLI 的沙箱与你的确认。
- **变更查看与还原**：项目上下文里显示 Git 工作区未提交的改动清单与统一 diff，可逐文件还原（已跟踪文件恢复到 HEAD，未跟踪文件会被删除，需二次确认）。这不是版本隔离。
- **Agent 档案**：可编辑内置与自定义 Agent 的名称、描述、系统提示；自定义 Agent 支持克隆本机 CLI（继承相应编码/验收能力）或接 OpenAI 兼容接口，并可选择把 API Key 存到本机 `secrets.json`（权限 600，环境变量优先，不写入注册数据库）。
- 顶部只显示当前阶段操作：“讨论方案”→“审阅方案”→“开始执行”。普通消息只交流，不自动授权开发；模型调用前的确认保留调用上限与额度说明。
- “详情”集中放置手动交接、修改需求、暂停、连续执行授权、重试恢复、历史报告与日志；“Agent 设置”管理接入，高级 API 注册默认收起。当前阻塞仍有醒目处理入口。设计见 [DESIGN.md](DESIGN.md)。
- 多项目和任务列表、任务内有限轮对话、规划、方案批准、后台自动执行以及状态轮询。
- 手动模式可在页面派发下一项、复制 JSON 交接包、粘贴报告并由后端校验身份、版本和字段格式；验收权限受阻后可检查失败记录、显式重试，再改用手动交接。平台不能证明手填报告的证据真实存在，提交者必须自行核对。
- 页面展示任务步骤、报告和事件；执行详情按提供方汇总真实返回的 Token 字段（不推算费用或节省比例），并展示运行记录。点击某次运行可按需查看指令、结果和日志；仅允许五种已知文件，较大文件只展示开头或末尾 512 KB，不自动加载完整日志。产物可能包含任务内容和本机路径。项目 Doctor 可通过命令行或项目详情 API 查看。
- 暂停仅阻止后续派发，不会结束正在运行的模型；过期、未知运行和权限拒绝不会自动重放。
- 在没有固定版本工作区之前，同一项目的代码修改与验收互斥；另一个任务占用项目时，界面会说明为什么暂不能派发。不同项目仍可独立推进。
- 在 Git 项目的自动 CODE/FIX → VERIFY 流程中，平台会记录交付时 Git 可见文件的内容摘要，并在验收前、验收后及报告入库前检查是否变化；变化则阻止接受验收。这是漂移检测，**不是不可变版本隔离**：手动交接、非 Git 项目、Git 忽略文件、符号链接的外部目标及检查之间的竞态不在保护范围内。
- 服务只绑定本地回环地址，运行产物接口限制文件名；自定义 API Agent 是否真的可连接、是否具有有效凭据，须在实际调用中确认。

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

工作台内置预构建静态包（位于 `worktogether/static/`，使用时无需安装 Node 依赖）。若需基于 TypeScript / React 源码调试前端：

```bash
cd web
npm install
npm run dev
```

前端渲染与安全回归运行 `npm test`；发布前运行 `npm run build`，并一起提交更新的 `worktogether/static/`。本次 Codex 截图对齐的验证范围见 [界面验收记录](qa/ui-simplification/codex-reference.md)。

## 开发交接与文档

- 架构设计与 UI 决策：[DESIGN.md](DESIGN.md)
- 接口契约：[docs/contracts/v1.md](docs/contracts/v1.md)
- 运行时契约：[docs/contracts/runtime-v1.md](docs/contracts/runtime-v1.md)
- Web 工作台交付报告：[docs/handoff-web-v1.md](docs/handoff-web-v1.md)
- 项目上下文与工作台增强验收：[qa/project-context/acceptance.md](qa/project-context/acceptance.md)
- 项目群聊验收：[qa/group-room/acceptance.md](qa/group-room/acceptance.md)
- 界面验收记录：[qa/ui-simplification/codex-reference.md](qa/ui-simplification/codex-reference.md)
- 演进路线：[docs/roadmap.md](docs/roadmap.md)
