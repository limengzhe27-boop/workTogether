import React, { useEffect, useRef, useState } from 'react';
import { api, request } from './api';
import { TaskChat } from './TaskChat';
import { Project, TaskDetail, TaskSummary, RunRecord, RunArtifactPreview, AgentInfo, AgentProbeResult, ReportEnvelope } from './types';

type Detail = TaskDetail & { revision?: number; team?: { members: string[]; coordinator: string; reviewer: string } };
type Job = { id: string; status: string; error: string | null; authorization?: { revision: number }; result?: { reason?: string } };
type Approval = { digest: string; approved: boolean; requirement: string; plan: Detail['draft_plan']; revision: number };
const labels: Record<string, string> = {
  CONSULT: '成员意见',
  CLAIM: '任务认领',
  PLAN: '任务拆分',
  PLAN_FINALIZE: '分工定稿',
  CODE: '实现',
  VERIFY: '独立验收',
  FIX: '修复',
  ANALYZE: '分析交付',
  PLANNING: '准备方案',
  PLAN_REVIEW: '审查方案',
  EXECUTING: '开发与验收',
  COMPLETED: '已完成',
  ACTIVE: '待推进',
  PAUSED: '已暂停',
  BLOCKED: '需要处理',
  RUNNING: '执行中',
  ANALYZING: '分析中',
  PREPARED: '等待启动确认',
  QUEUED: '已登记',
  FAILED: '失败',
  EXITED: '等待收集结果',
  DONE: '已交付',
  SUPERSEDED: '已失效',
};
const label = (s: string) => labels[s] || s;
const artifactLabels: Record<string, string> = {
  'prompt.txt': '指令', 'result.json': '结果', 'events.jsonl': '运行日志',
  'stderr.log': '错误日志', 'command.json': '启动参数',
};
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
  const [artifact, setArtifact] = useState<{
    runId: string; filename: string; loading: boolean; error: string; preview?: RunArtifactPreview;
  } | null>(null);
  const artifactRequest = useRef(0);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [executionJobs, setExecutionJobs] = useState<Job[]>([]);
  const [teamJobs, setTeamJobs] = useState<Job[]>([]);
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
  const [manualDispatchId, setManualDispatchId] = useState('');
  const [handoffText, setHandoffText] = useState('');
  const [reportText, setReportText] = useState('');
  const actionLock = useRef(false);
  const selected = useRef({ project, taskId });
  selected.current = { project, taskId };
  const currentProject = projects.find(p => p.id === project);

  // Multi-agent states
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [teamChoice, setTeamChoice] = useState<{ taskId: string; members: string[] }>({ taskId: '', members: [] });
  const [delegateOnCreate, setDelegateOnCreate] = useState(false);
  const [newTeamChoice, setNewTeamChoice] = useState<{ project: string; members: string[] }>({ project: '', members: [] });
  const [providers, setProviders] = useState<Record<string, { available: boolean; executable: string }> | null>(null);
  const [managingAgents, setManagingAgents] = useState(false);
  const [probingAgent, setProbingAgent] = useState('');
  const [probeResults, setProbeResults] = useState<Record<string, AgentProbeResult>>({});
  const [newAgent, setNewAgent] = useState({
    id: '',
    name: '',
    type: 'openai_compat',
    endpoint: 'https://api.deepseek.com/v1',
    model: 'deepseek-chat',
    api_key_env: 'DEEPSEEK_API_KEY',
    description: '',
    color: '#8b5cf6',
    avatar: '🧠',
  });

  const agentMap = React.useMemo(() => {
    const map: Record<string, AgentInfo> = {};
    for (const a of agents) {
      map[a.id] = a;
    }
    return map;
  }, [agents]);
  const usageByProvider = React.useMemo(() => {
    const totals: Record<string, { calls: number; input: number; output: number; cached: number }> = {};
    for (const run of runs) {
      const usage = run.usage;
      if (!usage || typeof usage.input_tokens !== 'number') continue;
      const entry = totals[run.provider] ||= { calls: 0, input: 0, output: 0, cached: 0 };
      entry.calls += 1;
      entry.input += usage.input_tokens;
      entry.output += typeof usage.output_tokens === 'number' ? usage.output_tokens : 0;
      const cached = usage.cached_input_tokens ?? usage.cache_read_tokens ?? usage.cached_tokens;
      entry.cached += typeof cached === 'number' ? cached : 0;
    }
    return Object.entries(totals);
  }, [runs]);

  // Load projects & agents
  useEffect(() => {
    api.getProjects()
      .then(r => {
        setProjects(r.projects);
        setProject(p => p || r.projects.find(x => x.id === stored('wt:selected-project'))?.id || r.projects[0]?.id || '');
      })
      .catch(e => setError(e.message));

    api.getAgents()
      .then(r => setAgents(r.agents))
      .catch(() => {});
    api.getHealth()
      .then(r => setProviders(r.providers || {}))
      .catch(() => {});
  }, []);

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
          const [t, r, j, a, x, ag, tj] = await Promise.all([
            api.getTask(taskId),
            api.getRuns(taskId),
            request<{ jobs: Job[] }>(`/tasks/${taskId}/planning-jobs`),
            request<Approval>(`/tasks/${taskId}/approval`),
            request<{ jobs: Job[] }>(`/tasks/${taskId}/execution-jobs`),
            api.getAgents(),
            request<{ jobs: Job[] }>(`/tasks/${taskId}/team-jobs`),
          ]);
          if (valid) {
            if (project && t.project_id !== project) {
              choose('');
              return;
            }
            setExecutionJobs(x.jobs);
            setTeamJobs(tj.jobs);
            setTask(t);
            setRuns(r.runs);
            setJobs(j.jobs);
            setAgents(ag.agents);
            setApproval(a.requirement === t.requirement && JSON.stringify(a.plan) === JSON.stringify(t.draft_plan) ? a : null);
          }
        }
      } catch (e) {
        if (valid) {
          if ((e as Error & { status?: number }).status === 404 && taskId) choose('');
          else setError((e as Error).message);
        }
      }
      finally { if (valid) timer = setTimeout(refresh, 2500); }
    };
    refresh();
    return () => { valid = false; clearTimeout(timer); };
  }, [project, taskId, tick]);

  function choose(id: string) {
    artifactRequest.current += 1;
    save('wt:selected-task', id);
    setTaskId(id);
    setTask(null);
    setRuns([]);
    setArtifact(null);
    setJobs([]);
    setExecutionJobs([]);
    setTeamJobs([]);
    setApproval(null);
    setEdit(null);
    setError('');
    setManualDispatchId('');
    setHandoffText('');
    setReportText('');
    setNavigation(false);
  }

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
      if (cached.revision === (task.revision || 1) && typeof cached.text === 'string' && typeof cached.id === 'string') {
        setEdit(cached);
        return;
      }
    } catch { /* Start from latest requirement */ }
    setEdit({ text: task.requirement, revision: task.revision || 1, id: crypto.randomUUID() });
  }

  async function act(fn: () => Promise<unknown>) {
    if (actionLock.current) return;
    actionLock.current = true; setBusy(true); setError('');
    try { await fn(); setTick(n => n + 1); } catch (e) { setError((e as Error).message); }
    finally { actionLock.current = false; setBusy(false); }
  }

  async function handleAddAgent(e: React.FormEvent) {
    e.preventDefault();
    if (!newAgent.id || !newAgent.name) return;
    await act(async () => {
      await api.addAgent({
        ...newAgent,
      });
      const res = await api.getAgents();
      setAgents(res.agents);
      setProbeResults(current => {
        const next = { ...current };
        delete next[newAgent.id];
        return next;
      });
      setManagingAgents(false);
      setNewAgent({
        id: '',
        name: '',
        type: 'openai_compat',
        endpoint: 'https://api.deepseek.com/v1',
        model: 'deepseek-chat',
        api_key_env: 'DEEPSEEK_API_KEY',
        description: '',
        color: '#8b5cf6',
        avatar: '🧠',
      });
    });
  }

  async function testAgentConnection(agentId: string) {
    if (probingAgent) return;
    setProbingAgent(agentId);
    try {
      const result = await api.probeAgent(agentId);
      setProbeResults(current => ({ ...current, [agentId]: result }));
    } catch (e) {
      setProbeResults(current => ({ ...current, [agentId]: {
        agent_id: agentId, connected: false, code: 'PROBE_ERROR',
        message: (e as Error).message,
      } }));
    } finally {
      setProbingAgent('');
    }
  }

  async function viewRunArtifact(runId: string, filename: string) {
    const requestId = ++artifactRequest.current;
    const currentTask = taskId;
    setArtifact({ runId, filename, loading: true, error: '' });
    try {
      const preview = await api.getArtifact(runId, filename);
      if (artifactRequest.current === requestId && selected.current.taskId === currentTask) {
        setArtifact({ runId, filename, loading: false, error: '', preview });
      }
    } catch (e) {
      if (artifactRequest.current === requestId && selected.current.taskId === currentTask) {
        const error = e as Error & { status?: number };
        setArtifact({ runId, filename, loading: false,
          error: error.status === 404 ? '这次运行没有生成该文件。' : error.message });
      }
    }
  }

  const activeDispatch = task?.dispatches.find(d => d.status === 'ACTIVE');
  const blockedDispatches = task?.dispatches.filter(d => d.status === 'BLOCKED') || [];
  const inflight = runs.some(r => ['RUNNING', 'PREPARED', 'EXITED'].includes(r.status));
  const planningBusy = jobs.some(j => ['QUEUED', 'RUNNING'].includes(j.status));
  const executionBusy = executionJobs.some(j => ['QUEUED', 'RUNNING'].includes(j.status));
  const teamBusy = teamJobs.some(j => ['QUEUED', 'RUNNING'].includes(j.status));
  const locked = busy || inflight || planningBusy || chatPending || executionBusy || teamBusy;
  const planning = !!task && ['PLANNING', 'PLAN_REVIEW', 'PLAN_FINALIZE'].includes(task.phase);
  const revisionStarted = !!task?.dispatches.some(d => (d.payload.requirement_revision || 1) === (task.revision || 1));
  const canRevise = !!task && !locked && !activeDispatch;
  const base = '/tasks/' + taskId;
  const newTeamMembers = newTeamChoice.project === project ? newTeamChoice.members : ['codex', 'antigravity'];

  async function prepareManual(dispatchId: string) {
    await act(async () => {
      const bundle = await api.getHandoff(taskId, dispatchId);
      setManualDispatchId(dispatchId);
      setHandoffText(JSON.stringify(bundle, null, 2));
      setReportText(JSON.stringify(bundle.template, null, 2));
    });
  }

  async function submitManual() {
    await act(async () => {
      let report: unknown;
      try { report = JSON.parse(reportText); }
      catch { throw new Error('报告不是有效 JSON，请检查后再提交。'); }
      if (!report || typeof report !== 'object' || Array.isArray(report)) {
        throw new Error('报告必须是 JSON 对象。');
      }
      await api.submitReport(taskId, report as ReportEnvelope);
      setManualDispatchId('');
      setHandoffText('');
      setReportText('');
    });
  }

  return (
    <div className="workspace">
      <aside className={'sidebar ' + (navigation ? 'visible' : '')} aria-label="项目与任务">
        <div className="brand"><span className="brand-mark">W</span>WorkTogether</div>
        <label className="small" htmlFor="project">当前项目</label>
        <select id="project" value={project} disabled={busy} onChange={e => { setProject(e.target.value); setTasks([]); choose(''); }}>
          <option value="" disabled>选择项目</option>
          {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <button className="new-task" disabled={!project || busy} onClick={newTask}>＋ 新任务</button>
        <p className="small section-label">最近任务</p>
        <nav>
          {tasks.map(t => (
            <button key={t.id} disabled={busy} aria-current={t.id === taskId ? 'page' : undefined} className="task-link" onClick={() => choose(t.id)}>
              <span>{t.title}</span>
              <small>{label(t.status)}</small>
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <button disabled={busy} onClick={() => { setAdding(!adding); setNavigation(false); }}>添加项目</button>
      <p className="small">多 Agent 任务对话 · 本地受控工作区</p>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, minWidth: 0 }}>
            <button className="mobile-nav" aria-expanded={navigation} onClick={() => setNavigation(!navigation)}>项目与任务</button>
            <span>{currentProject?.name || '工作区'} <span className="muted">/ {task?.title || (taskId ? '加载任务' : '新任务')}</span></span>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div className="agents-strip">
              {agents.map(a => (
                <span key={a.id} className={`agent-pill ${a.ready ? 'active' : ''}`} style={{ color: a.color }} title={a.description}>
                  <span className="agent-avatar">{a.avatar}</span>
                  <span>{a.name}</span>
                </span>
              ))}
            </div>
            <button
              style={{ minHeight: 30, padding: '2px 10px', fontSize: 13, borderRadius: 6 }}
              disabled={busy}
              onClick={() => setManagingAgents(true)}
            >
              ⚙ 管理 Agent
            </button>
            <span className="badge">本地</span>
          </div>
        </header>

        <div className="main-scroll">
          <div className="reading page-enter">
            {error && (
              <div className="error" role="alert">
                {error}
                <button onClick={() => setError('')}>关闭提示</button>
              </div>
            )}

            {providers && (providers.codex?.available === false || providers.antigravity?.available === false) && (
              <div className="notice" role="status">
                自动调用尚未就绪：{[
                  providers.codex?.available === false ? 'Codex' : '',
                  providers.antigravity?.available === false ? 'Antigravity' : '',
                ].filter(Boolean).join('、')} 的命令行程序未找到。仍可用“手动派发下一项”和交接包推进；安装或指定路径后请重启服务。
              </div>
            )}

            {!taskId && composerRequest > 0 && (
              <p className="notice" role="status" key={composerRequest}>
                新建任务：请在下方填写需求，再点击“创建任务”。已有草稿会保留。
              </p>
            )}

            {adding && (
              <form className="card stack" onSubmit={e => {
                e.preventDefault();
                act(async () => {
                  const p = await api.addProject(name, path);
                  const result = await api.getProjects();
                  setProjects(result.projects);
                  setProject(p.id);
                  choose('');
                  setAdding(false);
                  setName('');
                  setPath('');
                });
              }}>
                <h2>添加本地项目</h2>
                <label>项目名称<input required value={name} onChange={e => setName(e.target.value)} /></label>
                <label>项目目录<input required placeholder="/Users/你的名字/projects/my-app" value={path} onChange={e => setPath(e.target.value)} /></label>
                <p className="small">关联现有目录，不会自动创建文件或启动模型。</p>
                <div className="actions">
                  <button className="primary" disabled={busy}>添加项目</button>
                  <button type="button" onClick={() => setAdding(false)}>取消</button>
                </div>
              </form>
            )}

            {!taskId ? (
              <section className="home">
                <p className="eyebrow">多 Agent 讨论 · 受控分工与验收</p>
                <h1>这个项目，你想完成什么？</h1>
                <p className="muted">说明目标和验收标准。创建后选择任务团队，按能力讨论与分工。你可以先审方案，也可以明确授权团队在当前需求范围内连续执行。</p>

                <form className="composer" onSubmit={e => {
                  e.preventDefault();
                  const p = project;
                  const delegate = delegateOnCreate;
                  const members = [...newTeamMembers];
                  act(async () => {
                    const t = await api.addTask(p, draft.trim().split('\n')[0].slice(0, 60), draft.trim());
                    save('wt:draft:' + p, '');
                    setDraft('');
                    choose(t.id);
                    if (delegate) {
                      // Keep the created task visible if configuration or launch
                      // fails; never recreate it blindly or hide partial state.
                      await post(`/tasks/${t.id}/team`, { members });
                      await post(`/tasks/${t.id}/delegate-team`, { authorize_execution: true });
                    }
                  });
                }}>
                  <label htmlFor="requirement" className="small">任务需求</label>
                  <textarea
                    id="requirement"
                    placeholder="例如：优化前端性能并重构架构。先多方讨论方案，再行分工实施…"
                    value={draft}
                    onChange={e => {
                      setDraft(e.target.value);
                      save('wt:draft:' + project, e.target.value);
                    }}
                  />
                  <label className="small">
                    <input type="checkbox" checked={delegateOnCreate} disabled={busy}
                      onChange={e => setDelegateOnCreate(e.target.checked)} /> 创建后授权团队讨论、分工并执行当前需求
                  </label>
                  {delegateOnCreate && <div>
                    <div className="actions">{agents.filter(a => a.ready).map(a => <label key={a.id}>
                      <input type="checkbox" disabled={busy} checked={newTeamMembers.includes(a.id)} onChange={e => setNewTeamChoice({
                        project, members: e.target.checked ? [...newTeamMembers, a.id] : newTeamMembers.filter(id => id !== a.id),
                      })} /> {a.name}
                    </label>)}</div>
                    <p className="small">至少选择两位成员。选定 Agent 将收到需求并可能修改项目，定稿后不再逐步询问。最多 {2 * newTeamMembers.length + 15} 次 Agent 调用；外部接口会接收必要任务信息。暂停、失败或预算耗尽会停止。</p>
                  </div>}
                  <div className="composer-footer">
                    <span className="small">{delegateOnCreate ? '将调用选定 Agent，可能消耗其额度' : '仅创建不会消耗模型额度'}</span>
                    <button className="primary" disabled={!project || !draft.trim() || busy || (delegateOnCreate && newTeamMembers.length < 2)}>{delegateOnCreate ? '创建并授权团队执行' : '创建任务'}</button>
                  </div>
                </form>

                {!project && <p className="notice">请先添加一个本地项目。</p>}

                <div className="partners">
                  {agents.map(a => (
                    <div key={a.id}>
                      <span className="avatar" style={{ color: a.color, borderColor: a.color, fontSize: 16 }}>
                        {a.avatar}
                      </span>
                      <div>
                        <strong style={{ color: a.color }}>{a.name}</strong>
                        <p className="small">{a.description || a.type}</p>
                      </div>
                    </div>
                  ))}
                </div>
                <p className="small">点击右上角“管理 Agent”可接入 DeepSeek API 或其他 OpenAI 兼容接口，参与对话与只读分析。</p>
              </section>
            ) : !task ? (
              <p role="status">正在加载任务…</p>
            ) : (
              <>
                <div className="title-row">
                  <div>
                    <p className="eyebrow">{label(task.phase)} · 需求版本 {task.revision || 1}</p>
                    <h1>{task.title}</h1>
                  </div>
                  <span className="badge">{label(task.status)}</span>
                </div>

                {task.phase === 'PLANNING' && !revisionStarted && (
                  <section className="card">
                    <h2>任务团队</h2>
                    <p className="muted">选择参与本任务的成员。规划与执行按能力分工；普通 API 不能修改代码或运行测试。可用配置不代表登录与配额已验证。</p>
                    <div className="actions">
                      {agents.filter(a => a.ready).map(a => {
                        const members = teamChoice.taskId === taskId ? teamChoice.members : task.team?.members || ['codex', 'antigravity'];
                        return <label key={a.id}>
                          <input type="checkbox" disabled={locked} checked={members.includes(a.id)} onChange={e => setTeamChoice({
                            taskId, members: e.target.checked ? [...members, a.id] : members.filter(id => id !== a.id),
                          })} /> {a.name}
                        </label>;
                      })}
                    </div>
                    <button disabled={locked || task.status !== 'ACTIVE'} onClick={() => act(() => post(base + '/team', {
                      members: teamChoice.taskId === taskId ? teamChoice.members : task.team?.members || ['codex', 'antigravity'],
                    }))}>保存团队与能力分工</button>
                  </section>
                )}
                {task.team && <p className="muted">规划协调：{agentMap[task.team.coordinator]?.name || task.team.coordinator} · 方案复核：{agentMap[task.team.reviewer]?.name || task.team.reviewer} · 步骤执行与验收按能力另行分配</p>}

                <div className="actions controls">
                  {task.team && task.phase === 'PLANNING' && !revisionStarted && (
                    <button className="primary" disabled={locked || task.status !== 'ACTIVE'} onClick={() => act(() => post(base + '/delegate-team', { authorize_execution: true }))}>
                      授权团队讨论并执行 · 最多 {2 * task.team.members.length + 15} 次调用
                    </button>
                  )}
                  {planning && (
                    <button className="primary" disabled={locked || !!activeDispatch || task.status !== 'ACTIVE'} onClick={() => act(() => post(base + '/discuss'))}>
                      {task.team ? '团队讨论与认领 · 两轮成员发言＋三次规划' : '开始方案讨论 · 最多三轮'}
                    </button>
                  )}
                  {!activeDispatch && task.phase !== 'COMPLETED' && (
                    <button disabled={locked || task.status !== 'ACTIVE' || (task.phase === 'EXECUTING' && !approval?.approved)}
                      onClick={() => act(async () => {
                        const next = await api.dispatch(taskId);
                        if (!next.dispatch && next.reason === 'PROJECT_WORKSPACE_BUSY') {
                          throw new Error('同一项目还有开发或验收项未处理；请先完成、重试或修订那个任务。');
                        }
                        if (!next.dispatch && next.reason === 'AGENT_UNAVAILABLE') {
                          throw new Error('当前负责人已停用或未就绪，请检查 Agent 配置。');
                        }
                        if (!next.dispatch && next.reason === 'TEAM_ROUND_REQUIRED') {
                          throw new Error('需要先完成团队意见或认领轮，请使用“团队讨论与认领”。');
                        }
                      })}>
                      手动派发下一项（不调用模型）
                    </button>
                  )}
                  {task.phase === 'EXECUTING' && !approval?.approved && (
                    <button className="primary" disabled={locked || !approval || task.status !== 'ACTIVE'} onClick={() => act(() => post(base + '/approve', { digest: approval!.digest }))}>
                      批准下方方案
                    </button>
                  )}
                  {task.phase === 'EXECUTING' && approval?.approved && !activeDispatch && (
                    <button className="primary" disabled={locked || task.status !== 'ACTIVE'} onClick={() => act(() => post(base + '/execute'))}>
                      自动开发与验收 · 最多 6 次调用
                    </button>
                  )}
                  {activeDispatch && (
                    <button
                      className="primary"
                      disabled={locked || task.status !== 'ACTIVE' || (['CODE', 'ANALYZE', 'FIX', 'VERIFY'].includes(activeDispatch.kind) && !approval?.approved)}
                      onClick={() => act(() => api.triggerRun(taskId, activeDispatch.id))}
                    >
                      执行当前项 · {agentMap[activeDispatch.role]?.name || activeDispatch.role}
                    </button>
                  )}
                  {task.status !== 'COMPLETED' && (
                    <button disabled={busy} onClick={() => act(() => task.status === 'PAUSED' ? api.resumeTask(taskId) : api.pauseTask(taskId))}>
                      {task.status === 'PAUSED' ? '恢复后续工作' : '暂停后续工作'}
                    </button>
                  )}
                  <button disabled={!canRevise} onClick={beginEdit}>修改需求</button>
                </div>
                <p className="small">暂停不会终止已运行的模型；执行状态不明时，请查看执行详情，不要重复启动。</p>
                {task.team && !revisionStarted && <p className="small">“授权团队讨论并执行”允许团队在本需求范围内定稿后直接工作，不再逐步询问；仍受能力、独立验收、修复与调用预算限制。若要先审方案，请使用“团队讨论与认领”。</p>}
                {teamJobs.length > 0 && <section className="card">
                  <h2>团队自动协作</h2>
                  {teamJobs.map(j => <p key={j.id}>需求版本 {j.authorization?.revision || 1} · {j.result?.reason === 'COMPLETED' ? '全部步骤通过验收' : j.status === 'COMPLETED' ? '本轮已停止，任务未必完成' : label(j.status)} · {j.error || j.result?.reason || '讨论、认领、定稿后按授权执行'}{j.result?.reason === 'CALL_LIMIT' ? '（本轮预算已用完，不代表任务完成）' : ''}</p>)}
                </section>}

                {activeDispatch && (
                  <section className="card stack">
                    <h2>当前交接 · {activeDispatch.kind} → {agentMap[activeDispatch.role]?.name || activeDispatch.role}</h2>
                    <p className="small">自动执行不可用时，可把交接包发给对应 Agent，再将它返回的结构化报告贴回这里。手动提交不会调用模型；平台只校验身份、版本和证据字段格式，不能证明测试确实执行。请你核对报告真实性。</p>
                    <div className="actions">
                      <button disabled={locked || task.status !== 'ACTIVE'} onClick={() => prepareManual(activeDispatch.id)}>
                        准备手动交接与报告模板
                      </button>
                      {activeDispatch.deadline < Date.now() / 1000 && (
                        <button disabled={locked} onClick={() => act(() => api.expireDispatches(taskId))}>标记超时并处理</button>
                      )}
                    </div>
                    {manualDispatchId === activeDispatch.id && (
                      <>
                        <label>交接包（只读）
                          <textarea readOnly value={handoffText} rows={10} aria-label="交接包" />
                        </label>
                        <button type="button" disabled={!handoffText} onClick={async () => {
                          try { await navigator.clipboard.writeText(handoffText); }
                          catch { setError('无法自动复制，请在交接包文本框中手动选择并复制。'); }
                        }}>复制交接包</button>
                        <label>Agent 返回的 JSON 报告
                          <textarea value={reportText} onChange={e => setReportText(e.target.value)} rows={12} aria-label="JSON 报告" />
                        </label>
                        <p className="small">模板中的 REPLACE 和 BLOCKED 只是占位，不能当作完成证明；验收报告必须填写每项标准的真实检查与证据。</p>
                        <button className="primary" disabled={locked || task.status !== 'ACTIVE' || !reportText.trim()} onClick={submitManual}>校验并提交报告</button>
                      </>
                    )}
                  </section>
                )}

                {task.status === 'BLOCKED' && blockedDispatches.length > 0 && (
                  <section className="card stack" role="alert">
                    <h2>任务已阻塞 · {task.block_reason || '检查失败详情'}</h2>
                    <p>平台不会自动重试。先查看失败记录和证据，再决定是否重新派发。重新派发会产生新的报告编号，旧报告不能用于新派发。</p>
                    {task.block_reason === 'REPAIR_LIMIT' ? (
                      <p>返工次数已达上限；不能直接重试。请人工检查结果，并通过“修改需求”重新规划。</p>
                    ) : blockedDispatches.map(d => (
                      <button key={d.id} disabled={locked} onClick={() => act(() => api.retryDispatch(taskId, d.id))}>
                        检查后重试 {d.kind} · {agentMap[d.role]?.name || d.role}
                      </button>
                    ))}
                  </section>
                )}

                {task.block_reason === 'PERMISSION_DENIED' && (
                  <section className="card" role="alert">
                    <h2>验收权限被拒绝</h2>
                    <p>方案批准不包含工具权限批准。当前验收未完成，平台没有自动重试，也不会跳过权限检查。</p>
                    {runs.filter(r => r.error_code === 'PERMISSION_DENIED').map(r => (
                      <details key={r.id}>
                        <summary>查看被拒绝的操作（仅展示，不执行）</summary>
                        {r.error_details?.denied_commands?.length ? (
                          r.error_details.denied_commands.map((command, i) => <pre key={i}>{command}</pre>)
                        ) : (
                          <p>工具未提供具体命令，请检查运行日志。</p>
                        )}
                      </details>
                    ))}
                    <p>请先在 Antigravity 支持的权限界面核对操作范围。也可在检查后重试该派发，再使用上方“手动交接与报告模板”完成验收；不要通过关闭安全检查来继续。</p>
                  </section>
                )}

                {(planningBusy || executionBusy || teamBusy) && (
                  <section className="card">
                    <h2>重启后卡住了？</h2>
                    <p>检查只会解除没有运行中作业、没有待处理模型结果的旧记录，不会启动模型。仍在工作时会拒绝解除。</p>
                    {[{ kind: 'planning', records: jobs }, { kind: 'execution', records: executionJobs }, { kind: 'team', records: teamJobs }].flatMap(group =>
                      group.records.filter(j => ['QUEUED', 'RUNNING'].includes(j.status)).map(j => (
                        <button key={j.id} disabled={busy} onClick={() => act(() => post(`${base}/${group.kind}-jobs/${j.id}/recover`))}>
                          检查并解除{group.kind === 'planning' ? '讨论' : '执行'}等待
                        </button>
                      ))
                    )}
                  </section>
                )}

                {executionJobs.length > 0 && (
                  <section className="card" role="status">
                    <h2>自动执行</h2>
                    {executionJobs.map(j => (
                      <p key={j.id}>
                        {['QUEUED', 'RUNNING'].includes(j.status)
                          ? '已登记或执行中；正在检查当前步骤。'
                          : j.result?.reason === 'COMPLETED'
                          ? '全部步骤已通过验收。'
                          : '本轮已停止，任务不一定完成。'}{' '}
                        {j.error ||
                          ({
                            CALL_LIMIT: '已达到 6 次调用上限，可检查结果后再次启动。',
                            MESSAGE_PENDING: '有新消息待处理。',
                            TASK_NOT_ACTIVE: '任务已暂停或需要处理。',
                            EXISTING_DISPATCH: '已有派发需要检查。',
                            PROJECT_WORKSPACE_BUSY: '同一项目有开发或验收项尚未处理。',
                            AGENT_UNAVAILABLE: '当前负责人已停用或未就绪。',
                            APPROVAL_REQUIRED: '请重新批准方案。',
                            PROVIDER_FAILED: '模型运行失败，没有自动重试。',
                          } as Record<string, string>)[j.result?.reason || ''] ||
                          j.result?.reason}
                        <small> 刷新页面不会重复启动；重启后的未知作业需人工核对。</small>
                      </p>
                    ))}
                  </section>
                )}

                {edit && (
                  <form className="card stack" onSubmit={e => {
                    e.preventDefault();
                    const id = taskId;
                    act(async () => {
                      await post('/tasks/' + id + '/revisions', {
                        message_id: edit.id,
                        expected_revision: edit.revision,
                        requirement: edit.text,
                      });
                      save('wt:revision:' + id, '');
                      if (selected.current.taskId === id) setEdit(null);
                    });
                  }}>
                    <h2>修改完整需求</h2>
                    <p className="small">保存后撤销旧批准，重新规划；保留已有代码与历史报告。</p>
                    <textarea
                      aria-label="完整新需求"
                      value={edit.text}
                      onChange={e => {
                        const next = { ...edit, text: e.target.value, id: crypto.randomUUID() };
                        setEdit(next);
                        save('wt:revision:' + taskId, JSON.stringify(next));
                      }}
                    />
                    <div className="actions">
                      <button className="primary" disabled={!canRevise || !edit.text.trim()}>保存新版本</button>
                      <button type="button" onClick={() => setEdit(null)}>收起（保留草稿）</button>
                    </div>
                  </form>
                )}

                <div className="tabs">
                  <button aria-pressed={tab === 'progress'} onClick={() => setTab('progress')}>沟通与进展</button>
                  <button aria-pressed={tab === 'details'} onClick={() => setTab('details')}>执行详情</button>
                </div>

                <div hidden={tab !== 'progress'}>
                  <TaskChat key={taskId} taskId={taskId} agents={task.team ? agents.filter(a => task.team!.members.includes(a.id)) : agents} completed={task.status === 'COMPLETED'} onPending={setChatPending} />
                </div>

                {tab === 'progress' ? (
                  <>
                    <section className="message">
                      <div className="message-heading">
                        <span className="avatar">👤</span>
                        <strong>当前需求</strong>
                      </div>
                      <p className="prose">{task.requirement}</p>
                    </section>

                    {task.draft_plan && (
                      <section className="card">
                        <h2>当前方案 <small className="muted">{approval?.approved ? '已批准' : '待审阅'}</small></h2>
                        {task.draft_plan.steps.map(s => {
                          const assignee = s.assignee ? agentMap[s.assignee] : agentMap['codex'];
                          return (
                            <div key={s.id} className="plan-step">
                              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                        <strong>{s.title} {s.work_kind === 'analysis' ? '· 分析' : '· 开发'}</strong>
                                {assignee && (
                                  <span className="agent-tag" style={{ background: assignee.color + '1a', color: assignee.color }}>
                                    {assignee.avatar} {assignee.name}
                                  </span>
                                )}
                              </div>
                              {s.verifier && <p className="muted">独立验收：{agentMap[s.verifier]?.name || s.verifier}</p>}
                              {s.assignment_reason && <p className="muted">分工依据：{s.assignment_reason}</p>}
                              <ul>{s.acceptance.map(c => <li key={c}>{c}</li>)}</ul>
                            </div>
                          );
                        })}
                      </section>
                    )}

                    {task.dispatches.filter(d => d.report).map(d => {
                      const ag = agentMap[d.role];
                      const avatar = ag?.avatar || (d.role === 'codex' ? '🤖' : '🦅');
                      const name = ag?.name || d.role;
                      const color = ag?.color || '#6366f1';
                      return (
                        <section key={d.id} className="message">
                          <div className="message-heading">
                            <span className="avatar" style={{ color, borderColor: color, fontSize: 16 }}>{avatar}</span>
                            <strong style={{ color }}>{name}</strong>
                            <span className="small muted">{label(d.kind)}</span>
                            <span className="badge">
                              {(d.payload.requirement_revision || 1) !== (task.revision || 1) ? '历史版本 · ' : ''}
                              {label(d.report!.status)}
                            </span>
                          </div>
                          <p className="prose">{d.report!.summary}</p>
                          {d.kind === 'CLAIM' && Array.isArray(d.report!.data.claims) && (
                            d.report!.data.claims.length ? <ul className="small">{d.report!.data.claims.map((claim: { step_id: string; reason: string }) => (
                              <li key={claim.step_id}>愿意承担 {task.draft_plan?.steps.find(s => s.id === claim.step_id)?.title || claim.step_id}：{claim.reason}</li>
                            ))}</ul> : <p className="small muted">本轮没有认领工作项。</p>
                          )}
                          {d.report!.suggestions.map((s, i) => <p key={i} className="suggestion">{s}</p>)}
                          <details>
                            <summary>依据与报告</summary>
                            <pre>{JSON.stringify(d.report, null, 2)}</pre>
                          </details>
                        </section>
                      );
                    })}

                    {!task.dispatches.length && (
                      <div className="empty">
                        需求已保存。开始讨论后，这里会显示各位 Agent 的真实方案与建议。
                      </div>
                    )}
                  </>
                ) : (
                  <>
                    <h2>步骤与验收标准</h2>
                    {task.steps.map(s => {
                      const assignee = s.assignee ? agentMap[s.assignee] : agentMap['codex'];
                      return (
                        <details key={s.id} className="card">
                          <summary style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                            <span>{s.title} · {label(s.status || '')}</span>
                            {assignee && (
                              <span className="agent-tag" style={{ background: assignee.color + '1a', color: assignee.color }}>
                                {assignee.avatar} {assignee.name}
                              </span>
                            )}
                          </summary>
                          <ul>{s.acceptance.map(c => <li key={c}>{c}</li>)}</ul>
                        </details>
                      );
                    })}

                    <h2>后台讨论</h2>
                    {!jobs.length && <p className="muted">尚未登记讨论作业。</p>}
                    {jobs.map(j => (
                      <div className="card" key={j.id}>
                        <strong>{label(j.status)}</strong>
                        <p className="small">
                          {j.error || j.result?.reason || '登记不代表模型已经启动；重启后的未知状态需要人工检查。'}
                        </p>
                      </div>
                    ))}

                    <h2>模型运行记录</h2>
                    {usageByProvider.length > 0 && (
                      <section className="card">
                        <h3>模型报告的 Token 用量</h3>
                        {usageByProvider.map(([provider, usage]) => (
                          <p key={provider}>{provider}：{usage.calls} 次有用量记录；输入 {usage.input.toLocaleString()}，输出 {usage.output.toLocaleString()}，其中缓存输入/读取 {usage.cached.toLocaleString()}。</p>
                        ))}
                        <p className="small">仅汇总提供方实际返回的字段；失败调用若未返回用量则不计入。不同提供方口径不同，这不是费用或节省比例。</p>
                      </section>
                    )}
                    {runs.map(r => (
                      <details key={r.id} className="card">
                        <summary>{r.provider} · {label(r.status)}</summary>
                        <p className="small muted">运行产物可能包含任务内容或本机路径，仅在本机查看。</p>
                        <div className="actions">
                          {Object.entries(artifactLabels).map(([filename, title]) => (
                            <button key={filename} type="button" onClick={() => viewRunArtifact(r.id, filename)}>
                              查看{title}
                            </button>
                          ))}
                        </div>
                        {artifact?.runId === r.id && (
                          <section className="artifact-view" aria-live="polite">
                            <strong>{artifactLabels[artifact.filename]}</strong>
                            {artifact.loading && <p>正在读取…</p>}
                            {artifact.error && <p className="error">{artifact.error}</p>}
                            {artifact.preview && (
                              <>
                                {artifact.preview.truncated && (
                                  <p className="small muted">文件共 {artifact.preview.size_bytes.toLocaleString()} 字节，只显示{artifact.preview.view === 'tail' ? '末尾' : '开头'} 512 KB。</p>
                                )}
                                <pre>{artifact.preview.content}</pre>
                              </>
                            )}
                          </section>
                        )}
                        <pre>{JSON.stringify(r, null, 2)}</pre>
                      </details>
                    ))}

                    <h2>事件记录</h2>
                    {task.events.map(e => (
                      <details key={e.seq}>
                        <summary>{e.kind} · {new Date(e.created_at * 1000).toLocaleTimeString()}</summary>
                        <pre>{JSON.stringify(e.data, null, 2)}</pre>
                      </details>
                    ))}
                  </>
                )}
              </>
            )}
          </div>
        </div>
      </main>

      <aside className="inspector">
        <h2>任务概览</h2>
        <p className="small">一次只推进明确的下一步</p>
        <div className="divider" />
        <h3>协作方式</h3>
        <p>多 Agent 有限接力讨论<br />返工最多两轮<br />开发前由你批准</p>
        <div className="divider" />
        <h3>步骤进度</h3>
        {task?.steps.length ? (
          task.steps.map(s => {
            const assignee = s.assignee ? agentMap[s.assignee] : agentMap['codex'];
            return (
              <details key={s.id}>
                <summary>
                  {s.title}
                  {assignee && (
                    <small style={{ marginLeft: 6, color: assignee.color }}>
                      ({assignee.name})
                    </small>
                  )}
                </summary>
                <p>{label(s.status || '')}</p>
                <ul>{s.acceptance.map(c => <li key={c}>{c}</li>)}</ul>
              </details>
            );
          })
        ) : (
          <p className="small">方案定稿后显示步骤。</p>
        )}
        <div className="divider" />
        <p className="small">不共享其他项目的聊天。后台调用使用本任务绑定的独立会话。</p>
      </aside>

      {/* Agent Manager Modal */}
      {managingAgents && (
        <div className="modal-overlay" onClick={() => setManagingAgents(false)}>
          <div className="modal-card" onClick={e => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <h2>已接入的协作 Agent</h2>
              <button style={{ minHeight: 28, padding: '2px 8px' }} onClick={() => setManagingAgents(false)}>✕ 关闭</button>
            </div>
            <p className="small muted">自定义 Agent 可通过 OpenAI 兼容接口参与任务讨论并接受只读分析步骤；不能修改项目代码或独立验收。分工须经能力校验和你的方案批准。密钥只从启动服务的环境变量读取。连接测试会发送一条不含任务内容的短消息，可能产生少量模型用量。</p>
            <p className="small">DeepSeek API 是云端接口配置；DeepSeek Harness 是本机 CLI 接入，不是重复安装。本机 CLI 仍可能调用云端模型，并不表示离线运行。旧 ID deepseek-hermes 仅为保留历史消息，不代表 Harness。</p>

            <div className="agent-card-list">
              {agents.map(a => (
                <div key={a.id} className="agent-card" style={{ borderLeft: `4px solid ${a.color}` }}>
                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ fontSize: 18 }}>{a.avatar}</span>
                      <strong>{a.name}</strong>
                      <span className="small muted">({a.id})</span>
                      <span className="badge" style={{ fontSize: 11 }}>{a.type === 'cli' ? '本机 CLI' : 'API 接口'}</span>
                    </div>
                    <p className="small" style={{ margin: '4px 0 0' }}>{a.description || '无描述'}</p>
                    {a.endpoint && <p className="small muted" style={{ margin: 0 }}>接口: {a.endpoint} ({a.model})</p>}
                  </div>
                  <div className="agent-card-actions">
                    <button type="button" disabled={busy} onClick={() => act(async () => {
                      await post(`/agents/${a.id}/enabled`, { enabled: !a.enabled });
                      setAgents((await api.getAgents()).agents);
                    })}>{a.enabled ? '停用' : '启用（不会立即调用模型）'}</button>
                    <span className={`badge ${a.ready ? 'active' : ''}`}>
                      {!a.ready ? '未配置／已禁用' : a.type === 'openai_compat' ? '配置就绪 · 未验证连接' : '内置 Agent · 调用时验证'}
                    </span>
                    {a.type === 'openai_compat' && (
                      <button type="button" disabled={!a.ready || !!probingAgent}
                        onClick={() => testAgentConnection(a.id)}>
                        {probingAgent === a.id ? '测试中…' : '测试连接（可能计费）'}
                      </button>
                    )}
                    {probeResults[a.id] && (
                      <span className="small" role="status">
                        {probeResults[a.id].connected ? '连接可用' : '连接失败'}：{probeResults[a.id].message}
                        {typeof probeResults[a.id].usage?.total_tokens === 'number' &&
                          ` · 本次 ${probeResults[a.id].usage?.total_tokens} Token`}
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>

            <hr className="divider" />
            <h3>添加 API Agent（如 DeepSeek、Qwen、Ollama 等）</h3>
            <form onSubmit={handleAddAgent} className="stack" style={{ marginTop: 12 }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                <label>标识 ID (英文)<input required placeholder="如 qwen-coder" value={newAgent.id} onChange={e => setNewAgent({ ...newAgent, id: e.target.value.toLowerCase().replace(/\s+/g, '-') })} /></label>
                <label>显示名称<input required placeholder="如 Qwen 2.5 Coder" value={newAgent.name} onChange={e => setNewAgent({ ...newAgent, name: e.target.value })} /></label>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                <label>接入类型
                  <select value={newAgent.type} onChange={e => setNewAgent({ ...newAgent, type: e.target.value })}>
                    <option value="openai_compat">OpenAI 兼容接口 (DeepSeek/Ollama/vLLM)</option>
                  </select>
                </label>
                <label>模型名称<input placeholder="如 deepseek-chat / qwen2.5-coder" value={newAgent.model} onChange={e => setNewAgent({ ...newAgent, model: e.target.value })} /></label>
              </div>
              {newAgent.type === 'openai_compat' && (
                <>
                  <label>API Endpoint (基础地址)<input placeholder="如 https://api.deepseek.com/v1 或 http://localhost:11434/v1" value={newAgent.endpoint} onChange={e => setNewAgent({ ...newAgent, endpoint: e.target.value })} /></label>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                    <label>API Key 环境变量名<input placeholder="如 DEEPSEEK_API_KEY" value={newAgent.api_key_env} onChange={e => setNewAgent({ ...newAgent, api_key_env: e.target.value })} /></label>
                    <p className="small muted">公网接口须先在启动服务的终端设置该环境变量；本机 Ollama 可留空。密钥不会保存在项目数据库中。</p>
                  </div>
                </>
              )}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                <label>图标 Emoji<input placeholder="如 🧠, 🚀, 🤖" value={newAgent.avatar} onChange={e => setNewAgent({ ...newAgent, avatar: e.target.value })} /></label>
                <label>主题颜色<input type="color" value={newAgent.color} onChange={e => setNewAgent({ ...newAgent, color: e.target.value })} /></label>
              </div>
              <label>定位描述<input placeholder="如：负责高难度算法推导与边界用例分析" value={newAgent.description} onChange={e => setNewAgent({ ...newAgent, description: e.target.value })} /></label>
              <div className="actions" style={{ marginTop: 8 }}>
                <button className="primary" type="submit" disabled={busy || !newAgent.id || !newAgent.name}>注册 Agent</button>
                <button type="button" onClick={() => setManagingAgents(false)}>取消</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
