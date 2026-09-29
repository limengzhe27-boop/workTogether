import React, { useEffect, useState } from 'react';
import { request } from './api';
type Message = { id: string; content: string; recipient: string; status: string; error?: string; replies: { role: string; content: string }[] };
const status: Record<string, string> = { QUEUED: '已保存，等待空闲后回复', RUNNING: '已派发，请查看执行详情确认运行状态', DELIVERED: '已回复', BLOCKED: '回复失败，需处理', NEEDS_REVIEW: '需求已变更，请重新确认后发送' };
export function TaskChat({ taskId, completed, onPending }: { taskId: string; completed: boolean; onPending: (pending: boolean) => void }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState(() => { try { return localStorage.getItem('wt:chat:' + taskId) || ''; } catch { return ''; } });
  const [recipient, setRecipient] = useState('antigravity');
  const [id, setId] = useState(() => crypto.randomUUID());
  const [error, setError] = useState('');
  const [sending, setSending] = useState(false);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true; let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const data = await request<{ messages: Message[] }>(`/tasks/${taskId}/messages`);
        if (alive) { setMessages(data.messages); onPending(data.messages.some(m => ['QUEUED', 'RUNNING'].includes(m.status))); }
      } catch (e) { if (alive) setError((e as Error).message); }
      finally { if (alive) timer = setTimeout(load, 2000); }
    }
    load(); return () => { alive = false; clearTimeout(timer); };
  }, [taskId, tick, onPending]);
  async function send(e: React.FormEvent) {
    e.preventDefault(); if (sending || !text.trim()) return;
    setSending(true); setError('');
    try {
      await request(`/tasks/${taskId}/messages`, { method: 'POST', body: JSON.stringify({ message_id: id, recipient, content: text.trim() }) });
      setText(''); setId(crypto.randomUUID()); onPending(true); setTick(n => n + 1);
      try { localStorage.removeItem('wt:chat:' + taskId); } catch { /* Optional storage */ }
    } catch (e) { setError((e as Error).message); }
    finally { setSending(false); }
  }
  return <section className="card" aria-label="任务内对话"><h2>任务内对话</h2>
    {messages.map(m => <div className="message" key={m.id}><strong>你 → {m.recipient === 'both' ? '双方' : m.recipient}</strong><p className="prose">{m.content}</p><p className="small">{status[m.status] || m.status}{m.error && '：' + m.error}</p>{m.replies.map((r, i) => <div key={i} className="suggestion"><strong>{r.role === 'codex' ? 'Codex' : 'Antigravity'}</strong><p className="prose">{r.content}</p></div>)}</div>)}
    <form onSubmit={send} className="stack"><label>发给<select value={recipient} disabled={sending} onChange={e => { setRecipient(e.target.value); setId(crypto.randomUUID()); }}><option value="antigravity">Antigravity</option><option value="codex">Codex</option><option value="both">双方（各回复一次）</option></select></label>
      <textarea aria-label="对话消息" disabled={sending} placeholder="询问方案、提出疑问或让双方讨论一个具体问题…" value={text} onChange={e => { setText(e.target.value); setId(crypto.randomUUID()); try { localStorage.setItem('wt:chat:' + taskId, e.target.value); } catch { /* Optional storage */ } }} />
      <p className="small">发送会使用所选 Agent 的额度；暂停时只排队。消息不会自动修改需求、批准方案或启动开发。默认 Antigravity，减少 Codex 用量。</p>
      {completed && <p className="notice">任务已完成，仍可讨论；继续开发需另行修改需求。</p>}
      {error && <p role="alert" className="error">{error}</p>}<button className="primary" disabled={sending || !text.trim()}>{sending ? '正在保存…' : '发送消息'}</button>
    </form></section>;
}
