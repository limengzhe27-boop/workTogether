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
  getAgents: () => request<{ agents: AgentInfo[] }>('/agents'),
  addAgent: (agent: Partial<AgentInfo>) =>
    request<AgentInfo>('/agents', {
      method: 'POST',
      body: JSON.stringify(agent),
    }),

  // Projects
  getProjects: () => request<{ projects: Project[] }>('/projects'),
  addProject: (name: string, path: string) =>
    request<Project>('/projects', {
      method: 'POST',
      body: JSON.stringify({ name, path }),
    }),
  getProjectDoctor: (projectId: string) =>
    request<DoctorReport>(`/projects/${projectId}`),
  getProjectTasks: (projectId: string) =>
    request<{ tasks: TaskSummary[] }>(`/projects/${projectId}/tasks`),

  // Tasks
  addTask: (projectId: string, title: string, requirement: string) =>
    request<TaskDetail>('/tasks', {
      method: 'POST',
      body: JSON.stringify({ project_id: projectId, title, requirement }),
    }),
  getTask: (taskId: string) => request<TaskDetail>(`/tasks/${taskId}`),
  dispatch: (taskId: string) =>
    request<{ dispatch: Dispatch | null }>(`/tasks/${taskId}/dispatch`, {
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
    request<{ filename: string; run_id: string; content: string }>(
      `/runs/${runId}/artifacts/${filename}`
    ),

  // Demo
  runDemo: (name?: string, path?: string) =>
    request<{ status: string; task: TaskDetail }>('/demo', {
      method: 'POST',
      body: JSON.stringify({ name, path }),
    }),
};
