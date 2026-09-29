# Antigravity 验收权限：调查结论（2026-09-22）

## 已确认

- 官方 headless 文档说明：后台模式没有交互提示，需要通过权限策略提前允许操作；无法获批的命令会 soft-deny，甚至以 exit 0 返回。这与真实实验一致。
- 官方允许按 command(target) 配置规则，不需要 dangerously-skip-permissions。
- 官方文档描述的 CLI 配置位置是 ~/.gemini/antigravity-cli/settings.json。本机此文件不存在；不代表所有权限配置都不存在，也不能推断默认规则已适合自动验收。
- 规则优先级为 Deny > Ask > Allow。command 规则按前缀或逐 token 正则匹配，不是“恰好只允许一条命令”的天然保证。
- 文档列出的 CLI 规则是全局设置。尚未验证本机 1.2.8 是否支持独立的逐任务配置目录或动态审批协议。不能把全局配置写入包装成项目内授权。

## 决策

不自动创建全局 allow，不移除 ask/deny，不使用权限跳过开关，不通过其他工具重做已被拒绝的操作。现有“批准方案”不授予扩大提供方权限的授权。

需要选择下一步权限路线：
1. 用户通过 Antigravity 的交互权限界面批准本次必要操作；验证后再决定后台规则，适合先走通真实流程。
2. 单独实现项目级测试执行服务，执行用户核准的测试入口，留存被测版本、命令、输出；Antigravity 根据可核查证据审查。此方案改变验收分工，不得宣称为 Antigravity 亲自执行测试。运行测试代码本身有文件、网络、进程风险，必须配合真实隔离，不以命令白名单替代沙箱。

尚未验证的权限入口，不应提前开发成能工作的批准按钮。优先选择交互式单次授权做可行性验证；无人值守需后续独立设计。

来源：
- https://antigravity.google/docs/cli/headless/
- https://antigravity.google/docs/permissions?tab=cli
- https://antigravity.google/docs/cli/reference/
