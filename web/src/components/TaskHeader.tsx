import React from 'react';
import { TaskDetail, RunRecord } from '../types';

interface TaskHeaderProps {
  task: TaskDetail;
  activeRun: RunRecord | null;
}

export const TaskHeader: React.FC<TaskHeaderProps> = ({ task, activeRun }) => {
  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'COMPLETED':
        return <span className="status-badge badge-completed">COMPLETED / 已完成</span>;
      case 'PAUSED':
        return <span className="status-badge badge-paused">PAUSED / 已暂停</span>;
      case 'BLOCKED':
        return <span className="status-badge badge-blocked">BLOCKED / 阻塞</span>;
      case 'ACTIVE':
      default:
        return <span className="status-badge badge-active">ACTIVE / 进行中</span>;
    }
  };

  return (
    <div className="stage-header">
      <div className="stage-header-top">
        <div className="task-headline">
          <h1 className="task-title">{task.title}</h1>
          {getStatusBadge(task.status)}
          <span className="status-badge" style={{ backgroundColor: 'var(--bg-tertiary)', color: 'var(--text-secondary)' }}>
            阶段: {task.phase}
          </span>
        </div>
      </div>

      {/* Block reason alert if task is blocked */}
      {task.status === 'BLOCKED' && (
        <div className="alert-banner alert-danger">
          <div>
            <strong>⚠️ 任务处于阻塞状态: </strong>
            <span>{task.block_reason || '存在阻断缺陷或外部超时'}</span>
            {task.block_reason === 'REPAIR_LIMIT' && (
              <div style={{ marginTop: '4px', fontSize: '12px' }}>
                已达到最大代码修复上限 (2 次)。根据安全熔断契约，系统已挂起以避免无限循环。请人工复核分歧后处理。
              </div>
            )}
          </div>
        </div>
      )}

      {/* Paused alert if task is paused */}
      {task.status === 'PAUSED' && (
        <div className="alert-banner alert-warning">
          <div>
            <strong>⏸️ 任务已暂停: </strong>
            <span>调度器已停止派发新工作。若此前有在途执行或未提交结果，仍允许提交入库，但在点击“继续 (Resume)”前不会自动启动下一阶段。</span>
          </div>
        </div>
      )}

      {/* Active Run Banner */}
      {activeRun && activeRun.status === 'RUNNING' && (
        <div className="alert-banner" style={{ backgroundColor: 'rgba(192, 132, 252, 0.15)', border: '1px solid #c084fc', color: '#e9d5ff' }}>
          <div>
            <strong>⚡ Codex 正在后台执行: </strong>
            <span>Run ID: {activeRun.id.slice(0, 8)}... (PID: {activeRun.pid || '启动中'}) | 正在独立沙箱中生成产物，请稍候。前端每 2.5 秒自动刷新。</span>
          </div>
        </div>
      )}

      {/* Requirement box */}
      <div className="task-req-box">
        <strong>需求描述: </strong>
        <span>{task.requirement}</span>
      </div>
    </div>
  );
};
