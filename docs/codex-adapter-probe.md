# Codex adapter — evidence and remaining checks

初始只读核查日期：2026-09-14。后续真实测试已完成，结果见
`docs/handoff-runtime-v1.md`；下文保留初始探测范围记录。

实际 `command -v codex` 返回：
`/Applications/ChatGPT.app/Contents/Resources/codex`

实际 `codex exec --help` 退出码 0，列出：
- `exec` 非交互运行，支持从 stdin 输入。
- `-C/--cd` 指定工作目录。
- `--json` 输出 JSONL 事件。
- `--output-schema` 指定最终响应 JSON Schema。
- `-o/--output-last-message` 保存最终响应。
- `resume` 按会话 ID 恢复；平台不能使用 `--last` 选择任务。
- `--sandbox read-only|workspace-write|danger-full-access`。
- `--worktree`、`--ephemeral`、`--skip-git-repo-check`。

帮助同时输出 PATH alias 创建被拒的警告，帮助本身仍成功。

官方非交互文档已打开：
https://developers.openai.com/codex/noninteractive/
（当前跳转至 https://learn.chatgpt.com/docs/non-interactive-mode）。

未验证：真实模型调用、账号额度适用性、鉴权、精确 session 恢复、
图形界面已有任务附着、输出 Schema 与模型组合、超时终止行为。
本轮未调用付费模型或创建额外编码会话。适配器暂为 manual/mock。

Antigravity 的 PATH 探测由 doctor 提供，但命令存在与否均不证明其
自动化能力；其能力报告由 Antigravity 在独立 QA 目录提交。
