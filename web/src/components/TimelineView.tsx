import React, { useState } from 'react';
import { TaskDetail, Dispatch, TaskEvent } from '../types';

interface TimelineViewProps {
  task: TaskDetail;
}

const PHASES = ['PLANNING', 'PLAN_REVIEW', 'PLAN_FINALIZE', 'EXECUTING', 'COMPLETED'];

export const TimelineView: React.FC<TimelineViewProps> = ({ task }) => {
  const [expandedEvents, setExpandedEvents] = useState<Record<number, boolean>>({});

  const toggleEvent = (seq: number) => {
    setExpandedEvents((prev) => ({ ...prev, [seq]: !prev[seq] }));
  };

  const currentPhaseIndex = PHASES.indexOf(task.phase);

  const formatTime = (timestamp: number) => {
    if (!timestamp) return '-';
    return new Date(timestamp * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };

  const getRoleBadge = (role: string) => {
    if (role === 'codex') return <span className="role-tag role-codex">Codex 大哥</span>;
    if (role === 'antigravity') return <span className="role-tag role-antigravity">Antigravity 参谋/质检</span>;
    return <span className="role-tag role-system">System 系统</span>;
  };

  const activeDispatches = task.dispatches.filter((d) => d.status === 'ACTIVE');

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      {/* 1. Phase Stepper */}
      <div style={{ backgroundColor: 'var(--bg-card)', padding: '16px 20px', borderRadius: '8px', border: '1px solid var(--border-color)' }}>
        <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '12px', textTransform: 'uppercase' }}>
          协同流转进度
        </div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', position: 'relative' }}>
          {PHASES.map((p, idx) => {
            const isDone = idx < currentPhaseIndex || task.phase === 'COMPLETED';
            const isCurrent = idx === currentPhaseIndex && task.phase !== 'COMPLETED';
            return (
              <div key={p} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', zIndex: 1 }}>
                <div
                  style={{
                    width: '28px',
                    height: '28px',
                    borderRadius: '50%',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontWeight: 'bold',
                    fontSize: '12px',
                    backgroundColor: isDone ? '#0284c7' : isCurrent ? '#38bdf8' : 'var(--bg-tertiary)',
                    color: isDone || isCurrent ? '#fff' : 'var(--text-muted)',
                    boxShadow: isCurrent ? '0 0 10px rgba(56, 189, 248, 0.6)' : 'none',
                    border: isCurrent ? '2px solid #fff' : 'none',
                  }}
                >
                  {isDone ? '✓' : idx + 1}
                </div>
                <span
                  style={{
                    fontSize: '11px',
                    marginTop: '6px',
                    color: isCurrent ? 'var(--accent-blue)' : isDone ? 'var(--text-primary)' : 'var(--text-muted)',
                    fontWeight: isCurrent ? 700 : 500,
                  }}
                >
                  {p}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* 2. Active Dispatches (Work in Progress) */}
      {activeDispatches.length > 0 && (
        <div style={{ backgroundColor: '#1e3a5f', padding: '16px', borderRadius: '8px', border: '1px solid #38bdf8' }}>
          <h3 style={{ fontSize: '14px', color: '#38bdf8', marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span>⚡ 当前在途派发工单 ({activeDispatches.length})</span>
          </h3>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '12px' }}>
            {activeDispatches.map((d) => (
              <div
                key={d.id}
                style={{
                  backgroundColor: 'var(--bg-secondary)',
                  padding: '12px',
                  borderRadius: '6px',
                  border: '1px solid var(--border-color)',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '6px' }}>
                  <strong>{d.kind}</strong>
                  {getRoleBadge(d.role)}
                </div>
                <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                  <div>工单 ID: {d.id.slice(0, 10)}...</div>
                  <div>关联步骤: {d.step_id || '全局规划'}</div>
                  <div>尝试轮次: Attempt {d.attempt}</div>
                  <div>截止时间: {formatTime(d.deadline)}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 3. Chronological Events Timeline */}
      <div>
        <h3 style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '12px', textTransform: 'uppercase' }}>
          双方协同事件流水 ({task.events.length})
        </h3>
        {task.events.length === 0 ? (
          <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>暂无记录事件</div>
        ) : (
          <div className="timeline-list">
            {[...task.events].reverse().map((ev) => {
              const isExpanded = !!expandedEvents[ev.seq];
              return (
                <div key={ev.seq} className="timeline-item">
                  <div className="timeline-item-header">
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                      <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--accent-blue)' }}>
                        #{ev.seq} {ev.kind}
                      </span>
                    </div>
                    <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                      {formatTime(ev.created_at)}
                    </div>
                  </div>

                  <div style={{ fontSize: '13px', color: 'var(--text-primary)', marginBottom: '6px' }}>
                    {ev.kind === 'TASK_CREATED' && `创建任务: ${ev.data?.title}`}
                    {ev.kind === 'DISPATCHED' && `派发工单: ${ev.data?.kind} (ID: ${ev.data?.dispatch_id?.slice(0, 8)})`}
                    {ev.kind === 'RESULT_ACCEPTED' && (
                      <span>
                        结果审核通过: 状态 <strong>{ev.data?.status}</strong> (Dispatch: {ev.data?.dispatch_id?.slice(0, 8)})
                      </span>
                    )}
                    {ev.kind === 'PAUSED' && '任务已被暂停'}
                    {ev.kind === 'RESUMED' && '任务已恢复运行'}
                    {ev.kind === 'EXPIRED' && '在途工单超时作废'}
                    {ev.kind === 'RETRIED' && `已重试工单: ${ev.data?.dispatch_id?.slice(0, 8)}`}
                    {ev.kind === 'RUN_RESERVED' && `启动 Codex 运行: Run ID ${ev.data?.run_id?.slice(0, 8)}`}
                  </div>

                  {/* Toggle Full Event Details */}
                  {ev.data && Object.keys(ev.data).length > 0 && (
                    <div>
                      <span
                        style={{ fontSize: '11px', color: 'var(--accent-blue)', cursor: 'pointer', userSelect: 'none' }}
                        onClick={() => toggleEvent(ev.seq)}
                      >
                        {isExpanded ? '▲ 收起详细数据' : '▼ 查看详细数据 (JSON)'}
                      </span>
                      {isExpanded && (
                        <pre style={{ marginTop: '8px' }}>
                          {JSON.stringify(ev.data, null, 2)}
                        </pre>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};
