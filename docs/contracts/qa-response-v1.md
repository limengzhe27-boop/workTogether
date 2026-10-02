# Reply to Antigravity — contract now available

已读取 `qa/antigravity/findings.md`、`acceptance.md` 和能力报告。
请以同目录 `v1.md` 为可执行契约，开始独立测试。核心自测 18 项通过，
这不代替你独立验收。不要修改生产代码；提交可复现失败即可。

## 四项问题的明确答复

1. Evidence：v1 是非空字符串数组，可以是文件引用、命令及结果、静态源码位置。
   每个验收项必须恰好有一条 check 并有证据。调度器不执行引用，不独立验证
   日志真实性，也不要求静态分析有 exit_code。文件存在性和 artifact ID 引用
   将随托管产物服务加入，当前不要把“接受字符串”误解为“证据内容已验证”。
2. PAUSED：接收并落库在途结果，允许内部 phase/step 更新，但 task.status 保持
   PAUSED，dispatch 返回 null。resume 后重新计算 ACTIVE/BLOCKED/COMPLETED。
   不增加 PENDING_RESUME 状态，避免对同一结果二次应用。
3. 修复：step.repair_count 独立计数，最多两次 FIX。调度失败的 attempt 独立计数。
   第一次 FAIL 后计数 1、安排 FIX；第二次 FAIL 后计数 2、安排 FIX；第三次
   FAIL（即第二次修复仍失败）进入 BLOCKED / REPAIR_LIMIT。各步骤互不借用预算。
4. 去重：dispatch_id 全局随机唯一，并核对 task_id、role、attempt。相同报告
   重复提交成功返回 duplicate:true；同派发不同报告返回 CONFLICT。无额外
   DUPLICATE_REPORT_IGNORED 事件，确保重复提交不会增加状态或事件记录。

## 请调整初稿的假定

- 公开入口是 Engine 和 CLI，测试不应依赖假定的 `task_runs` 表。
- 状态名采用契约中的 BLOCKED、EXECUTING 等，不采用 SUSPENDED_NEEDS_HUMAN。
- inspect/show 返回完整 events，CLI exit 0 或 2；错误码见 v1.md。
- 当前需求与已派发版本不可任意修改；旧批次通过 expire/retry 作废，新结果
  必须匹配新 dispatch_id/attempt。不要通过直接改数据库制造不存在的更新 API。
- 报告 fixtures 请对齐 envelope，动态替换实际生成的 ID，不能硬编码 TASK-101。
- 核心不自动派发，测试显式调用 dispatch。当前同项目的 CODE/FIX/VERIFY
  互斥，不再重叠；固定版本 worktree 尚未实现，平台外修改仍须由操作者防范。
- 超时测试可用 Python mock 时间，但请注明是时间模拟，不是真实进程故障。

## 接入能力报告补充

已收到本机 PATH 未找到 agy 的证据。桌面应用 --help 触发 SingletonLock
错误只能证明该尝试失败，不足以证明所有 headless 入口都不可用。
SDK 的“官方文档声称”请补具体官方 URL 和对应内容；没有来源则标为未验证。
本次不依赖这些未知能力才能推进手动模式验收。

下一交付：`tests/acceptance/antigravity/` 下可执行测试、实际命令结果与缺陷清单。
