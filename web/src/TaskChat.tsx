import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { api, request } from './api';
import { AgentInfo, ProjectFileEntry } from './types';
import { ArrowUp, Copy, Check, Paperclip, X, RefreshCw, ShieldAlert } from 'lucide-react';
import { MessageContent } from './MessageContent';

type ReplyReport = {
  basis?: string[];
  suggestions?: string[];
  next_action?: string;
  data?: {
    proposals?: {
      title: string;
      work_kind: 'code' | 'analysis';
      assignee: string;
      reason: string;
    }[];
  };
};

type Message = {
  id: string;
  created_at?: number;
  content: string;
  recipient: string;
  status: string;
  error?: string;
  wait_reason?: string | null;
  sender?: string;
  intent?: 'ask' | 'assign' | 'claim' | 'report';
  auto?: boolean;
  held?: boolean;
  round?: number;
  rounds?: number;
  allow_write?: boolean;
  write_authorized?: boolean;
  room_members?: string[];
  context_files?: string[];
  blocked_role?: string;
  blocked_roles?: string[];
  active_roles?: string[];
  pending_roles?: string[];
  failed_roles?: string[];
  write_role?: string | null;
  replies: { role: string; content: string; report?: ReplyReport; skipped?: boolean; round?: number; ok?: boolean;
    changed_files?: { path: string; status: string }[] }[];
};

type ChatState = {
  allow_write: boolean;
  max_auto_rounds: number;
  auto_rounds_used: number;
  held: number;
  available_members?: string[];
};

const intentLabels: Record<string, string> = {
  ask: '提问',
  assign: '派活',
  claim: '认领',
  report: '汇报',
};

const statusLabels: Record<string, string> = {
  QUEUED: '等待发送',
  RUNNING: '正在回复',
  DELIVERED: '已回复',
  BLOCKED: '回复中断',
  NEEDS_REVIEW: '待确认',
  CANCELLED: '已取消',
};

const waitLabels: Record<string, string> = {
  TASK_PAUSED: '当前会话已暂停，恢复后发送。',
  TASK_BLOCKED: '当前会话有未解决的问题，请先查看阻塞原因。',
  DISPATCH_UNRESOLVED: '上一项执行尚未结束，请稍候。',
  WORKFLOW_RUNNING: '正在等待后台流程结束。',
  RUN_UNRESOLVED: '有运行结果尚未确认。',
  EARLIER_MESSAGE: '正在等待上一条消息回复完成。',
  REQUIREMENT_CHANGED: '上下文已更新，需要重新确认。',
  AGENT_UNAVAILABLE: '选中的 Agent 未就绪或已停用，请检查 Agent 设置。',
  MEMBER_UNRESOLVED: '该 Agent 有未解决的失败记录。',
  ROOM_SCOPE_CHANGED: '这条消息的成员授权已失效或旧消息缺少范围。请跳过后重新选择成员发送。',
  AUTO_ROUND_BUDGET: '自动讨论已达上限，等你确认后继续。',
};

function getAgentBadge(role: string, name: string): { code: string; color: string } {
  if (role === 'codex') {
    return { code: 'CX', color: '#6366f1' };
  }
  if (role === 'antigravity') {
    return { code: 'AG', color: '#10b981' };
  }
  if (role.startsWith('deepseek')) {
    return { code: 'DS', color: '#f59e0b' };
  }
  return {
    code: name.slice(0, 2).toUpperCase(),
    color: '#38bdf8',
  };
}

export function ChatScopeNotice({ names, writable, loaded }: { names: string[]; writable: boolean; loaded: boolean }) {
  return <div className="small compose-notice" aria-label="本次协作范围">
    <p>本次房间：{names.length ? names.join('、') : '未选择成员'}。后续讨论和派活仅限这些成员；切换选择只影响新消息。</p>
    <p>{!loaded ? '正在读取授权，暂不能发送。' : writable ? '允许新消息申请写入，每轮只有一位写者。' : '当前只读，派活也不会获得写权限。'}
      关闭写入会撤销未启动的旧授权；重新打开不会恢复旧授权，已启动的进程不会被强行终止。</p>
  </div>;
}

export function TaskChat({
  taskId,
  projectId,
  agents,
  providers,
  completed,
  onPending,
  onDetails,
  visible,
  children,
}: {
  taskId: string;
  projectId?: string;
  agents: AgentInfo[];
  providers?: Record<string, { available: boolean; executable: string }> | null;
  completed?: boolean;
  onPending: (pending: boolean) => void;
  onDetails: () => void;
  onTeam?: () => void;
  visible: boolean;
  children?: React.ReactNode;
}) {
  const feed = useRef<HTMLDivElement>(null);
  const nearBottom = useRef(true);
  const previousVersion = useRef('');
  const [unseen, setUnseen] = useState(false);
  const [copied, setCopied] = useState('');
  const [expandedReplies, setExpandedReplies] = useState<Record<string, boolean>>({});
  const [loadError, setLoadError] = useState('');
  const [loaded, setLoaded] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [chat, setChat] = useState<ChatState>({ allow_write: false, max_auto_rounds: 2, auto_rounds_used: 0, held: 0 });
  const [text, setText] = useState(() => {
    try {
      return localStorage.getItem('wt:chat:' + taskId) || '';
    } catch {
      return '';
    }
  });
  const [selectedIds, setSelectedIds] = useState<string[] | null>(null);
  const [id, setId] = useState(() => crypto.randomUUID());
  const [error, setError] = useState('');
  const [sending, setSending] = useState(false);
  const [tick, setTick] = useState(0);
  const [rounds, setRounds] = useState(1);
  const [contextFiles, setContextFiles] = useState<string[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [projectFiles, setProjectFiles] = useState<ProjectFileEntry[]>([]);
  const [filesLoading, setFilesLoading] = useState(false);
  const [fileFilter, setFileFilter] = useState('');
  const [resolving, setResolving] = useState('');
  const [releasing, setReleasing] = useState(false);
  const [changingWrite, setChangingWrite] = useState(false);

  const readyAgents = React.useMemo(() => agents.filter(a => a.enabled && a.ready
    && (!chat.available_members || chat.available_members.includes(a.id))), [agents, chat.available_members]);
  const readyIds = React.useMemo(() => readyAgents.map(a => a.id), [readyAgents]);
  const missingCli = React.useCallback((agent: AgentInfo) => {
    if (agent.type !== 'cli' || !providers) return '';
    const info = providers[agent.base_agent || agent.id];
    return info && !info.available ? info.executable : '';
  }, [providers]);
  const activeAgentIds = React.useMemo(() => {
    if (selectedIds === null) return readyIds;
    return readyIds.filter(id => selectedIds.includes(id));
  }, [selectedIds, readyIds]);
  const isAllSelected = readyIds.length > 0 && activeAgentIds.length === readyIds.length;

  function toggleAllAgents() {
    if (isAllSelected) {
      setSelectedIds([]);
    } else {
      setSelectedIds([...readyIds]);
    }
    setId(crypto.randomUUID());
  }

  function toggleOneAgent(agentId: string) {
    if (activeAgentIds.includes(agentId)) {
      setSelectedIds(activeAgentIds.filter(x => x !== agentId));
    } else {
      setSelectedIds(readyIds.filter(x => x === agentId || activeAgentIds.includes(x)));
    }
    setId(crypto.randomUUID());
  }

  const recipientValue = React.useMemo(() => {
    if (activeAgentIds.length === 0) return '';
    // Send the explicit UI snapshot. A newly enabled Agent must not be silently added.
    if (activeAgentIds.length === 1) return activeAgentIds[0];
    return activeAgentIds.join(',');
  }, [activeAgentIds, readyIds]);

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
        const data = await request<{ messages: Message[]; chat?: ChatState }>(`/tasks/${taskId}/messages`);
        if (alive) {
          setMessages(data.messages);
          if (data.chat) setChat(data.chat);
          setSelectedIds(current => current ?? data.chat?.available_members ?? []);
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
    if (!loaded || changingWrite || sending || !text.trim() || !recipientValue) return;
    setSending(true);
    setError('');
    try {
      const payload: Record<string, unknown> = { message_id: id, recipient: recipientValue, content: text.trim(), allow_write: chat.allow_write };
      if (contextFiles.length > 0) payload.context_files = contextFiles;
      if (rounds === 2) payload.rounds = 2;
      await request(`/tasks/${taskId}/messages`, {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      nearBottom.current = true;
      setText('');
      setId(crypto.randomUUID());
      setContextFiles([]);
      setRounds(1);
      setPickerOpen(false);
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

  async function toggleSessionWrite() {
    if (!loaded || changingWrite) return;
    const next = !chat.allow_write;
    if (next && !window.confirm('允许随后发送的已选成员修改当前项目文件？开启不会立即调用模型，之前只读或已撤销的消息不会自动获得写权限。')) return;
    setChangingWrite(true);
    setError('');
    try {
      const state = await request<ChatState>(`/tasks/${taskId}/chat-config`, {
        method: 'POST',
        body: JSON.stringify({ allow_write: next }),
      });
      setChat(current => ({ ...current, ...state }));
      setId(crypto.randomUUID());
      setTick(n => n + 1);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setChangingWrite(false);
    }
  }

  async function releaseAgentMessages() {
    if (releasing) return;
    setReleasing(true);
    setError('');
    try {
      await request(`/tasks/${taskId}/chat-release`, { method: 'POST', body: '{}' });
      setTick(n => n + 1);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setReleasing(false);
    }
  }

  async function resolveMessage(messageId: string, action: 'skip' | 'retry') {
    if (resolving) return;
    setResolving(messageId + action);
    setError('');
    try {
      await api.resolveMessage(taskId, messageId, action);
      setTick(n => n + 1);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setResolving('');
    }
  }

  async function togglePicker() {
    const next = !pickerOpen;
    setPickerOpen(next);
    if (!next || !projectId || projectFiles.length > 0) return;
    setFilesLoading(true);
    try {
      const result = await api.getProjectFiles(projectId);
      setProjectFiles(result.files);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setFilesLoading(false);
    }
  }

  function toggleContextFile(path: string) {
    setContextFiles(current => current.includes(path)
      ? current.filter(item => item !== path)
      : current.length >= 10 ? current : [...current, path]);
    setId(crypto.randomUUID());
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
    if (rec === 'all') return '全部';
    if (rec === 'both') return 'Codex + Antigravity';
    if (rec.includes(',')) {
      return rec.split(',').map(part => agentMap[part]?.name || part).join(' + ');
    }
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
            <p>在下方选择要对话的 Agent（支持多选或全选），直接发送消息开始交流。</p>
          </div>}
          {messages.map(m => {
            const isAgent = (m.sender || 'user') !== 'user';
            const senderAgent = isAgent ? agentMap[m.sender!] : undefined;
            const senderName = senderAgent?.name || m.sender || '';
            const senderColor = senderAgent?.color || '#3072d2';
            const activeRoles = m.active_roles || [];
            const pendingRoles = m.pending_roles || [];
            return <article className={'chat-turn' + (isAgent ? ' peer-turn' : '')} key={m.id}>
            {typeof m.created_at === 'number' && <time className="message-time" dateTime={new Date(m.created_at * 1000).toISOString()}>
              {new Date(m.created_at * 1000).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
            </time>}
            {isAgent ? (
              <div className="peer-message" style={{ '--agent-color': senderColor } as React.CSSProperties}>
                <div className="peer-message-head">
                  <span className="agent-monogram" style={{ background: senderColor }}>
                    {(senderAgent?.avatar || senderName.slice(0, 2)).slice(0, 2)}
                  </span>
                  <strong style={{ color: senderColor }}>{senderName}</strong>
                  <span className="peer-arrow" aria-hidden="true">→</span>
                  <span className="peer-target">{getRecipientLabel(m.recipient)}</span>
                  {m.intent && <span className="intent-badge">{intentLabels[m.intent] || m.intent}</span>}
                  {m.held && <span className="intent-badge is-held">等待你确认</span>}
                </div>
                <p className="prose">{m.content}</p>
                {m.held && <p className="small muted">这条 Agent 之间的消息被自动轮次上限拦下了，点下方“让他们继续”才会送达。</p>}
              </div>
            ) : (
              <div className="user-message">
                <span className="sr-only">你，发给 {getRecipientLabel(m.recipient)}</span>
                <p className="prose">{m.content}</p>
                <div className="user-message-meta">
                  {m.recipient !== 'all' && <span className="small">@{getRecipientLabel(m.recipient)}</span>}
                  {m.allow_write && <span className="message-flag" title="仅对本条消息及其授权成员的后续派发生效">{m.write_authorized ? '已授权写入' : '旧写授权已失效'}</span>}
                  {m.room_members && <span className="small" title="发送时固定，后续派活不能添加其他成员">房间：{m.room_members.map(role => agentMap[role]?.name || role).join('、')}</span>}
                  {!!m.rounds && m.rounds > 1 && <span className="message-flag">{m.rounds} 轮</span>}
                  {!!m.context_files?.length && <span className="message-flag" title={m.context_files.join('\n')}>
                    <Paperclip aria-hidden="true" />{m.context_files.length} 个文件
                  </span>}
                  {m.status !== 'DELIVERED' && <span className="message-status small">{statusLabels[m.status] || m.status}</span>}
                </div>
              </div>
            )}
            {m.error && <p className={m.status === 'CANCELLED' ? 'wait-note small' : 'error small'}
              role={m.status === 'CANCELLED' ? undefined : 'alert'}>{m.error} <button type="button" onClick={onDetails}>查看详情</button></p>}
            {m.status === 'RUNNING' && (
              <div className="round-status small" role="status">
                <span className="round-chip">第 {m.round || 1} 轮 / 共 {m.rounds || 1} 轮</span>
                {activeRoles.length > 1 && <span className="round-chip is-parallel">{activeRoles.length} 位同时回复中</span>}
                {activeRoles.map(role => <span key={role} className="round-chip is-active">
                  <span className="agent-dot" style={{ background: agentMap[role]?.color || '#3072d2' }} />
                  {agentMap[role]?.name || role} 正在回复…
                </span>)}
                {pendingRoles.map(role => <span key={role} className="round-chip">
                  {agentMap[role]?.name || role} 等待中
                </span>)}
                {m.write_role && <span className="round-chip is-write">
                  {agentMap[m.write_role]?.name || m.write_role} 持有项目写入
                </span>}
              </div>
            )}
            {(m.status === 'BLOCKED' || (m.status === 'QUEUED'
              && ['AGENT_UNAVAILABLE', 'MEMBER_UNRESOLVED', 'ROOM_SCOPE_CHANGED'].includes(m.wait_reason || ''))) && (
              <div className="message-recovery small">
                <span>{m.blocked_role
                  ? `${agentMap[m.blocked_role]?.name || m.blocked_role} 的回复中断。`
                  : m.status === 'BLOCKED' ? '回复中断。' : waitLabels[m.wait_reason || ''] || '这一条没有 Agent 能接。'}</span>
                {m.status === 'BLOCKED' && <button type="button" disabled={!!resolving} onClick={() => resolveMessage(m.id, 'retry')}>
                  <RefreshCw aria-hidden="true" />{resolving === m.id + 'retry' ? '重试中…' : '重试'}
                </button>}
                <button type="button" disabled={!!resolving} onClick={() => resolveMessage(m.id, 'skip')}>
                  {resolving === m.id + 'skip' ? '跳过中…' : '跳过并继续'}
                </button>
              </div>
            )}
            {m.status === 'QUEUED' && m.wait_reason && !['AGENT_UNAVAILABLE', 'MEMBER_UNRESOLVED', 'ROOM_SCOPE_CHANGED'].includes(m.wait_reason) && (
              <p className="wait-note small" role="status">
                {waitLabels[m.wait_reason] || m.wait_reason}
                <button type="button" onClick={onDetails}>查看详情</button>
              </p>
            )}
            <div className="turn-replies">
              {(() => {
                const roundList = Array.from(new Set(m.replies.map(r => r.round || 1))).sort((a, b) => a - b);
                return m.replies.map((r, i) => {
                const replyRound = r.round || 1;
                const firstOfRound = m.replies.findIndex(x => (x.round || 1) === replyRound) === i;
                const ag = agentMap[r.role];
                const name = ag?.name || r.role;
                const badge = getAgentBadge(r.role, name);
                const color = ag?.color || badge.color;
                const replyKey = `${m.id}:${i}`;
                const isLong = r.content.length > 340 || r.content.split('\n').length > 8;
                const isExpanded = !!expandedReplies[replyKey];
                const basis = r.report?.basis?.filter(Boolean) || [];
                const suggestions = r.report?.suggestions?.filter(Boolean) || [];
                const metaCount = basis.length + suggestions.length;
                const roundLabel = replyRound === 1 ? '第 1 轮 · 各自独立回答' : `第 ${replyRound} 轮 · 看过同伴后回应`;

                return <div key={i}>
                  {roundList.length > 1 && firstOfRound && <p className="round-divider" role="presentation">{roundLabel}</p>}
                  <div className="agent-message" data-agent={r.role} style={{ '--agent-color': color } as React.CSSProperties}>
                  <div className="reply-heading">
                    <span className="agent-monogram" style={{ background: color }}>{badge.code}</span>
                    <strong className="reply-agent-name" style={{ color }}>{name}</strong>
                    {!!r.changed_files?.length && (
                      <span className="change-badge" title={r.changed_files.map(f => f.path).join('\n')}>
                        改了 {r.changed_files.length} 个文件
                      </span>
                    )}
                    <div className="reply-heading-actions">
                      <button type="button" className="icon-button" aria-label={copied === replyKey ? '已复制回复' : '复制 ' + name + ' 的回复'} title="复制回复"
                        onClick={async () => {
                          try { await navigator.clipboard.writeText(r.content); setCopied(replyKey); }
                          catch { setError('无法自动复制，请手动选择回复文字并复制。'); }
                        }}>{copied === replyKey ? <Check /> : <Copy />}</button>
                    </div>
                  </div>
                  <div className={'agent-message-body ' + (isLong && !isExpanded ? 'is-clamped' : '')}>
                    <MessageContent text={r.content} />
                  </div>
                  {isLong && (
                    <button type="button" className="reply-expand-btn" onClick={() => setExpandedReplies(prev => ({ ...prev, [replyKey]: !isExpanded }))}>
                      {isExpanded ? '收起长文' : '展开完整回复'}
                    </button>
                  )}
                  {(metaCount > 0 || !!r.report?.data?.proposals?.length || !!r.changed_files?.length) && (
                    <div className="reply-folds">
                      {!!r.changed_files?.length && (
                        <details className="reply-meta-box" open>
                          <summary>本轮改动 · {r.changed_files.length} 个文件</summary>
                          <ul>{r.changed_files.map(file => <li key={file.path}>
                            <code className="diff-status">{file.status === 'added' ? 'A' : file.status === 'removed' ? 'D' : 'M'}</code>
                            {' '}<span className="diff-path" title={file.path}>{file.path}</span>
                          </li>)}</ul>
                        </details>
                      )}
                      {metaCount > 0 && (
                        <details className="reply-meta-box">
                          <summary>依据与建议 ({metaCount})</summary>
                          {basis.length > 0 && (
                            <div className="meta-group">
                              <span className="meta-label">判断依据</span>
                              <ul>{basis.map((b, idx) => <li key={idx}>{b}</li>)}</ul>
                            </div>
                          )}
                          {suggestions.length > 0 && (
                            <div className="meta-group">
                              <span className="meta-label">建议下一步</span>
                              <ul>{suggestions.map((s, idx) => <li key={idx}>{s}</li>)}</ul>
                            </div>
                          )}
                        </details>
                      )}
                      {!!r.report?.data?.proposals?.length && <details className="reply-meta-box">
                        <summary>协作建议 ({r.report.data.proposals.length})</summary>
                        <ul>{r.report.data.proposals.map((proposal, index) => <li key={index}>
                          <strong>{proposal.title}</strong>
                          {' → '}{agentMap[proposal.assignee]?.name || proposal.assignee}：{proposal.reason}
                        </li>)}</ul>
                      </details>}
                    </div>
                  )}
                </div>
                </div>;
              });})()}
            </div>
          </article>;})}
        </div>
      </div>
      {unseen && <button type="button" className="new-message-link" onClick={() => {
        if (feed.current) feed.current.scrollTop = feed.current.scrollHeight;
        nearBottom.current = true; setUnseen(false);
      }}>有新动态 · 回到底部</button>}
      {chat.held > 0 && (
        <div className="held-banner" role="status">
          <span>有 {chat.held} 条 Agent 消息等待你确认（自动派发已用 {chat.auto_rounds_used}/{chat.max_auto_rounds} 条）。继续不恢复已撤销的写权限。</span>
          <button type="button" disabled={releasing} onClick={releaseAgentMessages}>
            <RefreshCw aria-hidden="true" />{releasing ? '送达中…' : '让他们继续'}
          </button>
        </div>
      )}
      <div className="chat-compose-wrap">
        {loadError && <p className="error small" role="alert">对话读取失败：{loadError}</p>}
        {error && <p role="alert" className="error small">{error}</p>}
        {completed && <p className="small compose-notice">任务已完成，可继续自由对话。</p>}
        <form onSubmit={send} className="chat-composer">
          {contextFiles.length > 0 && (
            <div className="context-chips" aria-label="已引用的项目文件">
              {contextFiles.map(path => <span key={path} className="context-chip">
                <Paperclip aria-hidden="true" />
                <span title={path}>{path}</span>
                <button type="button" aria-label={'移除引用 ' + path} onClick={() => toggleContextFile(path)}><X aria-hidden="true" /></button>
              </span>)}
            </div>
          )}
          <textarea aria-label="对话消息" disabled={sending} rows={2}
            placeholder="输入消息… (⌘ + Enter 发送)"
            value={text} onKeyDown={e => {
              if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && !e.nativeEvent.isComposing) {
                e.preventDefault(); e.currentTarget.form?.requestSubmit();
              }
            }}
            onChange={e => {
              setText(e.target.value); setId(crypto.randomUUID());
              try { localStorage.setItem('wt:chat:' + taskId, e.target.value); } catch { /* Optional storage */ }
            }} />
          {pickerOpen && (
            <div className="file-picker">
              <div className="file-picker-head">
                <strong>引用项目文件</strong>
                <span className="small muted">{projectId ? '最多 10 个，内容随本条消息发送' : '未选择项目'}</span>
              </div>
              <input type="search" aria-label="过滤项目文件" placeholder="过滤文件名…"
                value={fileFilter} onChange={e => setFileFilter(e.target.value)}
                onKeyDown={e => { if (e.key === 'Escape') { e.preventDefault(); setPickerOpen(false); } }} />
              {filesLoading && <p className="small" role="status">正在读取文件列表…</p>}
              {!filesLoading && projectFiles.length === 0 && <p className="small muted">没有可引用的文件。</p>}
              <ul className="file-picker-list">
                {projectFiles
                  .filter(file => file.path.toLocaleLowerCase().includes(fileFilter.trim().toLocaleLowerCase()))
                  .slice(0, 200)
                  .map(file => <li key={file.path}>
                    <label>
                      <input type="checkbox" checked={contextFiles.includes(file.path)}
                        onChange={() => toggleContextFile(file.path)} />
                      <span title={file.path}>{file.path}</span>
                      <span className="small muted">{file.size} B</span>
                    </label>
                  </li>)}
              </ul>
            </div>
          )}
          <div className="chat-composer-footer">
            <div className="agent-multiselect-bar" role="group" aria-label="选择对话 Agent">
              <button
                type="button"
                className={'agent-select-pill select-all-pill ' + (isAllSelected ? 'is-selected' : '')}
                aria-pressed={isAllSelected}
                disabled={sending || readyAgents.length === 0}
                onClick={toggleAllAgents}
                title={isAllSelected ? '再次点击取消全选' : '点击全选所有就绪 Agent'}
              >
                <span className="pill-check">{isAllSelected ? '✓' : ''}</span>
                <span>全部</span>
              </button>
              {readyAgents.map(a => {
                const selected = activeAgentIds.includes(a.id);
                const color = a.color || '#3072d2';
                const missing = missingCli(a);
                return (
                  <button
                    key={a.id}
                    type="button"
                    className={'agent-select-pill ' + (selected ? 'is-selected' : '') + (missing ? ' is-unavailable' : '')}
                    aria-pressed={selected}
                    disabled={sending}
                    style={{ '--pill-color': color } as React.CSSProperties}
                    title={missing ? `本机未找到 ${missing}，发送会立即失败` : undefined}
                    onClick={() => toggleOneAgent(a.id)}
                  >
                    <span className="agent-dot" style={{ background: color }} />
                    <span>{a.name}</span>
                    {missing && <span className="pill-warn" aria-label="本机 CLI 未找到">!</span>}
                    {selected && <span className="pill-check">✓</span>}
                  </button>
                );
              })}
            </div>
            <div className="composer-right-tools">
              <button type="button" className={'composer-tool' + (pickerOpen ? ' is-active' : '')}
                aria-pressed={pickerOpen} disabled={sending || !projectId} title="引用项目文件作为本次上下文"
                onClick={togglePicker}>
                <Paperclip aria-hidden="true" /><span>引用文件</span>
              </button>
              <label className={'composer-tool composer-toggle' + (chat.allow_write ? ' is-active' : '')}
                title="会话写入总开关：仅新发送的消息及其所选成员可继承；每轮单一写者；已撤销或只读的旧消息不会自动升级">
                <input type="checkbox" checked={chat.allow_write} disabled={sending || changingWrite || !loaded}
                  onChange={toggleSessionWrite} />
                <ShieldAlert aria-hidden="true" /><span>可在项目里工作</span>
              </label>
              <label className="composer-rounds" title="多于一位成员时，可让每人发言两轮">
                <span className="small">轮次</span>
                <select value={rounds} disabled={sending || activeAgentIds.length < 2}
                  onChange={e => { setRounds(Number(e.target.value)); setId(crypto.randomUUID()); }}>
                  <option value={1}>1</option>
                  <option value={2}>2</option>
                </select>
              </label>
              <button className="primary icon-button send-button" aria-label={sending ? '发送中' : '发送消息'} title="⌘ / Ctrl + Enter 发送" disabled={!loaded || changingWrite || sending || !text.trim() || !recipientValue}>
                <ArrowUp /><span className="sr-only">{sending ? '发送中…' : '发送消息'}</span>
              </button>
            </div>
          </div>
        </form>
        <ChatScopeNotice names={activeAgentIds.map(role => agentMap[role]?.name || role)} writable={chat.allow_write} loaded={loaded} />
        {readyAgents.length > 0 && activeAgentIds.length === 0 && <p className="small compose-notice">请点击上方「全部」或单个 Agent 选择消息接收对象。</p>}
        {!readyAgents.length && <p className="small error">没有就绪成员，请检查本任务团队和左下角 Agent 设置。</p>}
      </div>
    </section>
  );
}
