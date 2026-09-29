import React, { useState } from 'react';
import { TaskDetail, Dispatch, RunRecord } from '../types';

interface ActionBarProps {
  task: TaskDetail;
  activeDispatch: Dispatch | null;
  activeRun: RunRecord | null;
  onDispatch: () => Promise<void>;
  onRunCodex: (dispatchId: string) => Promise<void>;
  onPause: () => Promise<void>;
  onResume: () => Promise<void>;
  onRetry: (dispatchId: string) => Promise<void>;
  onExpire: () => Promise<void>;
  onOpenHandoff: (dispatchId: string) => void;
  onOpenReportModal: (dispatchId: string) => void;
}

export const ActionBar: React.FC<ActionBarProps> = ({
  task,
  activeDispatch,
  activeRun,
  onDispatch,
  onRunCodex,
  onPause,
  onResume,
  onRetry,
  onExpire,
  onOpenHandoff,
  onOpenReportModal,
}) => {
  const [loadingAction, setLoadingAction] = useState<string | null>(null);

  const handleAction = async (name: string, fn: () => Promise<void>) => {
    if (loadingAction) return; // Debounce / prevent double submission
    setLoadingAction(name);
    try {
      await fn();
    } finally {
      setLoadingAction(null);
    }
  };

  const isPaused = task.status === 'PAUSED';
  const isBlocked = task.status === 'BLOCKED';
  const isCompleted = task.status === 'COMPLETED';
  const isRunning = activeRun?.status === 'RUNNING' || activeRun?.status === 'PREPARED';

  // Check if there is an active codex dispatch ready to run
  const canRunCodex =
    activeDispatch &&
    activeDispatch.status === 'ACTIVE' &&
    activeDispatch.role === 'codex' &&
    !isRunning &&
    !isPaused;

  return (
    <div style={{ padding: '12px 24px', backgroundColor: 'var(--bg-secondary)', borderBottom: '1px solid var(--border-color)' }}>
      <div className="action-bar">
        {/* 1. Dispatch Next */}
        <button
          className="btn btn-primary"
          disabled={isPaused || isBlocked || isCompleted || isRunning || !!loadingAction}
          onClick={() => handleAction('dispatch', onDispatch)}
          title="调度并释放下一个符合 DAG 依赖关系的工单"
        >
          {loadingAction === 'dispatch' ? '派发中...' : '▶ 派发下一项 (Dispatch)'}
        </button>

        {/* 2. Run Codex */}
        <button
          className="btn btn-purple"
          disabled={!canRunCodex || !!loadingAction}
          onClick={() => activeDispatch && handleAction('runCodex', () => onRunCodex(activeDispatch.id))}
          title="在受保护沙箱中自动启动本地 Codex 子进程执行当前派发"
        >
          {isRunning
            ? '⏳ Codex 正在执行...'
            : loadingAction === 'runCodex'
            ? '启动中...'
            : '⚡ 启动 Codex (Run)'}
        </button>

        {/* 3. Pause / Resume */}
        {isPaused ? (
          <button
            className="btn btn-warning"
            disabled={!!loadingAction}
            onClick={() => handleAction('resume', onResume)}
            title="恢复任务调度"
          >
            {loadingAction === 'resume' ? '恢复中...' : '▶ 继续 (Resume)'}
          </button>
        ) : (
          <button
            className="btn btn-secondary"
            disabled={isCompleted || !!loadingAction}
            onClick={() => handleAction('pause', onPause)}
            title="暂停后续派发 (允许在途任务落库)"
          >
            {loadingAction === 'pause' ? '暂停中...' : '⏸ 暂停 (Pause)'}
          </button>
        )}

        {/* 4. Manual Handoff Bundle (for Antigravity or manual operations) */}
        <button
          className="btn btn-secondary"
          disabled={!activeDispatch || activeDispatch.status !== 'ACTIVE'}
          onClick={() => activeDispatch && onOpenHandoff(activeDispatch.id)}
          title="复制当前派发任务的完整交接包（提示词、上下文、验收模板），以便人工或外部传递"
        >
          📋 复制交接包 (Handoff)
        </button>

        {/* 5. Import Report */}
        <button
          className="btn btn-secondary"
          disabled={!activeDispatch || activeDispatch.status !== 'ACTIVE'}
          onClick={() => activeDispatch && onOpenReportModal(activeDispatch.id)}
          title="导入并提交 Agent 的审查或验收结果 JSON 报告"
        >
          📥 导入结果报告 (Submit)
        </button>

        {/* 6. Retry (when blocked) */}
        {isBlocked && task.block_reason !== 'REPAIR_LIMIT' && activeDispatch && (
          <button
            className="btn btn-danger"
            disabled={!!loadingAction}
            onClick={() => handleAction('retry', () => onRetry(activeDispatch.id))}
            title="重试已阻塞的派发（递增 attempt 并作废旧派发）"
          >
            {loadingAction === 'retry' ? '重试中...' : '🔄 显式重试 (Retry)'}
          </button>
        )}

        {/* 7. Expire Check */}
        <button
          className="btn btn-secondary"
          style={{ marginLeft: 'auto' }}
          disabled={isCompleted || !!loadingAction}
          onClick={() => handleAction('expire', onExpire)}
          title="检查并作废所有已超过 Deadline 的在途派发"
        >
          ⏱️ 检查超时
        </button>
      </div>
    </div>
  );
};
