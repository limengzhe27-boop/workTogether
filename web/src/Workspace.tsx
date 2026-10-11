import React, { useEffect, useRef, useState } from 'react';
import { api, request } from './api';
import { TaskChat } from './TaskChat';
import { Dialog } from './Dialog';
import { ProjectNavigation } from './ProjectNavigation';
import { ArrowUp, Folder, PanelLeft, Users, Search, ClipboardList, MessageSquare, FileDiff, Save, RotateCcw, Trash2, Pencil, RefreshCw, Layers } from 'lucide-react';
import { Project, TaskDetail, TaskSummary, RunRecord, RunArtifactPreview, AgentInfo, AgentProbeResult, ReportEnvelope, ProjectDiff, ProjectFileEntry } from './types';

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
  ANALYZING: '分析中',
  ANALYZE: '分析交付',
  PLANNING: '准备方案',
  PLAN_REVIEW: '审查方案',
  EXECUTING: '开发与验收',
  COMPLETED: '已完成',
  ACTIVE: '待推进',
  PAUSED: '已暂停',
  BLOCKED: '需要处理',
  RUNNING: '执行中',
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

type ProviderStatus = Record<string, { available: boolean; executable: string }>;

/** Name of the missing local executable, or '' when the agent is not a local CLI. */
function missingExecutable(agent: AgentInfo, providers: ProviderStatus | null): string {
  if (agent.type !== 'cli' || !providers) return '';
  const info = providers[agent.base_agent || agent.id];
  if (!info || info.available) return '';
  return info.executable;
}

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
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [searching, setSearching] = useState(false);
  const [searchText, setSearchText] = useState('');
  const [tab, setTab] = useState('progress');
  const [dialog, setDialog] = useState<'' | 'team' | 'plan' | 'authorize' | 'discuss' | 'execute' | 'context'>('');
  const [composerRequest, setComposerRequest] = useState(0);
  const [chatPending, setChatPending] = useState(false);
  const [manualDispatchId, setManualDispatchId] = useState('');
  const [handoffText, setHandoffText] = useState('');
  const [reportText, setReportText] = useState('');
  const actionLock = useRef(false);
  const selected = useRef({ project, taskId });
  selected.current = { project, taskId };
  const currentProject = projects.find(p => p.id === project);

  useEffect(() => {
    if (!navigation) return;
    const query = window.matchMedia('(max-width: 959px)');
    if (!query.matches) return;
    const sidebar = document.querySelector<HTMLElement>('.sidebar')!;
    const main = document.querySelector<HTMLElement>('main')!;
    const trigger = document.activeElement as HTMLElement | null;
    const chrome = document.querySelector<HTMLElement>('.windowbar');
    main.setAttribute('inert', '');
    chrome?.setAttribute('inert', '');
    const controls = () => Array.from(sidebar.querySelectorAll<HTMLElement>('button:not(:disabled),select:not(:disabled)'))
      .filter(el => el.getClientRects().length > 0);
    controls()[0]?.focus();
    const keys = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); setNavigation(false); }
      if (event.key !== 'Tab') return;
      const items = controls();
      const first = items[0], last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    const resized = () => { if (!query.matches) setNavigation(false); };
    document.addEventListener('keydown', keys);
    query.addEventListener('change', resized);
    return () => {
      main.removeAttribute('inert');
      chrome?.removeAttribute('inert');
      document.removeEventListener('keydown', keys);
      query.removeEventListener('change', resized);
      trigger?.focus();
    };
  }, [navigation]);

  // Multi-agent states
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [homeSelectedIds, setHomeSelectedIds] = useState<string[] | null>(null);
  const [teamChoice, setTeamChoice] = useState<{ taskId: string; members: string[] }>({ taskId: '', members: [] });
  const [providers, setProviders] = useState<ProviderStatus | null>(null);
  const [managingAgents, setManagingAgents] = useState(false);
  const [probingAgent, setProbingAgent] = useState('');
  const [probeResults, setProbeResults] = useState<Record<string, AgentProbeResult>>({});
  // Project context dialog: shared memory, changed files and bounded file list.
  const [contextProject, setContextProject] = useState('');
  const [contextMemory, setContextMemory] = useState('');
  const [contextSaved, setContextSaved] = useState('');
  const [contextDiff, setContextDiff] = useState<ProjectDiff | null>(null);
  const [contextFiles, setContextFiles] = useState<ProjectFileEntry[]>([]);
  const [contextFilesTruncated, setContextFilesTruncated] = useState(false);
  const [preview, setPreview] = useState<{ path: string; content: string; size: number; truncated: boolean } | null>(null);
  const [previewBusy, setPreviewBusy] = useState('');
  const [contextNote, setContextNote] = useState('');
  const [contextLoading, setContextLoading] = useState(false);
  const [contextError, setContextError] = useState('');
  const [revertTarget, setRevertTarget] = useState('');
  const [profileDraft, setProfileDraft] = useState<{
    id: string; name: string; description: string; system_prompt: string; model: string; secret_key: string;
  } | null>(null);
  const [savingProfile, setSavingProfile] = useState(false);
  const [newAgent, setNewAgent] = useState({
    id: '',
    name: '',
    type: 'openai_compat',
    base_agent: '',
    endpoint: 'https://api.deepseek.com/v1',
    model: 'deepseek-chat',
    api_key_env: 'DEEPSEEK_API_KEY',
    secret_key: '',
    system_prompt: '',
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
    setSearching(false);
    setTab('progress');
    setDialog('');
    setChatPending(false);
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
    const payload: Partial<AgentInfo> & { secret_key?: string } = {
      id: newAgent.id, name: newAgent.name, type: newAgent.type,
      description: newAgent.description, avatar: newAgent.avatar, color: newAgent.color,
      system_prompt: newAgent.system_prompt,
    };
    if (newAgent.type === 'cli') {
      payload.base_agent = newAgent.base_agent || 'codex';
      payload.model = newAgent.model;
    } else {
      payload.endpoint = newAgent.endpoint;
      payload.model = newAgent.model;
      payload.api_key_env = newAgent.api_key_env;
      if (newAgent.secret_key) payload.secret_key = newAgent.secret_key;
    }
    await act(async () => {
      await api.addAgent(payload);
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
        base_agent: '',
        endpoint: 'https://api.deepseek.com/v1',
        model: 'deepseek-chat',
        api_key_env: 'DEEPSEEK_API_KEY',
        secret_key: '',
        system_prompt: '',
        description: '',
        color: '#8b5cf6',
        avatar: '🧠',
      });
    });
  }

  async function openContext(projectId: string) {
    if (!projectId) return;
    setDialog('context');
    setContextProject(projectId);
    setContextLoading(true);
    setContextError('');
    setContextNote('');
    setRevertTarget('');
    setPreview(null);
    try {
      const [memory, files, diff] = await Promise.all([
        api.getProjectMemory(projectId),
        api.getProjectFiles(projectId),
        api.getProjectDiff(projectId),
      ]);
      setContextMemory(memory.memory);
      setContextSaved(memory.memory);
      setContextFiles(files.files);
      setContextFilesTruncated(files.truncated);
      setContextDiff(diff);
    } catch (e) {
      setContextError((e as Error).message);
    } finally {
      setContextLoading(false);
    }
  }

  async function reloadContext(projectId: string) {
    try {
      const [files, diff] = await Promise.all([api.getProjectFiles(projectId), api.getProjectDiff(projectId)]);
      setContextFiles(files.files);
      setContextFilesTruncated(files.truncated);
      setContextDiff(diff);
    } catch (e) {
      setContextError((e as Error).message);
    }
  }

  async function previewContextFile(path: string) {
    if (!contextProject || previewBusy) return;
    if (preview?.path === path) { setPreview(null); return; }
    setPreviewBusy(path);
    setContextError('');
    try {
      const file = await api.getProjectFile(contextProject, path);
      setPreview({ path: file.path, content: file.content, size: file.size_bytes, truncated: file.truncated });
    } catch (e) {
      setContextError((e as Error).message);
    } finally {
      setPreviewBusy('');
    }
  }

  async function renameCurrentTask() {
    if (!task) return;
    const title = window.prompt('新的会话名称', task.title);
    if (title === null) return;
    const trimmed = title.trim();
    if (!trimmed || trimmed === task.title) return;
    await act(async () => {
      const updated = await api.renameTask(taskId, trimmed);
      setTask(current => (current ? { ...current, title: updated.title } : current));
    });
  }

  async function deleteCurrentTask() {
    if (!task) return;
    if (!window.confirm(`删除会话「${task.title}」？其消息、运行记录与事件会一并删除，无法撤销。`)) return;
    await act(async () => {
      await api.deleteTask(taskId);
      choose('');
    });
  }

  async function deleteContextProject() {
    const ctx = projects.find(p => p.id === contextProject);
    if (!ctx) return;
    const count = typeof ctx.task_count === 'number' ? `其 ${ctx.task_count} 个会话及` : '其全部会话及';
    if (!window.confirm(`删除项目「${ctx.name}」？${count}消息、运行记录都会被删除；代码文件本身不受影响。`)) return;
    await act(async () => {
      await api.deleteProject(ctx.id);
      const rest = (await api.getProjects()).projects;
      setProjects(rest);
      setTasks([]);
      choose('');
      setProject(current => (current === ctx.id ? (rest[0]?.id || '') : current));
      setDialog('');
    });
  }

  async function saveContextMemory(append: boolean) {
    if (!contextProject) return;
    setContextError('');
    try {
      const res = await api.updateProjectMemory(contextProject, contextMemory, append);
      setContextMemory(res.memory);
      setContextSaved(res.memory);
      setContextNote(append ? '已追加到项目记忆。' : '已保存项目记忆，后续对话会带上它。');
      setProjects(list => list.map(p => p.id === contextProject ? { ...p, memory: res.memory } : p));
    } catch (e) {
      setContextError((e as Error).message);
    }
  }

  async function revertContextFile(path: string) {
    if (!contextProject || !path) return;
    const confirmed = window.confirm(
      `确认还原 ${path}？\n已跟踪文件会恢复到 HEAD 版本，未跟踪文件会被删除。此操作不可撤销。`);
    if (!confirmed) return;
    setContextError('');
    setRevertTarget(path);
    try {
      await api.revertProjectFile(contextProject, path);
      setContextNote(`已还原 ${path}。`);
      await reloadContext(contextProject);
    } catch (e) {
      setContextError((e as Error).message);
    } finally {
      setRevertTarget('');
    }
  }

  function beginProfile(agent: AgentInfo) {
    setProfileDraft({
      id: agent.id,
      name: agent.name,
      description: agent.description || '',
      system_prompt: agent.system_prompt || '',
      model: agent.model || '',
      secret_key: '',
    });
  }

  async function saveProfile(e: React.FormEvent) {
    e.preventDefault();
    if (!profileDraft || savingProfile) return;
    setSavingProfile(true);
    setError('');
    try {
      const patch: Partial<AgentInfo> & { secret_key?: string } = {
        name: profileDraft.name,
        description: profileDraft.description,
        system_prompt: profileDraft.system_prompt,
      };
      if (profileDraft.model) patch.model = profileDraft.model;
      if (profileDraft.secret_key) patch.secret_key = profileDraft.secret_key;
      await api.updateAgentProfile(profileDraft.id, patch);
      setAgents((await api.getAgents()).agents);
      setProfileDraft(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSavingProfile(false);
    }
  }

  async function clearStoredKey(agentId: string) {
    setError('');
    try {
      await api.updateAgentProfile(agentId, { secret_key: '' });
      setAgents((await api.getAgents()).agents);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function removeAgent(agent: AgentInfo) {
    if (!window.confirm(`移除 Agent「${agent.name}」？仅影响尚未启动、只发给它的消息，历史记录会保留。`)) return;
    await act(async () => {
      await api.deleteAgent(agent.id);
      setAgents((await api.getAgents()).agents);
      setProfileDraft(null);
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

  const members = task?.team?.members || [];
  const finishedSteps = task?.steps.filter(step => step.status === 'COMPLETED').length || 0;
  const workflowReports = task?.dispatches.filter(d => d.report && d.kind !== 'CHAT' &&
    (d.payload.requirement_revision || 1) === (task.revision || 1)) || [];
  const latestReport = workflowReports[workflowReports.length - 1];
  const running = inflight || planningBusy || executionBusy || teamBusy;
  const progressText = task?.status === 'PAUSED' ? '已暂停后续工作' :
    task?.status === 'BLOCKED' ? '需要处理' : running ? '团队工作中' :
    chatPending ? '正在处理消息' : task?.phase === 'EXECUTING' && !approval?.approved ? '方案待你确认' :
    task ? label(task.phase) : '';

  const readyAgents = agents.filter(a => a.enabled && a.ready);
  const readyIds = readyAgents.map(a => a.id);
  const homeActiveAgentIds = homeSelectedIds === null ? readyIds : readyIds.filter(id => homeSelectedIds.includes(id));
  const isHomeAllSelected = readyIds.length > 0 && homeActiveAgentIds.length === readyIds.length;
  const homeRecipientValue = homeActiveAgentIds.length === 0 ? '' :
    homeActiveAgentIds.length === readyIds.length ? 'all' :
    homeActiveAgentIds.length === 1 ? homeActiveAgentIds[0] : homeActiveAgentIds.join(',');
  const activeTeamIds = members.length ? members : readyAgents.map(a => a.id);

  function toggleHomeAllAgents() {
    if (isHomeAllSelected) setHomeSelectedIds([]);
    else setHomeSelectedIds([...readyIds]);
  }

  function toggleHomeOneAgent(agentId: string) {
    if (homeActiveAgentIds.includes(agentId)) {
      setHomeSelectedIds(homeActiveAgentIds.filter(x => x !== agentId));
    } else {
      setHomeSelectedIds(readyIds.filter(x => x === agentId || homeActiveAgentIds.includes(x)));
    }
  }

  function toggleNavigation() {
    if (window.matchMedia('(max-width: 959px)').matches) setNavigation(open => !open);
    else setSidebarCollapsed(collapsed => !collapsed);
  }

  function openSearch() { setSearchText(''); setNavigation(false); setSearching(true); }

  return (
    <div className={'workspace ' + (sidebarCollapsed ? 'sidebar-collapsed' : '')}>
      <header className="windowbar">
        <div className="window-leading">
          <button className="icon-button sidebar-toggle" onClick={toggleNavigation}
            aria-label="切换项目侧栏" title="展开／收起项目侧栏"><PanelLeft /></button>
        </div>
        <div className="window-title">
          <Folder aria-hidden="true" />
          {currentProject && task && <span className="window-project-chip">{currentProject.name} /</span>}
          <h1 title={task?.title || currentProject?.name}>
            {task?.title || (taskId ? '正在加载…' : currentProject?.name || 'WorkTogether')}
          </h1>
          {task && <span className={'status-pill status-' + task.status.toLowerCase()}>{label(task.status)}</span>}
        </div>
        <div className="window-actions">
          {task?.draft_plan && <button className="header-chip-btn" onClick={() => setDialog('plan')} aria-label="查看方案" title="查看方案">
            <ClipboardList /><span>方案</span>
          </button>}
          {task && (
            <button className="header-chip-btn" aria-label={tab === 'details' ? '返回对话' : '任务详情'}
              aria-pressed={tab === 'details'} onClick={() => setTab(tab === 'details' ? 'progress' : 'details')}>
              {tab === 'details' ? <MessageSquare /> : <ClipboardList />}
              <span>{tab === 'details' ? '对话' : '记录'}</span>
            </button>
          )}
        </div>
      </header>
      {navigation && <button className="nav-scrim" tabIndex={-1} aria-label="关闭项目导航" onClick={() => setNavigation(false)} />}
      <aside className={'sidebar ' + (navigation ? 'visible' : '')} aria-label="项目与任务">
        <ProjectNavigation projects={projects} project={project} tasks={tasks} taskId={taskId} busy={busy}
          onProject={id => { setProject(id); setTasks([]); choose(''); }} onTask={choose} onNew={newTask}
          onAdd={() => { setNavigation(false); setAdding(true); }} onSearch={openSearch} onClose={() => setNavigation(false)}
          onSettings={() => { setNavigation(false); setManagingAgents(true); }} />
      </aside>
      <main className="main" aria-label="工作对话">
        {error && <div className="global-error error" role="alert">{error}<button onClick={() => setError('')}>关闭提示</button></div>}
        {!taskId ? <div className="home-scroll"><div className="reading page-enter">
              <section className="home">
                <div className="home-header">
                  <h1>{currentProject ? `${currentProject.name} · 新建会话` : '新建会话'}</h1>
                  {currentProject && <p className="small muted">{currentProject.path}</p>}
                  {currentProject && (
                    <div className="home-context-actions">
                      <button type="button" className="header-chip-btn" onClick={() => openContext(currentProject.id)}
                        aria-label="项目上下文" title="项目记忆与代码变更">
                        <Layers /><span>项目上下文</span>
                      </button>
                      <span className="small muted">每次对话自动附带 README 与文件列表</span>
                    </div>
                  )}
                </div>

                <form className="composer" onSubmit={e => {
                  e.preventDefault();
                  const p = project;
                  const content = draft.trim();
                  if (!p || !content) return;
                  const recipient = homeRecipientValue;
                  act(async () => {
                    const t = await api.addTask(p, content.split('\n')[0].slice(0, 60), content);
                    save('wt:draft:' + p, '');
                    setDraft('');
                    choose(t.id);
                    if (recipient) {
                      await post(`/tasks/${t.id}/messages`, {
                        message_id: crypto.randomUUID(),
                        recipient,
                        content,
                      });
                    }
                  });
                }}>
                  <textarea
                    id="requirement"
                    aria-label="新会话消息"
                    placeholder={currentProject ? `在 ${currentProject.name} 下输入问题或任务，直接与选中的 Agent 交流… (⌘ + Enter 发送)` : '请先在左侧点击项目右侧 + 添加本地项目…'}
                    value={draft}
                    onKeyDown={e => {
                      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && !e.nativeEvent.isComposing) {
                        e.preventDefault();
                        e.currentTarget.form?.requestSubmit();
                      }
                    }}
                    onChange={e => {
                      setDraft(e.target.value);
                      save('wt:draft:' + project, e.target.value);
                    }}
                  />
                  <div className="composer-footer">
                    <div className="agent-multiselect-bar" role="group" aria-label="选择参与对话的 Agent">
                      <button
                        type="button"
                        className={'agent-select-pill select-all-pill ' + (isHomeAllSelected ? 'is-selected' : '')}
                        aria-pressed={isHomeAllSelected}
                        disabled={busy || readyAgents.length === 0}
                        onClick={toggleHomeAllAgents}
                        title={isHomeAllSelected ? '再次点击取消全选' : '点击全选所有就绪 Agent'}
                      >
                        <span className="pill-check">{isHomeAllSelected ? '✓' : ''}</span>
                        <span>全部</span>
                      </button>
                      {readyAgents.map(a => {
                        const selected = homeActiveAgentIds.includes(a.id);
                        const color = a.color || '#3072d2';
                        const missing = missingExecutable(a, providers);
                        return (
                          <button
                            key={a.id}
                            type="button"
                            className={'agent-select-pill ' + (selected ? 'is-selected' : '') + (missing ? ' is-unavailable' : '')}
                            aria-pressed={selected}
                            disabled={busy}
                            style={{ '--pill-color': color } as React.CSSProperties}
                            title={missing ? `本机未找到 ${missing}，发送会立即失败` : undefined}
                            onClick={() => toggleHomeOneAgent(a.id)}
                          >
                            <span className="agent-dot" style={{ background: color }} />
                            <span>{a.name}</span>
                            {missing && <span className="pill-warn" aria-label="本机 CLI 未找到">!</span>}
                            {selected && <span className="pill-check">✓</span>}
                          </button>
                        );
                      })}
                    </div>
                    <button className="primary icon-button send-button" title="开始新会话" aria-label="发送并开始新会话"
                      disabled={!project || !draft.trim() || busy || (readyAgents.length > 0 && homeActiveAgentIds.length === 0)}>
                      <ArrowUp />
                    </button>
                  </div>
                </form>
                {readyAgents.length > 0 && homeActiveAgentIds.length === 0 && (
                  <p className="small compose-notice">请点击上方「全部」或单个 Agent 选择消息接收对象。</p>
                )}
                {!project && <p className="notice">请先在左侧「项目」右侧点击「+」关联一个本机代码文件夹。</p>}
              </section>

        </div></div> : !task ? <p className="empty" role="status">正在加载会话…</p> : (
          <div className="task-layout">
            {task.status === 'BLOCKED' && <div className="attention-bar" role="alert">
              当前会话有未完成项：{task.block_reason || '请查看原因'}
              <button onClick={() => setTab('details')}>查看记录</button>
            </div>}
            {task.status === 'PAUSED' && <p className="inline-notice">当前会话已暂停后续派发。</p>}
            <div className="conversation-panel" hidden={tab !== 'progress'}>
              <TaskChat key={taskId} taskId={taskId} projectId={project}
                agents={agents} providers={providers}
                completed={task.status === 'COMPLETED'} onPending={setChatPending}
                onDetails={() => setTab('details')} visible={tab === 'progress'} />
            </div>
            <div className="detail-scroll" hidden={tab !== 'details'}>
              <div className="reading">
                <div className="detail-heading"><h2>任务详情</h2><button onClick={() => setTab('progress')}>返回对话</button></div>
                <p className="small">需求版本 {task.revision || 1} · {label(task.phase)} · {label(task.status)}</p>
                <div className="actions">
                  <button disabled={!project} onClick={() => openContext(project)}><Layers aria-hidden="true" />项目上下文与变更</button>
                  <button disabled={locked || !task} onClick={renameCurrentTask}><Pencil aria-hidden="true" />重命名会话</button>
                  <button disabled={locked || !task || busy} onClick={deleteCurrentTask}><Trash2 aria-hidden="true" />删除会话</button>
                  <button disabled={!canRevise} onClick={beginEdit}>修改需求</button>
                  {task.status !== 'COMPLETED' && <button disabled={busy} onClick={() => act(() => task.status === 'PAUSED' ? api.resumeTask(taskId) : api.pauseTask(taskId))}>
                    {task.status === 'PAUSED' ? '恢复后续工作' : '暂停后续工作'}</button>}
                  {task.team && task.phase === 'PLANNING' && !revisionStarted && <button disabled={locked || task.status !== 'ACTIVE'}
                    onClick={() => setDialog('authorize')}>授权团队连续执行</button>}
                </div>
                <p className="small">暂停不会终止已运行的模型。执行状态不明时，请先核对运行记录。</p>
                <details className="card"><summary>手动派发与环境信息</summary>
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
                          throw new Error('需要先完成团队意见或认领轮，请使用“讨论方案”。');
                        }
                      })}>
                      手动派发下一项（不调用模型）
                    </button>
                  )}

                  {providers && <p className="small">本机入口：{Object.entries(providers).map(([key, value]) => key + (value.available ? ' 已找到' : ' 未找到')).join(' · ')}。未找到入口时可使用手动交接；配置后请重启服务。</p>}
                </details>
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


                <details className="card" open><summary>协作记录 · {task.dispatches.filter(d => d.report && d.kind !== 'CHAT').length} 项</summary>
                    {task.dispatches.filter(d => d.report && d.kind !== 'CHAT').map(d => {
                      const ag = agentMap[d.role];
                      const avatar = ag?.avatar || (ag?.name || d.role).slice(0, 1).toUpperCase();
                      const name = ag?.name || d.role;
                      const color = ag?.color || '#3072d2';
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


                </details>
                    <h2>步骤与验收标准</h2>
                    {task.steps.map(s => {
                      const assignee = s.assignee ? agentMap[s.assignee] : agentMap['codex'];
                      const verifier = s.verifier ? agentMap[s.verifier] : undefined;
                      return (
                        <details key={s.id} className="card">
                          <summary style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                            <span>{s.title} · {label(s.status || '')}</span>
                            <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                              {assignee && (
                                <span className="agent-tag" style={{ borderColor: assignee.color || '#3072d2' }}>
                                  {assignee.avatar} {assignee.name}
                                </span>
                              )}
                              {verifier && (
                                <span className="badge">验收: {verifier.name}</span>
                              )}
                            </span>
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

              </div>
            </div>
          </div>
        )}
      </main>
            {adding && (
              <Dialog title="添加本地项目" onClose={() => setAdding(false)}><form className="stack" onSubmit={e => {
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
                {error && <p className="error" role="alert">{error}</p>}
                <label>项目名称<input required value={name} onChange={e => setName(e.target.value)} /></label>
                <label>项目目录<input required placeholder="/Users/你的名字/projects/my-app" value={path} onChange={e => setPath(e.target.value)} /></label>
                <p className="small">关联现有目录，不会自动创建文件或启动模型。</p>
                <div className="actions">
                  <button className="primary" disabled={busy}>添加项目</button>
                  <button type="button" onClick={() => setAdding(false)}>取消</button>
                </div>
              </form></Dialog>
            )}


                {edit && task && (
                  <Dialog title="修改需求" onClose={() => setEdit(null)}><form className="stack" onSubmit={e => {
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
                    {error && <p className="error" role="alert">{error}</p>}
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
                  </form></Dialog>
                )}


      {task && dialog === 'team' && <Dialog title="任务团队" onClose={() => setDialog('')}>
        <p className="small">成员按能力参与讨论与分工。就绪表示配置可用，不代表登录或额度已经验证。</p>
        <div className="team-list">{members.map(id => {
          const ag = agentMap[id];
          const color = ag?.color || '#3072d2';
          return <div key={id} className="team-member">
            <span className="avatar" style={{ borderColor: color, color }}>{ag?.avatar || (ag?.name || id).slice(0, 1).toUpperCase()}</span>
            <strong>{ag?.name || id}</strong>
            {task.team?.coordinator === id && <span className="badge">统筹规划</span>}
            {task.team?.reviewer === id && <span className="badge">方案复核</span>}
            <span className="small">{ag?.ready ? '配置就绪' : '未就绪'}</span>
          </div>;
        })}</div>
                {task.phase === 'PLANNING' && !revisionStarted && (
                  <section className="card">
                    <h2>调整参与本任务的成员</h2>
                    <p className="muted">选择参与本任务的成员。规划与执行按能力分工；普通 API 不能修改代码或运行测试。可用配置不代表登录与配额已验证。</p>
                    <div className="actions">
                      {agents.filter(a => a.ready).map(a => {
                        const members = teamChoice.taskId === taskId ? teamChoice.members : task.team?.members || ['codex', 'antigravity'];
                        return <label key={a.id} className="agent-check-pill">
                          <input type="checkbox" disabled={locked} checked={members.includes(a.id)} onChange={e => setTeamChoice({
                            taskId, members: e.target.checked ? [...members, a.id] : members.filter(id => id !== a.id),
                          })} /> {a.avatar || ''} {a.name}
                        </label>;
                      })}
                    </div>
                    <button disabled={locked || task.status !== 'ACTIVE'} onClick={() => act(() => post(base + '/team', {
                      members: teamChoice.taskId === taskId ? teamChoice.members : task.team?.members || ['codex', 'antigravity'],
                    }))}>保存团队与能力分工</button>
                  </section>
                )}
                {task.team && <p className="muted">规划协调：{agentMap[task.team.coordinator]?.name || task.team.coordinator} · 方案复核：{agentMap[task.team.reviewer]?.name || task.team.reviewer} · 步骤执行与验收按能力另行分配</p>}


        {error && <p className="error" role="alert">{error}</p>}
        <button onClick={() => { setDialog(''); setManagingAgents(true); }}>管理接入的 Agent</button>
      </Dialog>}
      {task && dialog === 'plan' && <Dialog title="任务方案" onClose={() => setDialog('')}>
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
                                  <span className="agent-tag" style={{ borderColor: assignee.color || '#3072d2' }}>
                                    {assignee.avatar} {assignee.name}
                                  </span>
                                )}
                              </div>
                              {s.verifier && <p className="muted">独立验收：{agentMap[s.verifier]?.avatar || ''} {agentMap[s.verifier]?.name || s.verifier}</p>}
                              {s.assignment_reason && <p className="muted">分工依据：{s.assignment_reason}</p>}
                              <ul>{s.acceptance.map(c => <li key={c}>{c}</li>)}</ul>
                            </div>
                          );
                        })}
                      </section>
                    )}


        {error && <p className="error" role="alert">{error}</p>}
        {task.phase === 'EXECUTING' && !approval?.approved && <>
          <p className="small">批准当前版本后仍需点击“开始执行”。工具权限需另行确认；修改需求会撤销批准。</p>
          <button className="primary" disabled={locked || !approval || task.status !== 'ACTIVE'} onClick={() => act(async () => {
            await post(base + '/approve', { digest: approval!.digest }); setDialog('');
          })}>批准此方案</button>
        </>}
      </Dialog>}
      {dialog === 'context' && <Dialog title="项目上下文" onClose={() => setDialog('')}>
        {(() => {
          const ctx = projects.find(p => p.id === contextProject);
          return <>
            <p className="small muted">{ctx ? `${ctx.name} · ${ctx.path}` : '未选择项目'}</p>
            <p className="small">项目记忆与 README、文件列表会在每次对话派发时一并提供给 Agent。
              这里的内容本机保存，不写入你的代码仓库。</p>
            {contextError && <p className="error" role="alert">{contextError}</p>}
            {contextNote && <p className="small notice" role="status">{contextNote}</p>}
            {contextLoading && <p className="empty" role="status">正在读取项目信息…</p>}

            <section className="card context-block">
              <h2><Layers aria-hidden="true" /> 项目记忆</h2>
              <label className="sr-only" htmlFor="project-memory">项目记忆</label>
              <textarea id="project-memory" rows={6} value={contextMemory}
                placeholder="例如：使用 Python 3.9，无第三方依赖；改动必须跑 python3 -m unittest；不要动 .collab/。"
                onChange={e => setContextMemory(e.target.value)} />
              <div className="actions">
                <button type="button" className="primary" disabled={contextLoading || contextMemory === contextSaved}
                  onClick={() => saveContextMemory(false)}><Save aria-hidden="true" />保存记忆</button>
                <button type="button" disabled={contextLoading || !contextMemory.trim()}
                  onClick={() => saveContextMemory(true)}>追加到现有记忆</button>
                {contextMemory !== contextSaved && <span className="small muted">有未保存的修改</span>}
              </div>
            </section>

            <section className="card context-block">
              <h2><FileDiff aria-hidden="true" /> 代码变更 {contextDiff?.is_git ? `· ${contextDiff.files.length} 个文件` : ''}</h2>
              {!contextDiff?.is_git && <p className="muted">该项目不是 Git 仓库，无法列出变更或还原文件。</p>}
              {contextDiff?.is_git && contextDiff.files.length === 0 && <p className="muted">工作区没有未提交的改动。</p>}
              {contextDiff?.is_git && contextDiff.files.length > 0 && <>
                <ul className="diff-file-list">{contextDiff.files.map(file => <li key={file.path}>
                  <code className="diff-status">{file.status}</code>
                  <span className="diff-path" title={file.path}>{file.path}</span>
                  <button type="button" disabled={!!revertTarget} onClick={() => revertContextFile(file.path)}>
                    <RotateCcw aria-hidden="true" />{revertTarget === file.path ? '还原中…' : '还原'}
                  </button>
                </li>)}</ul>
                <p className="small muted">还原已跟踪文件会恢复到 HEAD；未跟踪文件会被删除。平台不会替你确认 Agent 的改动是否正确。</p>
                <details className="diff-view"><summary>查看统一 diff</summary>
                  <pre>{contextDiff.diff || '（无文本差异，可能只有未跟踪文件）'}</pre>
                  {contextDiff.truncated && <p className="small muted">diff 已截断显示。</p>}
                </details>
              </>}
              <div className="actions">
                <button type="button" disabled={contextLoading} onClick={() => reloadContext(contextProject)}>
                  <RefreshCw aria-hidden="true" />刷新
                </button>
              </div>
            </section>

            <details className="card context-block"><summary>项目文件 · {contextFiles.length}{contextFilesTruncated ? '+' : ''}</summary>
              <p className="small muted">点击文件名可只读预览前 12 KB；不会执行任何内容。</p>
              <ul className="context-file-list">{contextFiles.map(file => <li key={file.path}>
                <button type="button" className="context-file-name" aria-expanded={preview?.path === file.path}
                  disabled={!!previewBusy} onClick={() => previewContextFile(file.path)}>
                  <span className="diff-path" title={file.path}>{file.path}</span>
                </button>
                <span className="small muted">{previewBusy === file.path ? '读取中…' : `${file.size} B`}</span>
              </li>)}</ul>
              {contextFilesTruncated && <p className="small muted">文件较多，仅显示前 {contextFiles.length} 个。</p>}
              {preview && <>
                <div className="artifact-view">
                  <p className="small">{preview.path} · {preview.size} B{preview.truncated ? ' · 仅显示开头部分' : ''}</p>
                  <pre>{preview.content}</pre>
                </div>
                <div className="actions"><button type="button" onClick={() => setPreview(null)}>收起预览</button></div>
              </>}
            </details>

            <details className="card context-block danger-zone"><summary>项目维护</summary>
              <p className="small muted">删除项目会移除它在本平台的会话、消息与运行记录；你的代码文件不会被删除。此操作不可撤销。</p>
              <div className="actions">
                <button type="button" className="danger" disabled={busy || contextLoading} onClick={deleteContextProject}>
                  <Trash2 aria-hidden="true" />删除此项目及其记录
                </button>
              </div>
            </details>
          </>;
        })()}
      </Dialog>}
      {task && ['discuss', 'execute', 'authorize'].includes(dialog) && <Dialog
        title={dialog === 'discuss' ? '开始方案讨论' : dialog === 'execute' ? '开始开发与验收' : '授权团队连续执行'} onClose={() => setDialog('')}>
        <p>{dialog === 'discuss' ? '团队将讨论需求、认领工作并形成方案；定稿后等待你审阅，不直接开始开发。' :
          dialog === 'execute' ? '按已批准的方案推进开发和独立验收。本轮最多 6 次 Agent 调用。' :
          '团队将在当前需求范围内讨论、分工并直接执行，定稿后不再逐步询问。'}</p>
        <p className="small">{dialog === 'discuss' ? (task.team ? '两轮成员发言与三次规划，最多 ' + (2 * members.length + 3) + ' 次调用。' : '最多三轮。') :
          dialog === 'authorize' ? '本轮最多 ' + (2 * members.length + 15) + ' 次调用。' : ''}
          将消耗选定 Agent 额度，外部服务会收到必要任务信息。暂停仅停止后续派发，不终止在途模型。</p>
        {dialog === 'authorize' && <p className="small">独立验收、修复次数和调用预算限制仍然生效；遇到失败或预算耗尽会停止。</p>}
        {error && <p className="error" role="alert">{error}</p>}
        <div className="actions"><button className="primary" disabled={locked || task.status !== 'ACTIVE'} onClick={() => act(async () => {
          if (dialog === 'authorize') await post(base + '/delegate-team', { authorize_execution: true });
          else await post(base + (dialog === 'execute' ? '/execute' : '/discuss'));
          setDialog('');
        })}>{dialog === 'authorize' ? '确认授权并执行' : '确认开始'}</button>
        <button onClick={() => setDialog('')}>取消</button></div>
      </Dialog>}
      {searching && <Dialog title="搜索当前项目任务" initialFocus=".search-field input" onClose={() => setSearching(false)}>
        <label className="search-field"><Search aria-hidden="true" /><input autoFocus aria-label="搜索任务标题" placeholder="输入任务名称…" value={searchText} onChange={e => setSearchText(e.target.value)} /></label>
        <p className="small">{currentProject?.name || '未选择项目'} · 仅搜索当前项目</p>
        <div className="search-results">{tasks.filter(t => t.title.toLocaleLowerCase().includes(searchText.trim().toLocaleLowerCase())).map(t =>
          <button key={t.id} disabled={busy} onClick={() => choose(t.id)}><MessageSquare /><span>{t.title}</span><small>{label(t.status)}</small></button>
        )}</div>
        {!tasks.some(t => t.title.toLocaleLowerCase().includes(searchText.trim().toLocaleLowerCase())) && <p className="empty">没有匹配的任务。</p>}
      </Dialog>}
      {managingAgents && <Dialog title="Agent 设置" onClose={() => setManagingAgents(false)}>
        {error && <p className="error" role="alert">{error}</p>}
            <p className="small muted">自定义 Agent 有两种接法：OpenAI 兼容接口只能讨论、复核方案和做只读分析；克隆本机 CLI 可继承对应的编码或验收能力。
              密钥优先读启动服务的环境变量，其次读本机 secrets.json（可选，权限 600，不写入注册数据库）。分工仍须经能力校验和你的方案批准。
              连接测试会发送一条不含任务内容的短消息，可能产生少量模型用量。</p>
            <p className="small">DeepSeek Harness 是本机 CLI 接入；本机 CLI 仍可能调用云端模型，并不表示离线运行。</p>
            {agents.some(a => a.id === 'deepseek-hermes') && <p className="small">DeepSeek API 是另一种云端接口配置，不是 Harness。旧 ID deepseek-hermes 仅为兼容历史消息。</p>}

            <div className="agent-card-list">
              {agents.map(a => (
                <div key={a.id} className="agent-card" style={{ '--agent-color': a.color || '#3072d2' } as React.CSSProperties}>
                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span className="avatar" style={{ borderColor: a.color || '#3072d2', color: a.color || '#3072d2' }}>{a.avatar || a.name.slice(0, 1).toUpperCase()}</span>
                      <strong>{a.name}</strong>
                      <span className="small muted">({a.id})</span>
                      <span className="badge" style={{ fontSize: 11 }}>{a.type === 'cli' ? '本机 CLI' : 'API 接口'}</span>
                    </div>
                    <p className="small" style={{ margin: '4px 0 0' }}>{a.description || '无描述'}</p>
                    {a.base_agent && <p className="small muted" style={{ margin: 0 }}>基于 {a.base_agent} 的本机 CLI 配置</p>}
                    {a.endpoint && <p className="small muted" style={{ margin: 0 }}>接口: {a.endpoint} ({a.model})</p>}
                    {a.system_prompt && <p className="small muted" style={{ margin: 0 }}>系统提示：{a.system_prompt.slice(0, 80)}{a.system_prompt.length > 80 ? '…' : ''}</p>}
                    {a.type === 'openai_compat' && <p className="small muted" style={{ margin: 0 }}>
                      {a.has_key ? (a.has_secret_file ? '已配置密钥（本机 secrets.json，可覆盖）' : '已从环境变量读取密钥') : '未配置密钥'}
                    </p>}
                    {missingExecutable(a, providers) && <p className="small agent-warning" role="status">
                      本机 PATH 未找到 {missingExecutable(a, providers)}：发送消息会立即失败。请先安装，或改用 <code>--codex-executable</code> / <code>--antigravity-executable</code> 指定路径后重启服务。
                    </p>}
                  </div>
                  <div className="agent-card-actions">
                    <button type="button" disabled={busy} onClick={() => act(async () => {
                      await post(`/agents/${a.id}/enabled`, { enabled: !a.enabled });
                      setAgents((await api.getAgents()).agents);
                    })}>{a.enabled ? '停用' : '启用（不会立即调用模型）'}</button>
                    <button type="button" disabled={busy} onClick={() => beginProfile(a)}>
                      <Pencil aria-hidden="true" />编辑档案
                    </button>
                    {!['codex', 'antigravity', 'deepseek-harness'].includes(a.id) && (
                      <button type="button" disabled={busy} onClick={() => removeAgent(a)}>
                        <Trash2 aria-hidden="true" />移除
                      </button>
                    )}
                    <span className={`badge ${a.ready ? 'active' : ''}`}>
                      {!a.ready ? '未配置／已禁用'
                        : missingExecutable(a, providers) ? '本机 CLI 未找到'
                        : a.type === 'openai_compat' ? '配置就绪 · 未验证连接' : '内置 Agent · 调用时验证'}
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
                  {profileDraft?.id === a.id && (
                    <form className="agent-profile-form" onSubmit={saveProfile}>
                      <p className="small muted">这里修改的是平台配置；系统提示会作为该 Agent 在对话中的角色设定。</p>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                        <label>显示名称<input value={profileDraft.name}
                          onChange={e => setProfileDraft({ ...profileDraft, name: e.target.value })} /></label>
                        {a.type === 'openai_compat' && <label>模型名称<input value={profileDraft.model}
                          onChange={e => setProfileDraft({ ...profileDraft, model: e.target.value })} /></label>}
                      </div>
                      <label>定位描述<input value={profileDraft.description}
                        onChange={e => setProfileDraft({ ...profileDraft, description: e.target.value })} /></label>
                      <label>系统提示 / 角色设定
                        <textarea rows={4} value={profileDraft.system_prompt}
                          placeholder="如：你负责验收，只报告可核查的证据；不确定时明确说不知道。"
                          onChange={e => setProfileDraft({ ...profileDraft, system_prompt: e.target.value })} />
                      </label>
                      {a.type === 'openai_compat' && (
                        <label>API Key（留空表示不修改）
                          <input type="password" autoComplete="off" value={profileDraft.secret_key}
                            placeholder={a.has_secret_file ? '已保存本机密钥' : '粘贴后保存到本机 secrets.json'}
                            onChange={e => setProfileDraft({ ...profileDraft, secret_key: e.target.value })} />
                        </label>
                      )}
                      <div className="actions">
                        <button className="primary" type="submit" disabled={savingProfile || !profileDraft.name.trim()}>
                          {savingProfile ? '保存中…' : '保存档案'}
                        </button>
                        {a.type === 'openai_compat' && a.has_secret_file && (
                          <button type="button" disabled={savingProfile}
                            onClick={() => clearStoredKey(a.id)}>清除本机密钥</button>
                        )}
                        <button type="button" onClick={() => setProfileDraft(null)}>取消</button>
                      </div>
                    </form>
                  )}
                </div>
              ))}
            </div>

            <details className="advanced-registration"><summary>接入其他 Agent（高级）</summary>
            <form onSubmit={handleAddAgent} className="stack" style={{ marginTop: 12 }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                <label>标识 ID (英文)<input required placeholder="如 qwen-coder" value={newAgent.id} onChange={e => setNewAgent({ ...newAgent, id: e.target.value.toLowerCase().replace(/\s+/g, '-') })} /></label>
                <label>显示名称<input required placeholder="如 Qwen 2.5 Coder" value={newAgent.name} onChange={e => setNewAgent({ ...newAgent, name: e.target.value })} /></label>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                <label>接入类型
                  <select value={newAgent.type} onChange={e => setNewAgent({ ...newAgent, type: e.target.value })}>
                    <option value="openai_compat">OpenAI 兼容接口 (DeepSeek/Ollama/vLLM)</option>
                    <option value="cli">本机 CLI 的独立配置（克隆 Codex / Antigravity / Harness）</option>
                  </select>
                </label>
                {newAgent.type === 'cli'
                  ? <label>基于哪个本机 CLI
                      <select value={newAgent.base_agent || 'codex'} onChange={e => setNewAgent({ ...newAgent, base_agent: e.target.value })}>
                        <option value="codex">Codex (codex)</option>
                        <option value="antigravity">Antigravity (agy)</option>
                        <option value="deepseek-harness">DeepSeek Harness (dsh)</option>
                      </select>
                    </label>
                  : <label>模型名称<input placeholder="如 deepseek-chat / qwen2.5-coder" value={newAgent.model} onChange={e => setNewAgent({ ...newAgent, model: e.target.value })} /></label>}
              </div>
              {newAgent.type === 'cli' && <label>默认模型（可选，留空使用 CLI 自己的配置）
                <input placeholder="一般留空" value={newAgent.model} onChange={e => setNewAgent({ ...newAgent, model: e.target.value })} />
              </label>}
              {newAgent.type === 'openai_compat' && (
                <>
                  <label>API Endpoint (基础地址)<input placeholder="如 https://api.deepseek.com/v1 或 http://localhost:11434/v1" value={newAgent.endpoint} onChange={e => setNewAgent({ ...newAgent, endpoint: e.target.value })} /></label>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                    <label>API Key 环境变量名<input placeholder="如 DEEPSEEK_API_KEY" value={newAgent.api_key_env} onChange={e => setNewAgent({ ...newAgent, api_key_env: e.target.value })} /></label>
                    <label>API Key（可选，保存到本机 secrets.json）<input type="password" autoComplete="off" placeholder="留空则只读环境变量" value={newAgent.secret_key} onChange={e => setNewAgent({ ...newAgent, secret_key: e.target.value })} /></label>
                  </div>
                  <p className="small muted">环境变量优先于本机保存的密钥；密钥不写入注册数据库，文件权限为 600。</p>
                </>
              )}
              <label>系统提示 / 角色设定（可选）
                <textarea rows={3} value={newAgent.system_prompt}
                  placeholder="如：你只做只读分析，输出可核查的证据清单。"
                  onChange={e => setNewAgent({ ...newAgent, system_prompt: e.target.value })} />
              </label>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                <label>图标 Emoji<input placeholder="如 🧠, 🚀, 🤖" value={newAgent.avatar} onChange={e => setNewAgent({ ...newAgent, avatar: e.target.value })} /></label>
                <label>主题颜色<input type="color" value={newAgent.color} onChange={e => setNewAgent({ ...newAgent, color: e.target.value })} /></label>
              </div>
              <label>定位描述<input placeholder="如：负责高难度算法推导与边界用例分析" value={newAgent.description} onChange={e => setNewAgent({ ...newAgent, description: e.target.value })} /></label>
              <div className="actions" style={{ marginTop: 8 }}>
                <button className="primary" type="submit" disabled={busy || !newAgent.id || !newAgent.name}>注册 Agent</button>
                <button type="button" onClick={() => setManagingAgents(false)}>取消</button>
              </div>
            </form></details>

      </Dialog>}
    </div>
  );
}
