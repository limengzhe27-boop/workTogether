import React, { useState } from 'react';
import { TaskDetail, PlanStep } from '../types';

interface StepsViewProps {
  task: TaskDetail;
}

export const StepsView: React.FC<StepsViewProps> = ({ task }) => {
  const [expandedSteps, setExpandedSteps] = useState<Record<string, boolean>>({});

  const toggleStep = (id: string) => {
    setExpandedSteps((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const getStepStatusBadge = (status?: string, repairCount?: number) => {
    switch (status) {
      case 'COMPLETED':
        return <span className="status-badge badge-completed">COMPLETED / 已验收通过</span>;
      case 'CODING':
        return <span className="status-badge badge-active">CODING / 开发中 (Codex)</span>;
      case 'VERIFYING':
        return <span className="status-badge badge-active" style={{ backgroundColor: 'rgba(56, 189, 248, 0.2)', color: '#38bdf8' }}>VERIFYING / 独立验收中 (Antigravity)</span>;
      case 'FIXING':
        return <span className="status-badge badge-warning">FIXING / 修复中 ({repairCount || 1}/2)</span>;
      case 'BLOCKED':
        return <span className="status-badge badge-blocked">BLOCKED / 修复超限或阻塞</span>;
      case 'PENDING':
      default:
        return <span className="status-badge" style={{ backgroundColor: 'var(--bg-tertiary)', color: 'var(--text-muted)' }}>PENDING / 等待前置依赖</span>;
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h3 style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase' }}>
          阶段步骤与独立验收清单 ({task.steps.length})
        </h3>
        <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
          单步修复上限: 2 次 | 独立步骤允许重叠执行
        </span>
      </div>

      {task.steps.length === 0 ? (
        <div style={{ backgroundColor: 'var(--bg-card)', padding: '24px', borderRadius: '8px', textAlign: 'center', color: 'var(--text-muted)' }}>
          尚未生成最终执行步骤。当前处于规划期（{task.phase}），请点击上方“派发下一项”启动方案讨论。
        </div>
      ) : (
        task.steps.map((step) => {
          const isExpanded = !!expandedSteps[step.id];
          const ver = step.verification?.data;
          const checks = ver?.checks || [];
          const findings = ver?.findings || [];

          return (
            <div
              key={step.id}
              style={{
                backgroundColor: 'var(--bg-card)',
                border: '1px solid var(--border-color)',
                borderRadius: '8px',
                padding: '16px',
              }}
            >
              {/* Header */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '8px' }}>
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span style={{ fontWeight: 'bold', color: 'var(--accent-blue)', fontSize: '14px' }}>
                      [{step.id}]
                    </span>
                    <span style={{ fontSize: '15px', fontWeight: 600, color: '#fff' }}>
                      {step.title}
                    </span>
                  </div>
                  {step.depends_on && step.depends_on.length > 0 && (
                    <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px' }}>
                      依赖前置步骤: {step.depends_on.join(', ')}
                    </div>
                  )}
                </div>
                <div>
                  {getStepStatusBadge(step.status, step.repair_count)}
                </div>
              </div>

              {/* Version and Repair Meta */}
              <div style={{ display: 'flex', gap: '16px', fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
                <div>目标代码版本: <code style={{ color: 'var(--accent-purple)' }}>{step.version || '尚未提交'}</code></div>
                <div>修复轮次: <strong>{step.repair_count || 0} / 2</strong></div>
              </div>

              {/* Acceptance Criteria Checklist */}
              <div style={{ backgroundColor: 'var(--bg-primary)', padding: '12px', borderRadius: '6px', border: '1px solid var(--border-color)', marginBottom: '8px' }}>
                <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '6px' }}>
                  验收准则 (Acceptance Criteria):
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                  {step.acceptance.map((crit, idx) => {
                    const checkResult = checks.find((c: any) => c.criterion === crit);
                    const isPass = checkResult?.status === 'PASS';
                    const isFail = checkResult?.status === 'FAIL';

                    return (
                      <div key={idx} style={{ display: 'flex', alignItems: 'flex-start', gap: '8px', fontSize: '13px' }}>
                        <span style={{ color: isPass ? '#4ade80' : isFail ? '#f87171' : 'var(--text-muted)', fontWeight: 'bold' }}>
                          {isPass ? '✓' : isFail ? '✗' : '○'}
                        </span>
                        <div style={{ flex: 1 }}>
                          <span>{crit}</span>
                          {checkResult?.evidence && (
                            <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '2px' }}>
                              证据引用: {checkResult.evidence.join('; ')}
                            </div>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Findings (Defects) */}
              {findings.length > 0 && (
                <div style={{ backgroundColor: 'rgba(248, 113, 113, 0.1)', border: '1px solid #f87171', padding: '10px 14px', borderRadius: '6px', marginBottom: '8px' }}>
                  <div style={{ fontSize: '12px', fontWeight: 700, color: '#fca5a5', marginBottom: '4px' }}>
                    发现阻塞性缺陷 (Findings):
                  </div>
                  {findings.map((f: any, fIdx: number) => (
                    <div key={fIdx} style={{ fontSize: '12px', color: '#fecaca', marginBottom: '6px' }}>
                      <div><strong>问题:</strong> {f.problem}</div>
                      <div><strong>预期:</strong> {f.expected} | <strong>实际:</strong> {f.actual}</div>
                      {f.reproduction && <div><strong>复现步骤:</strong> {f.reproduction.join(' -> ')}</div>}
                    </div>
                  ))}
                </div>
              )}

              {/* Toggle Handoff / Verification JSON */}
              <div>
                <span
                  style={{ fontSize: '11px', color: 'var(--accent-blue)', cursor: 'pointer', userSelect: 'none' }}
                  onClick={() => toggleStep(step.id)}
                >
                  {isExpanded ? '▲ 收起报告与交付细节' : '▼ 查看报告与交付细节'}
                </span>
                {isExpanded && (
                  <div style={{ marginTop: '8px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    {step.handoff && (
                      <div>
                        <strong style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Codex 交付便签 (Handoff):</strong>
                        <pre>{JSON.stringify(step.handoff, null, 2)}</pre>
                      </div>
                    )}
                    {step.verification && (
                      <div>
                        <strong style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Antigravity 验收报告 (Verification):</strong>
                        <pre>{JSON.stringify(step.verification, null, 2)}</pre>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          );
        })
      )}
    </div>
  );
};
