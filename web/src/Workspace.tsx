import React, { useEffect, useRef, useState } from 'react';
import { api, request } from './api';
import { TaskChat } from './TaskChat';
import { Project, TaskDetail, TaskSummary, RunRecord } from './types';

type Detail = TaskDetail & { revision?: number };
type Job = { id: string; status: string; error: string | null; result?: { reason?: string } };
type Approval = { digest: string; approved: boolean; requirement: string; plan: Detail['draft_plan']; revision: number };
const labels: Record<string, string> = { PLANNING: '准备方案', PLAN_REVIEW: '审查方案', PLAN_FINALIZE: '方案定稿', EXECUTING: '开发与验收', COMPLETED: '已完成', ACTIVE: '待推进', PAUSED: '已暂停', BLOCKED: '需要处理', RUNNING: '执行中', PREPARED: '等待启动确认', QUEUED: '已登记', FAILED: '失败', EXITED: '等待收集结果', DONE: '已交付', SUPERSEDED: '已失效' };
const label = (s: string) => labels[s] || s;
const post = <T,>(url: string, body = {}) => request<T>(url, { method: 'POST', body: JSON.stringify(body) });
function stored(key: string) { try { return localStorage.getItem(key) || ''; } catch { return ''; } }
function save(key: string, value: string) { try { localStorage.setItem(key, value); } catch { /* Storage is optional. */ } }

export function Workspace() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState('');
  const [taskId, setTaskId] = useState(() => stored('wt:selected-task'));
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [task, setTask] = useState<Detail | null>(null);
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [executionJobs, setExecutionJobs] = useState<Job[]>([]);
  const [approval, setApproval] = useState<Approval | null>(null);
  const [draft, setDraft] = useState('');
  const [edit, setEdit] = useState<{ text: string; revision: number; id: string } | null>(null);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState('');
  const [path, setPath] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [tick, setTick] = useState(0);
  const [navigation, setNavigation] = useState(false);
  const [tab, setTab] = useState('progress');
  const [composerRequest, setComposerRequest] = useState(0);
  const [chatPending, setChatPending] = useState(false);
  const actionLock = useRef(false);
  const selected = useRef({ project, taskId });
  selected.current = { project, taskId };
  const currentProject = projects.find(p => p.id === project);

  useEffect(() => { api.getProjects().then(r => { setProjects(r.projects); setProject(p => p || r.projects.find(x => x.id === stored('wt:selected-project'))?.id || r.projects[0]?.id || ''); }).catch(e => setError(e.message)); }, []);
  useEffect(() => { if (project) save('wt:selected-project', project); }, [project]);
  useEffect(() => { setDraft(stored('wt:draft:' + project)); }, [project]);
  useEffect(() => {
    if (!composerRequest || taskId) return;
    const input = document.getElementById('requirement');
    input?.focus({ preventScroll: true });
    input?.scrollIntoView({ block: 'center', behavior: 'auto' });
  }, [composerRequest, taskId]);
  useEffect(() => {
    let valid = true;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        if (project) {
          const result = await api.getProjectTasks(project);
          if (valid) setTasks(result.tasks);
        }
        if (taskId) {
          const [t, r, j, a, x] = await Promise.all([api.getTask(taskId), api.getRuns(taskId), request<{ jobs: Job[] }>(`/tasks/${taskId}/planning-jobs`), request<Approval>(`/tasks/${taskId}/approval`), request<{ jobs: Job[] }>(`/tasks/${taskId}/execution-jobs`)]);
          if (valid) setExecutionJobs(x.jobs);
          if (valid) { setTask(t); setRuns(r.runs); setJobs(j.jobs); setApproval(a.requirement === t.requirement && JSON.stringify(a.plan) === JSON.stringify(t.draft_plan) ? a : null); }
        }
      } catch (e) { if (valid) setError((e as Error).message); }
      finally { if (valid) timer = setTimeout(refresh, 2500); }
    };
    refresh();
    return () => { valid = false; clearTimeout(timer); };
  }, [project, taskId, tick]);

  function choose(id: string) { save('wt:selected-task', id); setTaskId(id); setTask(null); setRuns([]); setJobs([]); setExecutionJobs([]); setApproval(null); setEdit(null); setError(''); setNavigation(false); }
  function newTask() {
    choose('');
    setAdding(false);
    setComposerRequest(n => n + 1);
  }
  function beginEdit() {
    if (!task) return;
    const key = 'wt:revision:' + taskId;
    try {
      const cached = JSON.parse(stored(key));
      if (cached.revision === (task.revision || 1) && typeof cached.text === 'string' && typeof cached.id === 'string') { setEdit(cached); return; }
    } catch { /* Start from the latest requirement if no valid draft exists. */ }
    setEdit({ text: task.requirement, revision: task.revision || 1, id: crypto.randomUUID() });
  }
  async function act(fn: () => Promise<unknown>) {
    if (actionLock.current) return;
    actionLock.current = true; setBusy(true); setError('');
    try { await fn(); setTick(n => n + 1); } catch (e) { setError((e as Error).message); }
    finally { actionLock.current = false; setBusy(false); }
  }
  const activeDispatch = task?.dispatches.find(d => d.status === 'ACTIVE');
  const inflight = runs.some(r => ['RUNNING', 'PREPARED', 'EXITED'].includes(r.status));
  const planningBusy = jobs.some(j => ['QUEUED', 'RUNNING'].includes(j.status));
  const executionBusy = executionJobs.some(j => ['QUEUED', 'RUNNING'].includes(j.status));
  const locked = busy || inflight || planningBusy || chatPending || executionBusy;
  const planning = !!task && ['PLANNING', 'PLAN_REVIEW', 'PLAN_FINALIZE'].includes(task.phase);
  const canRevise = !!task && !locked && !activeDispatch;
  const base = '/tasks/' + taskId;

  return <div className="workspace">
    <aside className={'sidebar ' + (navigation ? 'visible' : '')} aria-label="项目与任务">
      <div className="brand"><span className="brand-mark">W</span>WorkTogether</div>
      <label className="small" htmlFor="project">当前项目</label>
      <select id="project" value={project} disabled={busy} onChange={e => { setProject(e.target.value); setTasks([]); choose(''); }}><option value="" disabled>选择项目</option>{projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
      <button className="new-task" disabled={!project || busy} onClick={newTask}>＋ 新任务</button>
      <p className="small section-label">最近任务</p>
      <nav>{tasks.map(t => <button key={t.id} disabled={busy} aria-current={t.id === taskId ? 'page' : undefined} className="task-link" onClick={() => choose(t.id)}><span>{t.title}</span><small>{label(t.status)}</small></button>)}</nav>
      <div className="sidebar-bottom"><button disabled={busy} onClick={() => { setAdding(!adding); setNavigation(false); }}>添加项目</button><p className="small">本地工作区 · 有限轮协作</p></div>
    </aside>
    <main className="main">
      <header className="topbar"><button className="mobile-nav" aria-expanded={navigation} onClick={() => setNavigation(!navigation)}>项目与任务</button><span>{currentProject?.name || '工作区'} <span className="muted">/ {task?.title || (taskId ? '加载任务' : '新任务')}</span></span><span className="badge">本地</span></header>
      <div className="main-scroll"><div className="reading page-enter">
        {error && <div className="error" role="alert">{error}<button onClick={() => setError('')}>关闭提示</button></div>}
        {!taskId && composerRequest > 0 && <p className="notice" role="status" key={composerRequest}>新建任务：请在下方填写需求，再点击“创建任务”。已有草稿会保留。</p>}
        {adding && <form className="card stack" onSubmit={e => { e.preventDefault(); act(async () => { const p = await api.addProject(name, path); const result = await api.getProjects(); setProjects(result.projects); setProject(p.id); choose(''); setAdding(false); setName(''); setPath(''); }); }}><h2>添加本地项目</h2><label>项目名称<input required value={name} onChange={e => setName(e.target.value)} /></label><label>项目目录<input required placeholder="/Users/你的名字/projects/my-app" value={path} onChange={e => setPath(e.target.value)} /></label><p className="small">关联现有目录，不会自动创建文件或启动模型。</p><div className="actions"><button className="primary" disabled={busy}>添加项目</button><button type="button" onClick={() => setAdding(false)}>取消</button></div></form>}
        {!taskId ? <section className="home"><p className="eyebrow">一个目标，两位协作伙伴</p><h1>这个项目，你想完成什么？</h1><p className="muted">说明目标和验收标准。先保存需求，再让双方讨论方案。</p><form className="composer" onSubmit={e => { e.preventDefault(); const p = project; act(async () => { const t = await api.addTask(p, draft.trim().split('\n')[0].slice(0, 60), draft.trim()); save('wt:draft:' + p, ''); setDraft(''); choose(t.id); }); }}><label htmlFor="requirement" className="small">任务需求</label><textarea id="requirement" placeholder="例如：优化视频生成进度。先讨论方案，不要急着写代码…" value={draft} onChange={e => { setDraft(e.target.value); save('wt:draft:' + project, e.target.value); }} /><div className="composer-footer"><span className="small">创建不会消耗模型额度</span><button className="primary" disabled={!project || !draft.trim() || busy}>创建任务</button></div></form>{!project && <p className="notice">请先添加一个本地项目。</p>}<div className="partners"><div><span className="avatar">C</span><div><strong>Codex</strong><p className="small">规划与主要实现</p></div></div><div><span className="avatar">A</span><div><strong>Antigravity</strong><p className="small">方案建议与独立验收</p></div></div></div><p className="small">连接状态需通过实际调用验证；此处不代表已连接。</p></section> : !task ? <p role="status">正在加载任务…</p> : <>
          <div className="title-row"><div><p className="eyebrow">{label(task.phase)} · 需求版本 {task.revision || 1}</p><h1>{task.title}</h1></div><span className="badge">{label(task.status)}</span></div>
          <div className="actions controls">
            {planning && <button className="primary" disabled={locked || !!activeDispatch || task.status !== 'ACTIVE'} onClick={() => act(() => post(base + '/discuss'))}>开始讨论 · 最多三轮</button>}
            {task.phase === 'EXECUTING' && !approval?.approved && <button className="primary" disabled={locked || !approval || task.status !== 'ACTIVE'} onClick={() => act(() => post(base + '/approve', { digest: approval!.digest }))}>批准下方方案</button>}
            {task.phase === 'EXECUTING' && approval?.approved && !activeDispatch && <button className="primary" disabled={locked || task.status !== 'ACTIVE'} onClick={() => act(() => post(base + '/execute'))}>自动开发与验收 · 最多 6 次调用</button>}
            {activeDispatch && <button className="primary" disabled={locked || task.status !== 'ACTIVE' || (['CODE', 'FIX'].includes(activeDispatch.kind) && !approval?.approved)} onClick={() => act(() => api.triggerRun(taskId, activeDispatch.id))}>执行当前项 · {activeDispatch.role === 'codex' ? 'Codex' : 'Antigravity'}</button>}
            {task.status !== 'COMPLETED' && <button disabled={busy} onClick={() => act(() => task.status === 'PAUSED' ? api.resumeTask(taskId) : api.pauseTask(taskId))}>{task.status === 'PAUSED' ? '恢复后续工作' : '暂停后续工作'}</button>}
            <button disabled={!canRevise} onClick={beginEdit}>修改需求</button>
          </div>
          <p className="small">暂停不会终止已运行的模型；执行状态不明时，请查看执行详情，不要重复启动。</p>
          {task.block_reason === 'PERMISSION_DENIED' && <section className="card" role="alert"><h2>验收权限被拒绝</h2><p>方案批准不包含工具权限批准。当前验收未完成，平台没有自动重试，也不会跳过权限检查。</p>{runs.filter(r => r.error_code === 'PERMISSION_DENIED').map(r => <details key={r.id}><summary>查看被拒绝的操作（仅展示，不执行）</summary>{r.error_details?.denied_commands?.length ? r.error_details.denied_commands.map((command, i) => <pre key={i}>{command}</pre>) : <p>工具未提供具体命令，请检查运行日志。</p>}</details>)}<p>请先在 Antigravity 支持的权限界面核对操作范围。本平台尚不能代你批准其工具权限；不要通过关闭安全检查来继续。</p></section>}
          {(planningBusy || executionBusy) && <section className="card"><h2>重启后卡住了？</h2><p>检查只会解除没有运行中作业、没有待处理模型结果的旧记录，不会启动模型。仍在工作时会拒绝解除。</p>{[{ kind: 'planning', records: jobs }, { kind: 'execution', records: executionJobs }].flatMap(group => group.records.filter(j => ['QUEUED', 'RUNNING'].includes(j.status)).map(j => <button key={j.id} disabled={busy} onClick={() => act(() => post(`${base}/${group.kind}-jobs/${j.id}/recover`))}>检查并解除{group.kind === 'planning' ? '讨论' : '执行'}等待</button>))}</section>}
          {executionJobs.length > 0 && <section className="card" role="status"><h2>自动执行</h2>{executionJobs.map(j => <p key={j.id}>{['QUEUED', 'RUNNING'].includes(j.status) ? '已登记或执行中；正在检查当前步骤。' : j.result?.reason === 'COMPLETED' ? '全部步骤已通过验收。' : '本轮已停止，任务不一定完成。'} {j.error || ({ CALL_LIMIT: '已达到 6 次调用上限，可检查结果后再次启动。', MESSAGE_PENDING: '有新消息待处理。', TASK_NOT_ACTIVE: '任务已暂停或需要处理。', EXISTING_DISPATCH: '已有派发需要检查。', APPROVAL_REQUIRED: '请重新批准方案。', PROVIDER_FAILED: '模型运行失败，没有自动重试。' } as Record<string, string>)[j.result?.reason || ''] || j.result?.reason}<small> 刷新页面不会重复启动；重启后的未知作业需人工核对。</small></p>)}</section>}
          {edit && <form className="card stack" onSubmit={e => { e.preventDefault(); const id = taskId; act(async () => { await post('/tasks/' + id + '/revisions', { message_id: edit.id, expected_revision: edit.revision, requirement: edit.text }); save('wt:revision:' + id, ''); if (selected.current.taskId === id) setEdit(null); }); }}><h2>修改完整需求</h2><p className="small">保存后撤销旧批准，重新规划；保留已有代码与历史报告。</p><textarea aria-label="完整新需求" value={edit.text} onChange={e => { const next = { ...edit, text: e.target.value, id: crypto.randomUUID() }; setEdit(next); save('wt:revision:' + taskId, JSON.stringify(next)); }} /><div className="actions"><button className="primary" disabled={!canRevise || !edit.text.trim()}>保存新版本</button><button type="button" onClick={() => setEdit(null)}>收起（保留草稿）</button></div></form>}
          <div className="tabs"><button aria-pressed={tab === 'progress'} onClick={() => setTab('progress')}>沟通与进展</button><button aria-pressed={tab === 'details'} onClick={() => setTab('details')}>执行详情</button></div>
          <div hidden={tab !== 'progress'}>
            <TaskChat key={taskId} taskId={taskId} completed={task.status === 'COMPLETED'} onPending={setChatPending} />
          </div>
          {tab === 'progress' ? <>
            <section className="message"><div className="message-heading"><span className="avatar">你</span><strong>当前需求</strong></div><p className="prose">{task.requirement}</p></section>
            {task.draft_plan && <section className="card"><h2>当前方案 <small className="muted">{approval?.approved ? '已批准' : '待审阅'}</small></h2>{task.draft_plan.steps.map(s => <div key={s.id} className="plan-step"><strong>{s.title}</strong><ul>{s.acceptance.map(c => <li key={c}>{c}</li>)}</ul></div>)}</section>}
            {task.dispatches.filter(d => d.report).map(d => <section key={d.id} className="message"><div className="message-heading"><span className="avatar">{d.role === 'codex' ? 'C' : 'A'}</span><strong>{d.role === 'codex' ? 'Codex' : 'Antigravity'}</strong><span className="badge">{(d.payload.requirement_revision || 1) !== (task.revision || 1) ? '历史版本 · ' : ''}{label(d.report!.status)}</span></div><p className="prose">{d.report!.summary}</p>{d.report!.suggestions.map((s, i) => <p key={i} className="suggestion">{s}</p>)}<details><summary>依据与报告</summary><pre>{JSON.stringify(d.report, null, 2)}</pre></details></section>)}
            {!task.dispatches.length && <div className="empty">需求已保存。开始讨论后，这里会显示双方的真实方案与建议。</div>}
          </> : <>
            <h2>步骤与验收标准</h2>{task.steps.map(s => <details key={s.id} className="card"><summary>{s.title} · {label(s.status || '')}</summary><ul>{s.acceptance.map(c => <li key={c}>{c}</li>)}</ul></details>)}
            <h2>后台讨论</h2>{!jobs.length && <p className="muted">尚未登记讨论作业。</p>}{jobs.map(j => <div className="card" key={j.id}><strong>{label(j.status)}</strong><p className="small">{j.error || j.result?.reason || '登记不代表模型已经启动；重启后的未知状态需要人工检查。'}</p></div>)}
            <h2>模型运行记录</h2>{runs.map(r => <details className="card" key={r.id}><summary>{r.provider} · {label(r.status)}</summary><pre>{JSON.stringify(r, null, 2)}</pre></details>)}
            <h2>事件记录</h2>{task.events.map(e => <details key={e.seq}><summary>{e.kind} · {new Date(e.created_at * 1000).toLocaleTimeString()}</summary><pre>{JSON.stringify(e.data, null, 2)}</pre></details>)}
          </>}
        </>}
      </div></div>
    </main>
    <aside className="inspector"><h2>任务概览</h2><p className="small">一次只推进明确的下一步</p><div className="divider" /><h3>协作方式</h3><p>讨论最多三轮<br />返工最多两轮<br />开发前由你批准</p><div className="divider" /><h3>步骤进度</h3>{task?.steps.length ? task.steps.map(s => <details key={s.id}><summary>{s.title}</summary><p>{label(s.status || '')}</p><ul>{s.acceptance.map(c => <li key={c}>{c}</li>)}</ul></details>) : <p className="small">方案定稿后显示步骤。</p>}<div className="divider" /><p className="small">不共享其他项目的聊天。后台调用使用本任务绑定的独立会话。</p></aside>
  </div>;
}
