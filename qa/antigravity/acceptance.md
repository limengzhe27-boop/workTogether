# Antigravity 独立验收测试规格说明书 (Acceptance Specification) v1

- **基准规范**: `docs/contracts/v1.md` 及 `docs/contracts/qa-response-v1.md`
- **公共入口**: `worktogether.core.Engine` 及 `python3 -m worktogether --db PATH COMMAND`
- **测试框架**: Python `unittest`（标准库，不引入额外依赖）
- **测试目录**: `tests/acceptance/antigravity/`

---

## 一、 核心状态机与协议定义（对齐 v1 契约）

1. **任务状态 (Task Status)**: `ACTIVE`, `PAUSED`, `BLOCKED`, `COMPLETED`
2. **任务阶段 (Task Phase)**: `PLANNING`, `PLAN_REVIEW`, `PLAN_FINALIZE`, `EXECUTING`, `COMPLETED`
3. **步骤状态 (Step Status)**: `PENDING`, `CODING`, `VERIFYING`, `FIXING`, `COMPLETED`, `BLOCKED`
4. **派发类型与角色 (Dispatch Kind & Role)**:
   - `PLAN` (codex) -> `PLAN_REVIEW` (antigravity, 限 1 轮) -> `PLAN_FINALIZE` (codex)
   - `CODE` (codex) -> `VERIFY` (antigravity) -> `FIX` (codex, 最多 2 次)
5. **验收报告封套规范 (Report Envelope)**:
   - 必须包含：`schema_version: 1`, `task_id`, `dispatch_id`, `role`, `attempt`, `status`, `summary`, `basis`, `suggestions`, `next_action`, `data`。未知字段严禁包含。
   - `VERIFY` 报告的 `data.checks` 必须严格与步骤声明的 `acceptance` 字符串一一对应，每个 check 包含 `status` (PASS/FAIL) 和非空 `evidence` 字符串数组。
   - `FAIL` 报告必须包含至少一条 `findings`（含 problem, reproduction, expected, actual, evidence）。

---

## 二、 核心验收场景（黑盒可观测行为定义）

### 场景 01：正常基线流转 (ACC-001)
- **目标**: 验证完整生命周期从需求提出到最终完成。
- **流转路径**:
  1. `task_add` -> 状态 `ACTIVE`, 阶段 `PLANNING`。
  2. `dispatch` -> 获得 `PLAN` 派发。
  3. `submit` (PLAN 结果，含 1 个无依赖步骤 `s1`，包含验收准则 `["c1"]`) -> 阶段进入 `PLAN_REVIEW`。
  4. `dispatch` -> 获得 `PLAN_REVIEW` 派发（分配给 Antigravity）。
  5. `submit` (PLAN_REVIEW 结果，suggestions ≤ 3) -> 阶段进入 `PLAN_FINALIZE`。
  6. `dispatch` -> 获得 `PLAN_FINALIZE` 派发（分配给 Codex）。
  7. `submit` (PLAN_FINALIZE 结果) -> 阶段进入 `EXECUTING`，步骤 `s1` 变为 `CODING`。
  8. `dispatch` -> 获得 `CODE` 派发。
  9. `submit` (CODE 结果，附带不可变版本 `v1`) -> 步骤 `s1` 变为 `VERIFYING`。
  10. `dispatch` -> 获得 `VERIFY` 派发（版本匹配 `v1`）。
  11. `submit` (VERIFY 结果，`PASS`，包含对应 `c1` 的 check 和 evidence) -> 步骤 `s1` 变为 `COMPLETED`，任务进入 `COMPLETED`。
- **断言**:
  - 任务状态为 `COMPLETED`，阶段为 `COMPLETED`。
  - `show` 输出包含完整时序事件。

---

### 场景 02：阶段验收失败 -> 触发修复 -> 复验通过 (ACC-002)
- **目标**: 验证在允许的修复额度内（repair_count=1），FAIL 报告能正确触发 FIX 派发并在修复后推进。
- **流转路径**:
  1. 任务到达步骤 `s1` 的 `VERIFY` 阶段，版本为 `v1`。
  2. 提交 `status: FAIL` 报告（附带 finding 与复现依据）。
  3. 步骤状态变为 `FIXING`，`step.repair_count` 递增为 1。
  4. 再次调用 `dispatch` -> 成功派发 `kind: FIX` 给 Codex，`attempt: 2`。
  5. Codex 提交修复结果（必须提交新版本 `v2`）。
  6. 步骤状态重新变为 `VERIFYING`。
  7. 再次调用 `dispatch` -> 派发 `kind: VERIFY` 给 Antigravity，目标版本为 `v2`。
  8. 提交 `status: PASS` 报告。
- **断言**:
  - 步骤状态成功转为 `COMPLETED`。
  - 两次验收分别绑定了不同版本 `v1` 与 `v2`。

---

### 场景 03：两次修复仍失败 -> 任务熔断 BLOCKED (ACC-003)
- **目标**: 严格验证修复上限（max repairs = 2）。第二次修复失败后，系统必须进入 `BLOCKED`，且禁止继续自动派发或通过 CLI retry 重试。
- **流转路径**:
  1. 步骤 `s1` 第一次验证 FAIL -> 触发 FIX 1 (`repair_count = 1`)。
  2. 提交 FIX 1 (版本 `v2`)。
  3. 第二次验证 FAIL -> 触发 FIX 2 (`repair_count = 2`)。
  4. 提交 FIX 2 (版本 `v3`)。
  5. 第三次验证再次提交 FAIL。
- **断言**:
  - 步骤 `s1` 状态变更为 `BLOCKED`。
  - 任务状态变更为 `BLOCKED`。
  - 任务事件中记录 `REPAIR_LIMIT`。
  - 调用 `dispatch` 返回 `null`。
  - 调用 `retry` 抛出 `REPAIR_LIMIT` 域错误（不允许重试）。

---

### 场景 04：重复提交与冲突幂等性 (ACC-004)
- **目标**: 验证重复提交的安全幂等性及同派发不同结果的冲突防护。
- **流转路径**:
  1. 对某一活跃派发 `d1` 提交合法结果 `R1`，返回 `{"accepted": true}`。
  2. 再次以完全相同的数据向 `d1` 提交 `R1`。
- **断言**:
  - 返回 `{"accepted": true, "duplicate": true}`。
  - 状态机不产生新事件，步骤与任务状态不重复跳跃。
  3. 若向已结束的 `d1` 提交不同的报告数据 `R2`：
- **断言**:
  - 抛出 `CONFLICT` 错误代码。

---

### 场景 05：陈旧派发与过期处理 (ACC-005)
- **目标**: 验证旧派发被作废或超时后，陈旧报告被拒绝，且状态保持不变。
- **流转路径**:
  1. 派发 `d1` 超时或被显式 retry 作废，生成新派发 `d2`。
  2. 此时外部向 `d1` 提交报告。
- **断言**:
  - 抛出 `STALE_REPORT` 错误代码。
  - 任务当前状态与 `d2` 的活跃状态保持不变。

---

### 场景 06：项目与任务严格隔离 (ACC-006)
- **目标**: 验证多项目、多任务下的数据独立性与互斥锁。
- **流转路径**:
  1. 建立 `Project A` (path A) 与 `Project B` (path B)。
  2. 分别创建 `Task A` 与 `Task B`。
  3. 在 `Task A` 处于 `CODING` 时，同项目若有其他任务尝试写入应受限制；但不同项目 `Task B` 完全独立推进。
  4. 跨任务提交（将 Task A 的 dispatch 提交给 Task B）必须被拦截。
- **断言**:
  - 跨任务提交抛出 `NOT_FOUND` 或 `INVALID_REPORT`。
  - Task A 的流转和失败绝对不影响 Task B。

---

### 场景 07：暂停与在途结果处理 (ACC-007)
- **目标**: 验证 `PAUSE` 与 `RESUME` 的精确语义。
- **流转路径**:
  1. 任务派发了 `d1`，随后调用 `pause`。任务状态变为 `PAUSED`。
  2. 在 `PAUSED` 期间调用 `dispatch` -> 必须返回 `{"dispatch": null}`（禁止派发新工作）。
  3. 在 `PAUSED` 期间提交 `d1` 的合法结果。
- **断言**:
  - 提交成功落库（`accepted: true`）。
  - 内部步骤状态被正确推进（如从 `CODING` 变为 `VERIFYING`），但**任务主状态仍保持 `PAUSED`**。
  4. 调用 `resume` -> 任务重新根据内部步骤状态计算恢复为 `ACTIVE`。
  5. 再次调用 `dispatch` -> 能够正常派发后续工作。

---

### 场景 08：进程重启与状态持久化恢复 (ACC-008)
- **目标**: 验证基于 SQLite 的可靠崩溃恢复能力。
- **流转路径**:
  1. Engine 实例 1 在数据库中执行任务至中间状态（如已派发 `d1`，状态为 `ACTIVE`）。
  2. 销毁 Engine 实例 1（模拟进程退出/崩溃）。
  3. 创建全新的 Engine 实例 2 指向同一数据库文件。
- **断言**:
  - 实例 2 读取到的任务状态、步骤状态及事件流与实例 1 完全一致。
  - 调用 `dispatch` 依然返回 `null`（不会向外部重复派发已处于 ACTIVE 的相同工作）。
  - 针对 `d1` 提交结果能被实例 2 正常处理。

---

### 场景 09：非法输入拦截与状态不变性 (ACC-009)
- **目标**: 验证各种畸形输入被拦截，且被拦截后数据库事务必须完整回滚（状态绝对不变）。
- **覆盖子场景**:
  - `9a (缺失证据)`: `checks` 中某项缺少 `evidence` 或为空 -> 抛出 `INVALID_REPORT`。
  - `9b (缺少准则)`: `checks` 未覆盖步骤声明的所有验收准则 -> 抛出 `INVALID_REPORT`。
  - `9c (多余准则)`: `checks` 包含了未在步骤声明的准则 -> 抛出 `INVALID_REPORT`。
  - `9d (未知字段)`: 报告顶层或内部包含 schema 之外的字段 -> 抛出 `INVALID_REPORT`。
  - `9e (版本不匹配)`: VERIFY 提交的版本与派发版本不一致 -> 抛出 `INVALID_REPORT`。
  - `9f (BLOCKED 判定)`: 提交 BLOCKED 报告 -> 派发与步骤变更为 `BLOCKED`，不可作为 PASS 推进。
  - `9g (规划循环依赖)`: PLAN 包含 `s1 -> s2 -> s1` 循环 -> 抛出 `INVALID_PLAN`。
- **断言**:
  - 所有非法请求均返回对应错误代码。
  - 查询 `show` 确认任务状态、步骤状态与事件数在错误发生前后**完全一致（零副作用回滚）**。

---

### 场景 10：步骤依赖图与独立步骤并发重叠 (ACC-010)
- **目标**: 验证 DAG 依赖约束以及独立步骤的流水线并发能力。
- **流转路径**:
  1. 规划包含 2 个步骤：`s1` (独立) 和 `s2` (依赖 `s1`)。
  2. 在 `s1` 尚未 `COMPLETED` 前，调度器绝不能派发 `s2` 的 CODE 任务。
  3. 当规划包含 2 个无依赖的独立步骤 `s1` 和 `s2` 时：
     - `s1` 完成 CODE 进入 `VERIFYING`；
     - 此时调度器允许并发派发 `s1` 的 Antigravity VERIFY 和 `s2` 的 Codex CODE（流水线重叠）。
- **断言**:
  - 有依赖时，依赖未满足前 `dispatch` 不会泄露后续步骤。
  - 独立步骤时，支持一个 Codex 写入任务与一个 Antigravity 验证任务重叠活跃。
