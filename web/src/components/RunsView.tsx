import React, { useState } from 'react';
import { RunRecord } from '../types';
import { api } from '../api';

interface RunsViewProps {
  runs: RunRecord[];
}

export const RunsView: React.FC<RunsViewProps> = ({ runs }) => {
  const [selectedRun, setSelectedRun] = useState<RunRecord | null>(runs[0] || null);
  const [activeArtifact, setActiveArtifact] = useState<string | null>(null);
  const [artifactContent, setArtifactContent] = useState<string | null>(null);
  const [loadingArtifact, setLoadingArtifact] = useState(false);
  const [artifactError, setArtifactError] = useState<string | null>(null);

  const viewArtifact = async (runId: string, filename: string) => {
    setActiveArtifact(filename);
    setLoadingArtifact(true);
    setArtifactError(null);
    try {
      const res = await api.getArtifact(runId, filename);
      setArtifactContent(res.content);
    } catch (err: any) {
      setArtifactError(err.message || '读取产物失败');
      setArtifactContent(null);
    } finally {
      setLoadingArtifact(false);
    }
  };

  const getRunBadge = (status: string) => {
    switch (status) {
      case 'COMPLETED':
        return <span className="status-badge badge-completed">COMPLETED</span>;
      case 'RUNNING':
        return <span className="status-badge badge-active">RUNNING</span>;
      case 'PREPARED':
        return <span className="status-badge badge-paused">PREPARED</span>;
      case 'FAILED':
      default:
        return <span className="status-badge badge-blocked">{status}</span>;
    }
  };

  const formatTime = (ts?: number) => {
    if (!ts) return '-';
    return new Date(ts * 1000).toLocaleString();
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h3 style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase' }}>
          Codex 执行运行日志与用量 ({runs.length})
        </h3>
        <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
          所有运行记录与产物均真实持久化在本地 runs/ 目录
        </span>
      </div>

      {runs.length === 0 ? (
        <div style={{ backgroundColor: 'var(--bg-card)', padding: '30px', borderRadius: '8px', textAlign: 'center', color: 'var(--text-muted)' }}>
          暂无 Codex 运行记录。当有处于 ACTIVE 状态的 Codex 派发时，点击上方“⚡ 启动 Codex (Run)”即可触发执行。
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          {/* Runs Table */}
          <div style={{ backgroundColor: 'var(--bg-card)', borderRadius: '8px', border: '1px solid var(--border-color)', overflow: 'hidden' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Run ID</th>
                  <th>状态</th>
                  <th>关联派发</th>
                  <th>PID / 会话</th>
                  <th>退出码</th>
                  <th>启动时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => {
                  const isSelected = selectedRun?.id === r.id;
                  return (
                    <tr
                      key={r.id}
                      style={{ backgroundColor: isSelected ? 'rgba(56, 189, 248, 0.08)' : undefined }}
                    >
                      <td>
                        <strong style={{ color: 'var(--accent-blue)' }}>{r.id.slice(0, 8)}...</strong>
                      </td>
                      <td>{getRunBadge(r.status)}</td>
                      <td style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                        {r.dispatch_id.slice(0, 8)}...
                      </td>
                      <td style={{ fontSize: '12px' }}>
                        <div>PID: {r.pid || '暂无'}</div>
                        <div style={{ color: 'var(--text-muted)', fontSize: '11px' }}>
                          {r.session_id ? `会话: ${r.session_id.slice(0, 10)}...` : '首轮未绑定'}
                        </div>
                      </td>
                      <td>
                        {r.exit_code === null ? '-' : r.exit_code === 0 ? (
                          <span style={{ color: '#4ade80' }}>0 (成功)</span>
                        ) : (
                          <span style={{ color: '#f87171' }}>{r.exit_code} (异常)</span>
                        )}
                      </td>
                      <td style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                        {formatTime(r.created_at)}
                      </td>
                      <td>
                        <button
                          className="btn btn-secondary"
                          style={{ padding: '4px 8px', fontSize: '11px' }}
                          onClick={() => {
                            setSelectedRun(r);
                            setActiveArtifact(null);
                            setArtifactContent(null);
                          }}
                        >
                          查看产物
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Selected Run Telemetry & Artifact Details */}
          {selectedRun && (
            <div style={{ backgroundColor: 'var(--bg-card)', padding: '20px', borderRadius: '8px', border: '1px solid var(--border-color)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                <h4 style={{ fontSize: '15px', fontWeight: 600, color: '#fff' }}>
                  运行详情: {selectedRun.id}
                </h4>
                {getRunBadge(selectedRun.status)}
              </div>

              {/* Telemetry / Token Usage */}
              <div style={{ marginBottom: '16px' }}>
                <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '8px' }}>
                  诚实观测指标 (Raw Telemetry):
                </div>
                {selectedRun.usage ? (
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '12px' }}>
                    <div style={{ backgroundColor: 'var(--bg-primary)', padding: '12px', borderRadius: '6px', border: '1px solid var(--border-color)' }}>
                      <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Input Tokens</div>
                      <div style={{ fontSize: '18px', fontWeight: 'bold', color: 'var(--accent-blue)' }}>
                        {selectedRun.usage.input_tokens ?? '暂无数据'}
                      </div>
                    </div>
                    <div style={{ backgroundColor: 'var(--bg-primary)', padding: '12px', borderRadius: '6px', border: '1px solid var(--border-color)' }}>
                      <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Cached Tokens</div>
                      <div style={{ fontSize: '18px', fontWeight: 'bold', color: 'var(--accent-purple)' }}>
                        {selectedRun.usage.cached_tokens ?? '暂无数据'}
                      </div>
                    </div>
                    <div style={{ backgroundColor: 'var(--bg-primary)', padding: '12px', borderRadius: '6px', border: '1px solid var(--border-color)' }}>
                      <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Output Tokens</div>
                      <div style={{ fontSize: '18px', fontWeight: 'bold', color: 'var(--accent-green)' }}>
                        {selectedRun.usage.output_tokens ?? '暂无数据'}
                      </div>
                    </div>
                  </div>
                ) : (
                  <div style={{ backgroundColor: 'var(--bg-primary)', padding: '12px', borderRadius: '6px', color: 'var(--text-muted)', fontSize: '12px' }}>
                    暂无用量数据 (该运行未提供或尚未完成)
                  </div>
                )}
              </div>

              {/* Artifacts Tab Buttons */}
              <div style={{ marginBottom: '12px' }}>
                <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '8px' }}>
                  沙箱产物安全检查 (Artifacts):
                </div>
                <div style={{ display: 'flex', gap: '8px' }}>
                  {['prompt.txt', 'result.json', 'stderr.log', 'command.json', 'events.jsonl'].map((file) => (
                    <button
                      key={file}
                      className={`btn ${activeArtifact === file ? 'btn-primary' : 'btn-secondary'}`}
                      onClick={() => viewArtifact(selectedRun.id, file)}
                    >
                      📄 {file}
                    </button>
                  ))}
                </div>
              </div>

              {/* Artifact Display Box */}
              {activeArtifact && (
                <div>
                  <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '4px' }}>
                    正在查看: <code>{selectedRun.artifact_dir}/{activeArtifact}</code>
                  </div>
                  {loadingArtifact ? (
                    <div style={{ padding: '20px', color: 'var(--text-muted)' }}>正在读取沙箱文件...</div>
                  ) : artifactError ? (
                    <div className="alert-banner alert-danger">{artifactError}</div>
                  ) : (
                    <pre style={{ maxHeight: '350px' }}>
                      {artifactContent || '(空文件)'}
                    </pre>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
};
