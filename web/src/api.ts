/**
 * REST API client for WorkTogether backend.
 */
import {
  Project,
  DoctorReport,
  TaskSummary,
  TaskDetail,
  Dispatch,
  ReportEnvelope,
  RunRecord,
  HandoffBundle,
  AgentInfo,
  AgentList,
  AgentProbeResult,
  RunArtifactPreview,
  ProjectFiles,
  ProjectFileContent,
  ProjectDiff,
  ProjectMemory,
  RevertResult,
} from './types';

const BASE_URL = '/api';

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = {
    'Content-Type': 'application/json',
    ...(options.headers || {}),
  };

  const response = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers,
  });

  const data = await response.json();

  if (!response.ok) {
    const errorMsg = data?.error?.message || `HTTP ${response.status}: ${response.statusText}`;
    const err = new Error(errorMsg);
    (err as any).code = data?.error?.code || 'UNKNOWN_ERROR';
    (err as any).status = response.status;
    throw err;
  }

  return data;
}

export const api = {
  // Health
  getHealth: () => request<{ status: string; version: string; providers?: Record<string, { available: boolean; executable: string }> }>('/health'),

  // Agents
  getAgents: () => request<AgentList>('/agents'),
  addAgent: (agent: Partial<AgentInfo>) =>
    request<AgentInfo>('/agents', {
      method: 'POST',
      body: JSON.stringify(agent),
    }),
  updateAgentProfile: (agentId: string, body: Partial<AgentInfo> & { secret_key?: string }) =>
    request<AgentInfo>(`/agents/${encodeURIComponent(agentId)}/profile`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  deleteAgent: (agentId: string) =>
    request<{ deleted: boolean; removed: boolean; agent_id: string }>(`/agents/${encodeURIComponent(agentId)}/delete`, {
      method: 'POST',
      body: '{}',
    }),
  probeAgent: (agentId: string) =>
    request<AgentProbeResult>(`/agents/${encodeURIComponent(agentId)}/probe`, {
      method: 'POST',
      body: '{}',
    }),

  // Projects
  getProjects: () => request<{ projects: Project[] }>('/projects'),
  addProject: (name: string, path: string) =>
    request<Project>('/projects', {
      method: 'POST',
      body: JSON.stringify({ name, path }),
    }),
  deleteProject: (projectId: string) =>
    request<{ deleted: boolean; project_id: string; tasks_removed: number }>(`/projects/${encodeURIComponent(projectId)}/delete`, {
      method: 'POST',
      body: '{}',
    }),
  getProjectDoctor: (projectId: string) =>
    request<DoctorReport>(`/projects/${projectId}`),
  getProjectTasks: (projectId: string) =>
    request<{ tasks: TaskSummary[] }>(`/projects/${projectId}/tasks`),
  getProjectFiles: (projectId: string) =>
    request<ProjectFiles>(`/projects/${encodeURIComponent(projectId)}/files`),
  getProjectFile: (projectId: string, path: string) =>
    request<ProjectFileContent>(
      `/projects/${encodeURIComponent(projectId)}/file?path=${encodeURIComponent(path)}`),
  getProjectDiff: (projectId: string) =>
    request<ProjectDiff>(`/projects/${encodeURIComponent(projectId)}/diff`),
  revertProjectFile: (projectId: string, path: string) =>
    request<RevertResult>(`/projects/${encodeURIComponent(projectId)}/revert-file`, {
      method: 'POST',
      body: JSON.stringify({ path }),
    }),
  getProjectMemory: (projectId: string) =>
    request<ProjectMemory>(`/projects/${encodeURIComponent(projectId)}/memory`),
  updateProjectMemory: (projectId: string, memory: string, append = false) =>
    request<ProjectMemory>(`/projects/${encodeURIComponent(projectId)}/memory`, {
      method: 'POST',
      body: JSON.stringify({ memory, append }),
    }),

  // Tasks
  addTask: (projectId: string, title: string, requirement: string) =>
    request<TaskDetail>('/tasks', {
      method: 'POST',
      body: JSON.stringify({ project_id: projectId, title, requirement }),
    }),
  getTask: (taskId: string) => request<TaskDetail>(`/tasks/${taskId}`),
  renameTask: (taskId: string, title: string) =>
    request<TaskDetail>(`/tasks/${encodeURIComponent(taskId)}/rename`, {
      method: 'POST',
      body: JSON.stringify({ title }),
    }),
  deleteTask: (taskId: string) =>
    request<{ deleted: boolean; task_id: string }>(`/tasks/${encodeURIComponent(taskId)}/delete`, {
      method: 'POST',
      body: '{}',
    }),
  resolveMessage: (taskId: string, messageId: string, action: 'skip' | 'retry') =>
    request<Record<string, any>>(`/tasks/${encodeURIComponent(taskId)}/messages/${encodeURIComponent(messageId)}/resolve`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    }),
  dispatch: (taskId: string) =>
    request<{ dispatch: Dispatch | null; reason?: string }>(`/tasks/${taskId}/dispatch`, {
      method: 'POST',
    }),
  submitReport: (taskId: string, report: ReportEnvelope) =>
    request<{ accepted: boolean; duplicate?: boolean }>(`/tasks/${taskId}/submit`, {
      method: 'POST',
      body: JSON.stringify(report),
    }),
  pauseTask: (taskId: string) =>
    request<{ status: string }>(`/tasks/${taskId}/pause`, { method: 'POST' }),
  resumeTask: (taskId: string) =>
    request<{ status: string }>(`/tasks/${taskId}/resume`, { method: 'POST' }),
  retryDispatch: (taskId: string, dispatchId: string) =>
    request<{ dispatch: Dispatch }>(`/tasks/${taskId}/retry`, {
      method: 'POST',
      body: JSON.stringify({ dispatch_id: dispatchId }),
    }),
  expireDispatches: (taskId: string) =>
    request<{ expired: string[] }>(`/tasks/${taskId}/expire`, { method: 'POST' }),

  // Templates & Handoff
  getTemplate: (taskId: string, dispatchId: string) =>
    request<{ template: ReportEnvelope }>(`/tasks/${taskId}/template/${dispatchId}`),
  getHandoff: (taskId: string, dispatchId: string) =>
    request<HandoffBundle>(`/tasks/${taskId}/handoff/${dispatchId}`),

  // Runs
  getRuns: (taskId: string) => request<{ runs: RunRecord[] }>(`/tasks/${taskId}/runs`),
  triggerRun: (taskId: string, dispatchId: string, timeout = 300) =>
    request<{ status: string; message: string }>(`/tasks/${taskId}/run`, {
      method: 'POST',
      body: JSON.stringify({ dispatch_id: dispatchId, timeout }),
    }),
  getArtifact: (runId: string, filename: string) =>
    request<RunArtifactPreview>(
      `/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(filename)}`
    ),

  // Demo
  runDemo: (name?: string, path?: string) =>
    request<{ status: string; task: TaskDetail }>('/demo', {
      method: 'POST',
      body: JSON.stringify({ name, path }),
    }),
};
