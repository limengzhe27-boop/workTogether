# Antigravity 独立黑盒测试结果报告 (Phase 1 Acceptance Results)

- **执行时间**: 2026-09-14 00:55
- **测试套件**: `tests/acceptance/antigravity/test_acceptance.py`
- **被测对象**: `worktogether.core.Engine` 及 `python3 -m worktogether` (v1 契约)
- **运行环境**: Python 3.12 (原生标准库 unittest，独立临时 SQLite 数据库)

---

## 一、 执行命令与汇总数据

```bash
$ python3 -m unittest discover -s tests/acceptance/antigravity -v
```

### 测试汇总
- **总执行数**: 11
- **通过数 (PASSED)**: 11
- **失败数 (FAILED)**: 0
- **错误数 (ERRORS)**: 0
- **执行总耗时**: 0.549s
- **代码修改边界**: 仅在 `qa/antigravity/` 及 `tests/acceptance/antigravity/` 交付，**未修改任何生产模块代码**。

---

## 二、 场景覆盖与可观察行为验证清单

| 场景编号 | 对应测试方法 | 验证的核心契约与可观察行为 | 测试结论 |
| :--- | :--- | :--- | :--- |
| `ACC-001` | `test_acc_001_normal_lifecycle` | 完整端到端生命周期：`PLANNING` -> `PLAN_REVIEW` (限 1 轮) -> `PLAN_FINALIZE` -> `EXECUTING` (CODE) -> `VERIFYING` -> `COMPLETED`。 | **PASS** |
| `ACC-002` | `test_acc_002_verification_fail_and_fix` | 首次验证 FAIL 正确将步骤置为 `FIXING` (`repair_count=1`)；派发 `FIX` 任务并要求提交新版本；新版本复验通过后成功标记 `COMPLETED`。 | **PASS** |
| `ACC-003` | `test_acc_003_repair_limit_blocks` | 连续两次修复均失败后（累计 3 次 FAIL），步骤与任务熔断变更为 `BLOCKED`，记录 `REPAIR_LIMIT`；`dispatch` 返回 null；`retry` 被明确拒绝抛出 `INVALID_STATE`。 | **PASS** |
| `ACC-004` | `test_acc_004_duplicate_and_conflict` | 相同报告重复提交返回 `{"accepted": true, "duplicate": true}`，且不新增事件或改变状态；同派发不同内容的提交被拦截抛出 `CONFLICT`。 | **PASS** |
| `ACC-005` | `test_acc_005_stale_dispatch_and_version_mismatch` | 验证报告的版本与派发版本不一致时抛出 `STALE_REPORT`；attempt 序号不一致时抛出 `STALE_REPORT`，拒绝推进。 | **PASS** |
| `ACC-006` | `test_acc_006_project_and_task_isolation` | 不同项目的任务独立推进；将 Task A 的派发结果提交给 Task B 被安全拦截（`NOT_FOUND` 或 `INVALID_REPORT`），无状态窜扰。 | **PASS** |
| `ACC-007` | `test_acc_007_pause_accepts_inflight_and_resumes` | 暂停后 `dispatch` 立即返回 null；在途结果允许正常落库并推进步骤，但任务主状态严格保持 `PAUSED`；恢复后任务正确重算状态并继续派发。 | **PASS** |
| `ACC-008` | `test_acc_008_process_restart_crash_recovery` | 新创建的独立 `Engine` 实例指向同一 SQLite 库，能完全恢复任务、步骤与时序事件，且不会重复派发已处于 `ACTIVE` 的外部任务。 | **PASS** |
| `ACC-009` | `test_acc_009_invalid_input_and_state_invariance` | 缺少 evidence、缺少或多出 acceptance 验收项、包含未定义字段的报告均被拦截；**断言每次拦截后数据库状态、事件总数与失败前严格完全一致（事务零副作用回滚）**；`BLOCKED` 报告安全进入阻塞态而非被误判为通过。 | **PASS** |
| `ACC-010` | `test_acc_010_dependencies_and_pipeline_overlap` | 存在依赖的步骤在先决步骤 `COMPLETED` 前无法被派发；无依赖独立步骤允许同时处于 Codex `CODING` 与 Antigravity `VERIFYING` 的流水线重叠态。 | **PASS** |
| `CLI-001` | `test_acc_cli_invocations` | 通过标准子进程调用 `python3 -m worktogether --db ...` 验证 `doctor`, `task-add`, `show`, `pause`, `resume`，标准输出 JSON 解析正常，退出码严格符合契约。 | **PASS** |

---

## 三、 发现的缺陷与契约确认项 (Defects & Clarifications)

1. **生产代码缺陷**: **0 项**。
   - Codex 实现的 `worktogether.core.Engine` 核心状态机实现极其严谨，事务边界与防御性校验全部符合 `v1.md` 契约，11 项黑盒测试全部一次性通过。
2. **测试过程中的契约确认**:
   - 确认了 `dispatch()` 发出的新 `FIX` 派发是独立的工单（其 `attempt` 为 1，步骤修复轮次通过 `step.repair_count` 跟踪）；而 `retry()` 是针对已 `BLOCKED` 的同一工单递增 `attempt`。验收用例已对齐该定义。
   - 确认了 `doctor` 返回的 JSON 顶层键为 `{"project": {"id": ...}, "git": ...}`，验收用例已完全匹配。

---

## 四、 当前阶段未覆盖项与限制说明 (Uncovered Items)

依据交付边界，以下内容为后续阶段里程碑，本轮黑盒测试显式声明未覆盖：
1. **真实模型 API 连通性**: 本测试套件验证的是调度内核的确定性状态转移与契约完整性，**测试通过不代表真实大模型联网/会话恢复已跑通**。
2. **物理工作区隔离 (Git worktree)**: v1 核心不自动建立 worktree，代码版本由上层调用方传入静态标识（如 commit SHA / 校验摘要）。
3. **真实进程超时与中断**: 超时测试基于状态机时间戳比对，未模拟真实的外部死锁进程杀除。
