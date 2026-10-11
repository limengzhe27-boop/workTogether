# 项目上下文与工作台增强验收（v3.1）

日期：2026-10-11。范围：修复 `--db`、补齐项目上下文/记忆/变更/Agent 档案的后端与界面接入；不改变调度状态机、审批语义与调度预算。设计见 [DESIGN.md](../../DESIGN.md) 第 10 节。

## 背景（为什么改）

用户在自有项目里问团队“你们知道这个项目是做什么的嘛”。Codex 回答“目前还不知道这个项目的具体用途。现有上下文只有项目目录名 workTogether”。原因是 CHAT 派发没有携带任何项目信息，只做对话的 Agent 无从了解仓库。同一批未提交改动已经写好了项目记忆、文件读取、diff/还原、Agent 档案等后端能力，但界面完全无法触达，且存在缺陷。

## 修复的缺陷

| 缺陷 | 影响 | 处理 |
| --- | --- | --- |
| `web` 子命令重复定义 `--db` | `python3 -m worktogether --db /tmp/x web` 静默改用 `.collab/state.sqlite3`，可能污染真实状态库 | 子命令改为 `argparse.SUPPRESS`，并新增 `tests/core/test_cli_args.py` |
| 缺少 `GET /api/projects/{id}/memory` | 客户端读取项目记忆固定 404 | 新增 GET 路由；POST 支持 `append` |
| `/files` 返回字符串数组 | 与前端 `ProjectFileEntry` 类型不符，无法显示大小 | 新增 `project_files_snapshot`，返回 `{path,size}`、`total`、`truncated` |
| `revert-file`、`delete-agent`、`delete-project`、`delete-task` 返回结构与客户端类型不一致 | 界面读取不到字段 | 统一为 `{reverted,path,mode}`、`{deleted,agent_id}`、`{deleted,project_id,tasks_removed}`、`{deleted,task_id}` |
| CHAT 派发不携带项目上下文 | Agent 无法回答项目相关问题 | 每次 CHAT 都附带有界 `project_context`（README 前 3000 字、最多 120 个文件路径） |
| 新增后端能力无测试 | 回归风险 | 新增 68 项测试（169 → 237） |

## 自动检查

- `python3 -m unittest discover -s tests -q`：237 项通过。新增覆盖：工作区上下文与还原安全、项目记忆与级联删除、Agent 克隆/密钥/档案、聊天上下文与报告归一化、HTTP 路由、CLI 参数。
- `cd web && npm test`：10 项通过（新增 4 项：对话输入框的引用文件/允许修改/轮次控件存在、写入默认关闭、无就绪成员时不给死掉的发送按钮、TaskChat 可服务端渲染）。
- `cd web && npm run build`：TypeScript 与 Vite 构建通过，发布包已重建并提交到 `worktogether/static/`。

## 真实浏览器检查（1046×653，临时数据库，端口 8799）

- 添加本地项目（指向本仓库）成功，首页出现“项目上下文”入口与项目路径。
- 项目上下文弹窗：显示 29 个未提交变更文件与 116 个项目文件；统一 diff 可折叠；记忆保存后经 `GET /api/projects/{id}/memory` 回读一致。
- Agent 设置：内置 Agent 仅显示“编辑档案”，自定义 API Agent 额外显示“移除”；注册表单提供“OpenAI 兼容接口 / 克隆本机 CLI”两种接入类型，并含系统提示与密钥输入；编辑 Codex 系统提示并保存后卡片显示提示摘要。
- 对话界面：消息携带 `context_files` 时用户气泡显示“1 个文件”标记；文件选择器列出 116 个文件、选中 README.md 后出现可移除 chip。
- 回复中断恢复：`codex` 不在 PATH 时该条消息进入 BLOCKED，界面显示“重试该 Agent / 跳过并继续下一位”；点击跳过后消息结束，仅保留“（已跳过该 Agent 的本次回复）”，无自动重放、无真实模型调用。

## 边界

- 本轮没有触发任何真实模型调用：`agy` 可用但未参与，`codex` 缺失是刻意使用的失败路径。
- 项目记忆、`@引用文件`、“允许修改”都不构成版本隔离；还原已跟踪文件会丢弃当前改动，删除未跟踪文件不可恢复。
- 项目上下文有明确上限（README 3000 字、文件 120 个、单次引用 10 个文件 / 12 KB / 48 KB），超出部分不发送。
- 删除项目只清理平台记录，不删除用户的代码文件。
- 旧静态资源已随重建移除；运行中的服务需要重启才会加载新代码与前端包。
