/**
 * Type definitions matching WorkTogether v1 and runtime-v1 contracts.
 */

export type TaskStatus = 'ACTIVE' | 'PAUSED' | 'BLOCKED' | 'COMPLETED';
export type TaskPhase = 'PLANNING' | 'PLAN_REVIEW' | 'PLAN_FINALIZE' | 'EXECUTING' | 'COMPLETED';
export type StepStatus = 'PENDING' | 'CODING' | 'ANALYZING' | 'VERIFYING' | 'FIXING' | 'COMPLETED' | 'BLOCKED';
export type DispatchKind = 'PLAN' | 'PLAN_REVIEW' | 'PLAN_FINALIZE' | 'CODE' | 'ANALYZE' | 'VERIFY' | 'FIX' | 'CHAT' | 'CONSULT' | 'CLAIM';
export type DispatchStatus = 'ACTIVE' | 'DONE' | 'BLOCKED' | 'SUPERSEDED';
export type AgentRole = string;
export type RunStatus = 'PREPARED' | 'RUNNING' | 'EXITED' | 'COMPLETED' | 'FAILED';

export interface AgentInfo {
  id: string;
  name: string;
  type: string;
  base_agent?: string;
  executable?: string;
  endpoint?: string;
  model?: string;
  api_key_env?: string;
  roles: string[];
  color: string;
  avatar: string;
  description: string;
  system_prompt?: string;
  enabled: boolean;
  ready?: boolean;
  writable?: boolean;
  has_key?: boolean;
  has_stored_key?: boolean;
  secret_key?: string;
}

export interface AgentProbeResult {
  agent_id: string;
  connected: boolean;
  code: string;
  message: string;
  usage?: Record<string, number> | null;
}

export interface SecretStorageStatus {
  status: 'missing' | 'owner_only' | 'warning' | 'unknown';
  mode: string | null;
  warnings: { code: string; message: string }[];
}

export interface AgentList {
  agents: AgentInfo[];
  secret_storage?: SecretStorageStatus;
}

export interface Project {
  id: string;
  name: string;
  path: string;
  task_count?: number;
  memory?: string;
}

export interface ProjectFileEntry {
  path: string;
  size: number;
}

export interface ProjectFiles {
  project_id: string;
  files: ProjectFileEntry[];
  total: number;
  truncated: boolean;
}

export interface ProjectDiffFile {
  path: string;
  status: string;
}

export interface ProjectDiff {
  project_id: string;
  is_git: boolean;
  files: ProjectDiffFile[];
  diff: string;
  truncated: boolean;
  message?: string;
}

export interface ProjectMemory {
  project_id: string;
  memory: string;
}

export interface ProjectFileContent {
  project_id: string;
  path: string;
  content: string;
  size_bytes: number;
  truncated: boolean;
}

export interface RevertResult {
  reverted: boolean;
  path: string;
  mode: string;
}

export interface DoctorReport {
  project: Project;
  git: {
    exit_code: number | null;
    output: string;
  };
  head: {
    exit_code: number | null;
    output: string;
  };
  executables: Record<string, string | null>;
  note: string;
}

export interface PlanStep {
  id: string;
  title: string;
  work_kind?: 'code' | 'analysis';
  acceptance: string[];
  depends_on: string[];
  assignee?: string;
  verifier?: string;
  assignment_reason?: string;
  status?: StepStatus;
  repair_count?: number;
  repair_exhausted?: boolean;
  version?: string | null;
  handoff?: ReportEnvelope | null;
  verification?: ReportEnvelope | null;
}

export interface Dispatch {
  id: string;
  task_id: string;
  step_id: string | null;
  kind: DispatchKind;
  role: AgentRole;
  attempt: number;
  status: DispatchStatus;
  version: string | null;
  created_at: number;
  deadline: number;
  payload: Record<string, any>;
  report: ReportEnvelope | null;
}

export interface TaskEvent {
  seq: number;
  created_at: number;
  kind: string;
  data: Record<string, any>;
}

export interface TaskSummary {
  id: string;
  project_id: string;
  title: string;
  requirement: string;
  status: TaskStatus;
  phase: TaskPhase;
  block_reason: string | null;
  created_at: number;
  updated_at: number;
  steps_count: number;
  dispatches_count: number;
}

export interface TaskDetail extends TaskSummary {
  config: {
    max_repairs: number;
    timeout_seconds: number;
  };
  draft_plan: { steps: PlanStep[] } | null;
  plan_review: ReportEnvelope | null;
  steps: PlanStep[];
  dispatches: Dispatch[];
  events: TaskEvent[];
}

export interface CheckItem {
  criterion: string;
  status: 'PASS' | 'FAIL';
  evidence: string[];
}

export interface FindingItem {
  problem: string;
  reproduction: string[];
  expected: string;
  actual: string;
  evidence: string[];
}

export interface ReportEnvelope {
  schema_version: 1;
  task_id: string;
  dispatch_id: string;
  role: AgentRole;
  attempt: number;
  status: 'DONE' | 'PASS' | 'FAIL' | 'BLOCKED';
  summary: string;
  basis: string[];
  suggestions: string[];
  next_action: string;
  data: Record<string, any>;
}

export interface RunRecord {
  id: string;
  task_id: string;
  dispatch_id: string;
  provider: string;
  status: RunStatus;
  cwd: string;
  artifact_dir: string;
  session_id: string | null;
  pid: number | null;
  created_at: number;
  updated_at?: number;
  timeout: number;
  exit_code: number | null;
  error: string | null;
  error_code?: string;
  error_details?: { denied_commands?: string[]; retry_allowed?: boolean } | null;
  usage: {
    input_tokens?: number;
    cached_tokens?: number;
    output_tokens?: number;
    [key: string]: any;
  } | null;
}

export interface RunArtifactPreview {
  filename: string;
  run_id: string;
  content: string;
  size_bytes: number;
  truncated: boolean;
  view: 'head' | 'tail';
}

export interface HandoffBundle {
  task: {
    id: string;
    title: string;
    requirement: string;
    phase: TaskPhase;
  };
  dispatch: Dispatch;
  template: ReportEnvelope;
  instructions: string;
}
