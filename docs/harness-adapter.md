# DeepSeek Harness 本地接入

## 已核实协议

2026-10-03 对本机 Harness 0.2.0-rc.2 的 CLI 帮助及应用包内官方源码进行了只读检查，未调用真实模型：

- 入口：PATH 中 `dsh` 或 `/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh`。
- `dsh --profile headless --json -` 从标准输入接收任务，后台完成后退出；`--session-id` 恢复会话，目录必须与创建时相同。
- 事件：`session` 提供 `sessionId/cwd`；`status/turn_end` 的 `reason.kind=completed` 表示正常结束；`final.text` 是未截断的最终文本。
- `status/step_end.usage` 提供 Token 统计；缺失显示未知，不伪造零消耗。
- 最后一个 `--patch` 配置层指定沙箱和审批。Harness 的审批 `never` 是**拒绝需要提权的操作**，不是自动同意。

依据为安装包的 `@deepseek-ai/dsh-headless`、`dsh-base`、`dsh-sandbox-policy`、`dsh-user-approval`。它与另一个 `hermes` 命令不是同一产品。

## 使用

重启 WorkTogether，在“管理 Agent”找到 **DeepSeek Harness**，启用后加入新任务团队。默认关闭，避免突然加入旧任务广播。启用不调用模型，也不验证登录/配额。

适配器支持讨论、规划、认领、分析、编码和独立验收；实际分工仍根据能力与需求。凭据和模型选择沿用本机 Harness 正式配置，本项目不复制密钥。

## 权限边界

- CODE/FIX 为 `workspace-write`，其他为 `read-only`；不启用全权限。
- 任务内读写会话分开，避免验收恢复开发会话的历史权限。
- 禁用 worker 的子 Agent、工作流、目标循环、插件安装和模型生成标题；调度权留在平台。
- 作者不能验收自己；共享工作区写入与验收仍串行。
- 严格校验会话、目录、正常结束和唯一最终结果；缺失/重复 final 或错误结束均失败。
- 文件沙箱不是完整网络隔离；用户自定义 profile/plugins 仍需要信任审查。

## 验证范围

已做模拟 CLI 回归：三 Agent 讨论、Harness 编码、Codex 独立验收、会话恢复、读写隔离、异常流拦截；本机权限覆盖层通过隔离配置目录的无模型合成测试。

尚未调用真实 DeepSeek 模型、修改真实业务代码或运行真实项目测试。登录态、配额、工具权限及其他版本兼容性仍需受控试运行确认。模拟通过不代表真实账户已接通。
