import React, { useState, useEffect } from 'react';
import { api } from '../api';
import { HandoffBundle } from '../types';

interface HandoffModalProps {
  taskId: string;
  dispatchId: string;
  onClose: () => void;
}

export const HandoffModal: React.FC<HandoffModalProps> = ({
  taskId,
  dispatchId,
  onClose,
}) => {
  const [bundle, setBundle] = useState<HandoffBundle | null>(null);
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    const fetchHandoff = async () => {
      try {
        const res = await api.getHandoff(taskId, dispatchId);
        setBundle(res);
      } catch (err: any) {
        setErrorMsg(err.message || '获取交接包失败');
      } finally {
        setLoading(false);
      }
    };
    fetchHandoff();
  }, [taskId, dispatchId]);

  const getMarkdownText = () => {
    if (!bundle) return '';
    return `# WorkTogether 任务交接说明

## 1. 任务背景
- **任务 ID**: ${bundle.task.id}
- **任务标题**: ${bundle.task.title}
- **需求描述**: ${bundle.task.requirement}
- **当前阶段**: ${bundle.task.phase}

## 2. 待执行工单
- **工单 ID**: ${bundle.dispatch.id}
- **执行角色**: ${bundle.dispatch.role}
- **工单类型**: ${bundle.dispatch.kind}
- **尝试轮次**: Attempt ${bundle.dispatch.attempt}
- **目标代码版本**: ${bundle.dispatch.version || '首轮代码生成'}

## 3. 指令说明
${bundle.instructions}

## 4. 预期返回格式 (请填写后提交)
\`\`\`json
${JSON.stringify(bundle.template, null, 2)}
\`\`\`
`;
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(getMarkdownText());
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="modal-overlay">
      <div className="modal-dialog" style={{ maxWidth: '750px' }}>
        <div className="modal-header">
          <h3>复制 Agent 交接包 (Handoff Package)</h3>
          <button className="btn-secondary" onClick={onClose}>✕</button>
        </div>
        <div className="modal-body">
          <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
            当需要向未接通自动化的 Agent (如 Antigravity) 传递任务时，可直接复制以下 Markdown 说明与模板。
          </div>

          {errorMsg && <div className="alert-banner alert-danger">{errorMsg}</div>}

          {loading ? (
            <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>
              正在生成交接包...
            </div>
          ) : (
            <pre style={{ minHeight: '380px', fontSize: '12px' }}>
              {getMarkdownText()}
            </pre>
          )}
        </div>
        <div className="modal-footer">
          <button className="btn btn-secondary" onClick={onClose}>
            关闭
          </button>
          <button className="btn btn-primary" onClick={handleCopy} disabled={loading}>
            {copied ? '✓ 已复制到剪贴板' : '📋 复制全部内容'}
          </button>
        </div>
      </div>
    </div>
  );
};
