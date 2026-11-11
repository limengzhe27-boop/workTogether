# Agent Orchestrator 原生客户端验证

日期：2026-11-11。版本：官方 macOS arm64 v0.14.10。

## 决策
暂不迁移 WorkTogether，也不宣称完整协作可用。独立原生会话已验证；原生 Codex 内向 AO 本地服务发送同伴消息被权限拒绝，属于迁移前必须解决的阻碍。不得以跳过权限代替修复。

## 环境与证据
- 应用位于 `~/Applications/worktogether-evaluation/Agent Orchestrator.app`；codesign --verify --deep --strict 通过。
- AO_DATA_DIR 与 AO_RUN_FILE 都指向独立 evaluation 目录；未替换现有 WorkTogether 数据库。
- Codex login status 返回 Logged in using ChatGPT；没有新增 API Key。
- DeepSeek 使用现有应用内部 dsh，加入的 PATH 仅用于本次后台进程；AO 探测 authorized。其实际账户计费来源未独立核实。
- 独立空 Git 项目 wt-probe，使用本地 bare remote；没有上传或发布。无 remote 时 AO 返回 DEFAULT_BRANCH_UNRESOLVED，补本地默认分支后通过。
- wt-probe-1 / wt-probe-2：两个 Codex 原生会话，provider conversation ID 不同，工作目录不同；分别返回 READY-CODEX-1 / READY。
- wt-probe-3：DeepSeek Harness 原生 ACP 会话，返回 READY。
- 通过 AO send 要求 Codex-1 向 DeepSeek 发送一次测试消息。Codex 实际尝试后报告：连接 127.0.0.1:3001 被拒绝，operation not permitted。接收会话未出现 PEER-ACK-731。未重放、未放宽权限。
- 原始消息与会话记录保留在 evaluation/data/ao.db；不提交账号或原始数据库。
- AO 注入的系统说明约 16 KB，短消息也有上下文开销；尚无节省额度结论。

## 已验证与未验证
已验证：安装签名、原生客户端发现、三个独立会话实际回复、不同工作目录。
未验证：自动群聊广播、自主认领、同伴消息完整闭环、并行修改与集成、桌面 UI、其它项目完整无干扰、账户成本。
工作目录隔离不等于操作系统隔离；账号额度仍共享。

## 下一步
1. 对照当前版本源码检查 Codex app-server 的权限配置与 AO 消息传递入口。优先使用受支持的消息工具或仅针对本地 AO 服务的授权；不得启用全局 bypass。
2. 通道解决后重做一次有发送者身份的 Codex → DeepSeek 消息测试；保留明确停止条件。
3. 再验证两位 Codex 在独立工作目录各改一个小文件，以及第三成员的验收和集成。
4. 上述通过才比较 AO 原版与群聊目标的差距，决定直接使用或 fork；不同时维护两套执行调度器。

## 回归验证
本轮仅新增评估文档；后端 388 项、前端 18 项、生产构建均通过。构建未产生仓库差异。真实 Agent 只执行了连通与一次同伴发送探测，实际消耗未量化。
