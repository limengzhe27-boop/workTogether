import React, { useState, useEffect } from 'react';
import { api } from '../api';
import { ReportEnvelope } from '../types';

interface ReportModalProps {
  taskId: string;
  dispatchId: string;
  onClose: () => void;
  onSubmitted: () => void;
}

export const ReportModal: React.FC<ReportModalProps> = ({
  taskId,
  dispatchId,
  onClose,
  onSubmitted,
}) => {
  const [jsonText, setJsonText] = useState('');
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    const fetchTemplate = async () => {
      try {
        const res = await api.getTemplate(taskId, dispatchId);
        setJsonText(JSON.stringify(res.template, null, 2));
      } catch (err: any) {
        setErrorMsg(err.message || '获取结果模板失败');
      } finally {
        setLoading(false);
      }
    };
    fetchTemplate();
  }, [taskId, dispatchId]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg(null);
    let parsed: ReportEnvelope;
    try {
      parsed = JSON.parse(jsonText);
    } catch (err: any) {
      setErrorMsg(`JSON 语法解析错误: ${err.message}`);
      return;
    }

    setSubmitting(true);
    try {
      await api.submitReport(taskId, parsed);
      onSubmitted();
      onClose();
    } catch (err: any) {
      setErrorMsg(err.message || '提交报告失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-overlay">
      <div className="modal-dialog" style={{ maxWidth: '750px' }}>
        <div className="modal-header">
          <h3>导入/提交 Agent 成果报告 (Submit Report)</h3>
          <button className="btn-secondary" onClick={onClose}>✕</button>
        </div>
        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
              请确认或编辑当前工单 (<code>{dispatchId}</code>) 的输出 JSON。必须严格符合 <code>docs/contracts/v1.md</code> 封套规范。
            </div>

            {errorMsg && <div className="alert-banner alert-danger">{errorMsg}</div>}

            {loading ? (
              <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>
                加载报告模板中...
              </div>
            ) : (
              <div className="form-group">
                <textarea
                  className="form-textarea"
                  style={{ minHeight: '380px', fontFamily: 'monospace', fontSize: '12px' }}
                  value={jsonText}
                  onChange={(e) => setJsonText(e.target.value)}
                  required
                />
              </div>
            )}
          </div>
          <div className="modal-footer">
            <button type="button" className="btn btn-secondary" onClick={onClose}>
              取消
            </button>
            <button type="submit" className="btn btn-primary" disabled={loading || submitting}>
              {submitting ? '提交验证中...' : '提交结果入库 (Submit)'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
