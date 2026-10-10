import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { request } from './api';
import { AgentInfo } from './types';

type Message = {
  id: string;
  content: string;
  recipient: string;
  status: string;
  error?: string;
  wait_reason?: string | null;
  replies: { role: string; content: string; report?: { data?: { proposals?: {
    title: string; work_kind: 'code' | 'analysis'; assignee: string; reason: string;
  }[] } } }[];
};

const statusLabels: Record<string, string> = {
  QUEUED: '等待发送',
  RUNNING: '正在回复',
  DELIVERED: '已回复',
  BLOCKED: '回复中断',
  NEEDS_REVIEW: '需求变更，待确认',
  CANCELLED: '已取消（保留记录）',
};

const waitLabels: Record<string, string> = {
  TASK_PAUSED: '任务已暂停，恢复后才会发送。',
  TASK_BLOCKED: '当前任务有未解决的问题，请先查看任务阻塞原因。',
  DISPATCH_UNRESOLVED: '本任务已有派发尚未结束，请查看执行详情；不要重复发送。',
  WORKFLOW_RUNNING: '正在等待本任务的团队流程让出会话。',
  RUN_UNRESOLVED: '本任务有运行结果尚未确认，请检查执行详情。',
  EARLIER_MESSAGE: '正在等待本任务较早的消息处理完毕。',
  REQUIREMENT_CHANGED: '需求已变更，需要重新确认这条消息。',
  AGENT_UNAVAILABLE: '下一位接收成员未就绪或已停用，请检查 Agent 配置。',
  MEMBER_UNRESOLVED: '接收成员在本任务中有未解决的派发，请先检查失败记录。',
};

export function TaskChat({
  taskId,
  agents,
  completed,
  onPending,
  onDetails,
  visible,
  children,
}: {
  taskId: string;
  agents: AgentInfo[];
  completed: boolean;
  onPending: (pending: boolean) => void;
  onDetails: () => void;
  visible: boolean;
  children?: React.ReactNode;
}) {
  const feed = useRef<HTMLDivElement>(null);
  const nearBottom = useRef(true);
  const previousVersion = useRef('');
  const [unseen, setUnseen] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [loaded, setLoaded] = useState(false);
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
          setLoadError('');
          setLoaded(true);
          onPending(data.messages.some(m => ['QUEUED', 'RUNNING'].includes(m.status)));
        }
      } catch (e) {
        if (alive) setLoadError((e as Error).message);
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
    if (sending || !text.trim() || !agents.some(a => a.ready)) return;
    setSending(true);
    setError('');
    try {
      await request(`/tasks/${taskId}/messages`, {
        method: 'POST',
        body: JSON.stringify({ message_id: id, recipient, content: text.trim() }),
      });
      nearBottom.current = true;
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

  const messageVersion = JSON.stringify(messages);
  useLayoutEffect(() => {
    if (!loaded) return;
    const changed = previousVersion.current !== messageVersion;
    previousVersion.current = messageVersion;
    if (!feed.current || !visible) {
      if (changed) setUnseen(true);
      return;
    }
    if (nearBottom.current) {
      feed.current.scrollTop = feed.current.scrollHeight;
      setUnseen(false);
    } else if (changed) setUnseen(true);
  }, [messageVersion, loaded, visible]);

  function getRecipientLabel(rec: string) {
    if (rec === 'all') return '本任务成员';
    if (rec === 'both') return 'Codex + Antigravity';
    return agentMap[rec]?.name || rec;
  }

  return (
    <section className="task-chat" aria-label="任务对话">
      <div className="chat-feed" ref={feed} tabIndex={0} aria-label="对话记录"
        onScroll={() => {
          const el = feed.current!;
          nearBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
          if (nearBottom.current) setUnseen(false);
        }}>
        <div className="feed-content">
          {children}
          {!loaded && !loadError && <p className="empty" role="status">正在读取对话…</p>}
          {loaded && messages.length === 0 && <div className="chat-empty">
            <h2>在这里，和团队聊一聊</h2>
            <p>补充想法、询问进展，或一起推敲方案。</p>
            <p className="small">要拆分任务并开始工作，请使用上方的“讨论方案”。</p>
          </div>}
          {messages.map(m => <article className="chat-turn" key={m.id}>
            <div className="user-message">
              <div className="message-heading"><strong>你</strong><span className="small">发给 {getRecipientLabel(m.recipient)}</span></div>
              <p className="prose">{m.content}</p>
              <span className="message-status small">{statusLabels[m.status] || m.status}</span>
            </div>
            {m.error && <p className={m.status === 'CANCELLED' ? 'wait-note small' : 'error small'}
              role={m.status === 'CANCELLED' ? undefined : 'alert'}>{m.error} <button type="button" onClick={onDetails}>查看详情</button></p>}
            {m.status === 'QUEUED' && m.wait_reason && <p className="wait-note small" role="status">
              {waitLabels[m.wait_reason] || m.wait_reason}
              <button type="button" onClick={onDetails}>查看详情</button>
            </p>}
            {m.replies.map((r, i) => {
              const name = agentMap[r.role]?.name || r.role;
              return <div key={i} className="agent-message">
                <div className="message-heading"><span className="avatar" aria-hidden="true">{name.slice(0, 1).toUpperCase()}</span><strong>{name}</strong></div>
                <div className="agent-message-body">
                  <p className="prose">{r.content}</p>
                  {!!r.report?.data?.proposals?.length && <details>
                    <summary>工作建议 · {r.report.data.proposals.length} 项</summary>
                    <ul>{r.report.data.proposals.map((proposal, index) => <li key={index}>
                      {proposal.title} · {proposal.work_kind === 'analysis' ? '分析' : '开发'}
                      {' → '}{agentMap[proposal.assignee]?.name || proposal.assignee}：{proposal.reason}
                    </li>)}</ul>
                  </details>}
                </div>
              </div>;
            })}
          </article>)}
        </div>
      </div>
      {unseen && <button type="button" className="new-message-link" onClick={() => {
        if (feed.current) feed.current.scrollTop = feed.current.scrollHeight;
        nearBottom.current = true; setUnseen(false);
      }}>有新动态 · 回到底部</button>}
      <div className="chat-compose-wrap">
        {loadError && <p className="error small" role="alert">对话读取失败：{loadError}</p>}
        {error && <p role="alert" className="error small">{error}</p>}
        {completed && <p className="small compose-notice">任务已完成，仍可交流；继续开发需修改需求并重新规划。</p>}
        <form onSubmit={send} className="chat-composer">
          <textarea aria-label="对话消息" disabled={sending} rows={2}
            placeholder="给团队发消息，讨论下一步…"
            value={text} onKeyDown={e => {
              if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && !e.nativeEvent.isComposing) {
                e.preventDefault(); e.currentTarget.form?.requestSubmit();
              }
            }}
            onChange={e => {
              setText(e.target.value); setId(crypto.randomUUID());
              try { localStorage.setItem('wt:chat:' + taskId, e.target.value); } catch { /* Optional storage */ }
            }} />
          <div className="chat-composer-footer">
            <label className="recipient-field"><span className="sr-only">消息接收者</span>
              <select aria-label="消息接收者" value={recipient} disabled={sending} onChange={e => {
                setRecipient(e.target.value); setId(crypto.randomUUID());
              }}>
                <option value="all">本任务成员</option>
                {bothReady && <option value="both">Codex + Antigravity</option>}
                {agents.filter(a => a.enabled && a.ready).map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
            </label>
            <button className="primary" title="⌘ / Ctrl + Enter 发送" disabled={sending || !text.trim() || !agents.some(a => a.ready)}>
              {sending ? '发送中…' : '发送'}
            </button>
          </div>
        </form>
        <div className="composer-meta">
          <span>只交流，不自动开发</span>
          <details className="chat-policy"><summary>发送说明</summary>
            <p>本任务的已就绪成员依次回复一次，后发言者会看到前面的简要回复。发送会消耗 Agent 额度，外部服务会接收本任务的必要上下文；不会自动修改方案或启动开发。</p>
          </details>
        </div>
        {!agents.some(a => a.ready) && <p className="small error">没有就绪成员，请先检查团队与 Agent 设置。</p>}
      </div>
    </section>
  );
}
