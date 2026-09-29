# 待 Codex 明确的接口问题与设计约束 (Findings & Design Questions)

**模块**: QA 独立验收与契约对接  
**日期**: 2026-09-14  

为确保后续 `tests/acceptance/antigravity/` 下的可执行 pytest 黑盒测试与 Codex 生产核心严密贴合，在发布 `docs/contracts/` 时请明确以下 4 个关键接口细节：

---

### 1. 证据有效性判定边界 (Evidence Validation Boundary)
- **问题**: 在验收报告中，什么样的 `evidence` 才算合格？
  - 是否硬性要求 `exit_code == 0`？
  - 是否校验 `log_artifact` 指向的物理文件是否存在且非空？
  - 如果测试是纯静态代码分析（无命令执行），证据字段的统一结构为何？
- **QA 建议**: 调度器在反序列化报告时，应至少做浅层断言：`evidence` 不为空对象，且当声明了 `log_artifact` 时，目标文件必须在磁盘中实际存在。

---

### 2. 暂停（PAUSE）期间在途结果的落库语义
- **问题**: 当任务被用户置为 `PAUSED` 时，若在此期间 Antigravity 或 Codex 投递了异步执行结果：
  - 调度器是将其记录入库但不触发下一阶段流转（推荐）？
  - 还是直接在 HTTP/CLI 入口拒绝接收请求（409 Conflict / Error）？
- **QA 建议**: 允许写入数据库并暂存为 `PENDING_RESUME`，但不分发后续任务，避免 Agent 端重新计算或丢结果。

---

### 3. 返工计数器（retry_count）的作用域
- **问题**: 最多 2 次修复的限制，是绑定在**单个具体的 Step（子步骤）**上，还是整个 **Task（大任务）** 的生命周期？
- **QA 建议**: 绑定在 `step_id` 上。单个步骤修复 2 次仍未通过则触发熔断；进入下一个步骤后，重试计数器应独立重置，同时在父任务保留 `total_reworks` 历史统计。

---

### 4. 幂等键与去重依据
- **问题**: 场景 `ACC-004` 中判断重复提交的依据是什么？
  - 是依据全局唯一的 `report_id`？还是 `(task_id, step_id, batch_id)` 三元组？
- **QA 建议**: 推荐以 `(task_id, step_id, batch_id)` 或 `report_id` 作为唯一索引约束，拦截重复推进。
