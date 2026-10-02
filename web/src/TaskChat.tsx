import React, { useEffect, useState } from 'react';
import { request } from './api';
import { AgentInfo } from './types';

type Message = {
  id: string;
  content: string;
  recipient: string;
  status: string;
  error?: string;
  replies: { role: string; content: string; report?: { data?: { proposals?: {
    title: string; work_kind: 'code' | 'analysis'; assignee: string; reason: string;
  }[] } } }[];
};

const statusLabels: Record<string, string> = {
  QUEUED: '⏳ 排队中 (等待就绪后派发)',
  RUNNING: '⚡ 正在响应 (请查看执行详情)',
  DELIVERED: '✓ 已回复',
  BLOCKED: '⚠ 回复中断，需处理',
  NEEDS_REVIEW: '📝 需求已变更，需重新确认',
};

const PROMPT_SUGGESTIONS = [
  '请各位 Agent 共同评估当前需求方案，讨论最佳架构与分工。',
  '请各位分析当前方案的潜在缺陷与边界条件，只指出最重要的三项。',
  '请根据各自实际工具能力说明愿意承担的任务及理由，不要按品牌固定分工。',
  '请提出独立验收标准和测试场景，说明哪些验证需要额外权限。',
];

export function TaskChat({
  taskId,
  agents,
  completed,
  onPending,
}: {
  taskId: string;
  agents: AgentInfo[];
  completed: boolean;
  onPending: (pending: boolean) => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState(() => {
    try {
      return localStorage.getItem('wt:chat:' + taskId) || '';
    } catch {
      return '';
    }
  });
  const [recipient, setRecipient] = useState('all');
  const [id, setId] = useState(() => crypto.randomUUID());
  const [error, setError] = useState('');
  const [sending, setSending] = useState(false);
  const [tick, setTick] = useState(0);
  const bothReady = ['codex', 'antigravity'].every(id => agents.some(a => a.id === id && a.ready));
  useEffect(() => {
    if ((recipient === 'both' && !bothReady) ||
        (!['all', 'both'].includes(recipient) && !agents.some(a => a.id === recipient && a.ready))) {
      setRecipient('all');
      setId(crypto.randomUUID());
    }
  }, [agents, recipient, bothReady]);

  const agentMap = React.useMemo(() => {
    const map: Record<string, AgentInfo> = {};
    for (const a of agents) {
      map[a.id] = a;
    }
    return map;
  }, [agents]);

  // Poll messages
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const data = await request<{ messages: Message[] }>(`/tasks/${taskId}/messages`);
        if (alive) {
          setMessages(data.messages);
          onPending(data.messages.some(m => ['QUEUED', 'RUNNING'].includes(m.status)));
        }
      } catch (e) {
        if (alive) setError((e as Error).message);
      } finally {
        if (alive) timer = setTimeout(load, 2000);
      }
    }
    load();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [taskId, tick, onPending]);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    if (sending || !text.trim()) return;
    setSending(true);
    setError('');
    try {
      await request(`/tasks/${taskId}/messages`, {
        method: 'POST',
        body: JSON.stringify({ message_id: id, recipient, content: text.trim() }),
      });
      setText('');
      setId(crypto.randomUUID());
      onPending(true);
      setTick(n => n + 1);
      try {
        localStorage.removeItem('wt:chat:' + taskId);
      } catch {
        /* Optional storage */
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  }

  function getRecipientLabel(rec: string) {
    if (rec === 'all') return '📢 本任务已就绪成员';
    if (rec === 'both') return '🤝 Codex & Antigravity (双向评审)';
    const a = agentMap[rec];
    if (a) return `${a.avatar} ${a.name}`;
    return rec;
  }

  return (
    <section className="card" aria-label="多Agent协作与讨论">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <h2>多 Agent 协同讨论</h2>
        <div className="agents-strip">
          {agents.map(a => (
            <span
              key={a.id}
              className={`agent-pill ${a.enabled ? 'active' : ''}`}
              style={{ color: a.color }}
              title={a.description}
            >
              <span className="agent-avatar">{a.avatar}</span>
              <span>{a.name}</span>
            </span>
          ))}
        </div>
      </div>

      {messages.length === 0 ? (
        <div className="empty" style={{ padding: '24px 16px' }}>
          暂无补充对话。这里每位成员依次回复一次；并行讨论、认领和执行请使用任务上方的团队流程。
        </div>
      ) : (
        messages.map(m => (
          <div className="message" key={m.id}>
            <div className="message-heading" style={{ justifyContent: 'space-between' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span className="avatar">👤</span>
                <strong>你 → {getRecipientLabel(m.recipient)}</strong>
              </div>
              <span className="badge">{statusLabels[m.status] || m.status}</span>
            </div>
            <p className="prose" style={{ marginTop: 8 }}>{m.content}</p>
            {m.error && <p className="error small" style={{ marginTop: 6 }}>{m.error}</p>}

            {/* Agent Replies Stream */}
            {m.replies.map((r, i) => {
              const a = agentMap[r.role];
              const name = a?.name || r.role;
              const avatar = a?.avatar || '🤖';
              const color = a?.color || '#6366f1';
              return (
                <div key={i} className="reply-item" style={{ borderLeftColor: color }}>
                  <div className="reply-header">
                    <div className="reply-header-info">
                      <span className="agent-avatar">{avatar}</span>
                      <strong style={{ color }}>{name}</strong>
                      <span className="agent-tag" style={{ background: color + '1a', color }}>
                        {a?.description ? a.description.slice(0, 18) : '协作者'}
                      </span>
                    </div>
                    <span className="small muted">发言 #{i + 1}</span>
                  </div>
                  <p className="prose" style={{ margin: 0, fontSize: 14 }}>{r.content}</p>
                  {!!r.report?.data?.proposals?.length && (
                    <ul className="small">
                      {r.report.data.proposals.map((proposal, index) => (
                        <li key={index}>{proposal.title} · {proposal.work_kind === 'analysis' ? '分析' : '开发'}
                          {' → '}{agentMap[proposal.assignee]?.name || proposal.assignee}：{proposal.reason}</li>
                      ))}
                    </ul>
                  )}
                </div>
              );
            })}
          </div>
        ))
      )}

      {/* Message Composer */}
      <form onSubmit={send} className="stack" style={{ marginTop: 20 }}>
        <p className="small muted">每位参与者只回应一次；后发言者会看到前面的简要回复。自定义云端 Agent 会收到本任务需求、消息和必要的对话上下文。</p>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <label style={{ flexShrink: 0, fontWeight: 500 }}>发给</label>
          <select
            className="chat-recipient-select"
            value={recipient}
            disabled={sending}
            onChange={e => {
              setRecipient(e.target.value);
              setId(crypto.randomUUID());
            }}
          >
            <option value="all">📢 本任务已就绪成员（依次回复）</option>
            {bothReady && <option value="both">🤝 Codex + Antigravity</option>}
            {agents
              .filter(a => a.enabled && a.ready)
              .map(a => (
                <option key={a.id} value={a.id}>
                  {a.avatar} @{a.name} ({a.description ? a.description.slice(0, 16) : a.id})
                </option>
              ))}
          </select>
        </div>

        {/* Quick Suggestion Chips */}
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', margin: '4px 0' }}>
          {PROMPT_SUGGESTIONS.map((s, idx) => (
            <button
              key={idx}
              type="button"
              className="small"
              style={{
                padding: '3px 8px',
                minHeight: 'auto',
                fontSize: 12,
                borderRadius: 6,
                background: 'var(--surface-alt)',
                border: '1px solid var(--border)',
                cursor: 'pointer',
              }}
              onClick={() => {
                setText(s);
                setId(crypto.randomUUID());
              }}
            >
              💡 {s.slice(0, 22)}...
            </button>
          ))}
        </div>

        <textarea
          aria-label="对话消息"
          disabled={sending}
          placeholder="提出疑问、让各方讨论技术方案，或让 DeepSeek / Codex / Antigravity 分工探索…"
          value={text}
          onChange={e => {
            setText(e.target.value);
            setId(crypto.randomUUID());
            try {
              localStorage.setItem('wt:chat:' + taskId, e.target.value);
            } catch {
              /* Optional storage */
            }
          }}
        />

        <p className="small muted">
          支持广播多方讨论或指定 Agent 单独交流。消息用于沟通共识，不自动修改已定稿方案或直接触发代码变更。
        </p>

        {completed && (
          <p className="notice">任务已完成，仍可讨论；继续开发需另行修改需求并重新生成方案。</p>
        )}

        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}

        <button className="primary" disabled={sending || !text.trim()}>
          {sending ? '正在发送…' : '发送消息'}
        </button>
      </form>
    </section>
  );
}
