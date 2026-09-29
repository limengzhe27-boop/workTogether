# Runtime first integration delivery

日期：2026-09-14。新增 `worktogether/runtime.py` 和 CLI run/runs/collect/
resolve-stopped。规范：`docs/contracts/runtime-v1.md`。

## 实际接入证据

1. 首次在应用沙箱内执行 Codex 失败：Codex 自身 state DB 只读、app-server
   初始化不允许。随后通过权限审批，在沙箱外启动 Codex，内部仍指定 read-only。
2. 临时测试工作区 `/tmp/worktogether-codex-probe.ZbcJDK`，不修改业务文件。
3. 首次真实调用退出 0，返回 WT_PROBE_OK，session：
   `01a09bb9-392e-7951-9078-e0fe197fc533`。
4. 使用同一明确 session ID 恢复，要求回忆前一次回复；退出 0，返回
   WT_PROBE_OK，stream session ID 相同。未使用 --last。
5. 平台 `run` 真实规划派发成功：
   task `d481af0fe9d24e9da48fc86e0975d9f9`，
   dispatch `3f1b658b19144e9da24e24b5d9735ab3`，
   run `cf5fcac5446a49dcb234782a3958f815`，
   session `01a09bba-56c8-78d0-b357-16aaccb75aa1`。
   退出 0，run COMPLETED，核心已接收 PLAN 报告，下一阶段 PLAN_REVIEW。
   原始 prompt/result/events/stderr 在临时目录的 runs/RUN_ID 下。

用量原始值：首次短回复 input 16216 / cached 7552 / output 8；恢复回复
input 32467 / cached 14080 / output 16；平台规划 input 17918 / cached 8448 /
output 152。恢复后的数字可能为累计值，尚未证明可按调用相加，也不代表账单。
这说明当前配置的上下文开销值得后续测量，不能承诺特定节省比例。

官方来源：https://learn.chatgpt.com/docs/non-interactive-mode

## Antigravity 接入

已打开 Google 官方 Codelab：
https://codelabs.developers.google.com/antigravity-cli-hands-on

该教程明确列出独立 agy CLI 的 macOS/Linux 安装入口及 Google OAuth 登录。
因此不能从桌面应用单例锁错误推断没有 CLI。当前本机未安装/发现可用入口；
本轮未安装 CLI、未代用户登录、未接受条款。教程只建立 CLI 存在性，
不证明本机 headless、指定 session 恢复与订阅自动继承已经跑通。

## 验证与下一交接

- 核心和 runtime 自测新增真实子进程 fixture，验证超时、错误输出、重复启动、
  进程未知状态、会话不符和崩溃后重复收集。
- Antigravity 原 11 项独立核心验收已由 Codex 复跑通过。
- 请 Antigravity 按 runtime-v1 增加进程层验收；不要把这些 fixture 测试写成
  “真实 Codex/Gemini 模型测试”。
- 未完成：Antigravity 自动接入、后台常驻调度、worktree、浏览器界面、
  自动测试环境和用量差分统计。
