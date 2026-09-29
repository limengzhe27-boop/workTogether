import React, { useState, useEffect, useCallback } from 'react';
import { Project, TaskSummary, TaskDetail, RunRecord, DoctorReport } from './types';
import { api } from './api';
import { ProjectSidebar } from './components/ProjectSidebar';
import { TaskHeader } from './components/TaskHeader';
import { ActionBar } from './components/ActionBar';
import { TimelineView } from './components/TimelineView';
import { StepsView } from './components/StepsView';
import { RunsView } from './components/RunsView';
import { ReportModal } from './components/ReportModal';
import { HandoffModal } from './components/HandoffModal';

export const App: React.FC = () => {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [selectedTaskSummary, setSelectedTaskSummary] = useState<TaskSummary | null>(null);
  const [taskDetail, setTaskDetail] = useState<TaskDetail | null>(null);
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [doctorReport, setDoctorReport] = useState<DoctorReport | null>(null);

  const [activeTab, setActiveTab] = useState<'timeline' | 'steps' | 'runs'>('timeline');
  const [reportModalDispatchId, setReportModalDispatchId] = useState<string | null>(null);
  const [handoffModalDispatchId, setHandoffModalDispatchId] = useState<string | null>(null);

  const [globalError, setGlobalError] = useState<string | null>(null);
  const [loadingTask, setLoadingTask] = useState<boolean>(false);

  // 1. Initial load of projects
  const fetchProjects = async () => {
    try {
      const res = await api.getProjects();
      setProjects(res.projects);
      if (res.projects.length > 0 && !selectedProject) {
        setSelectedProject(res.projects[0]);
      }
    } catch (err: any) {
      setGlobalError(err.message || '加载项目列表失败');
    }
  };

  useEffect(() => {
    fetchProjects();
  }, []);

  // 2. Load tasks whenever selected project changes
  const fetchTasks = useCallback(async (projId: string) => {
    try {
      const res = await api.getProjectTasks(projId);
      setTasks(res.tasks);
      if (res.tasks.length > 0) {
        // Auto-select first task if none selected or selected belongs to another project
        if (!selectedTaskSummary || selectedTaskSummary.project_id !== projId) {
          setSelectedTaskSummary(res.tasks[0]);
        }
      } else {
        setSelectedTaskSummary(null);
        setTaskDetail(null);
      }
    } catch (err: any) {
      setGlobalError(err.message || '加载任务列表失败');
    }
  }, [selectedTaskSummary]);

  useEffect(() => {
    if (selectedProject) {
      fetchTasks(selectedProject.id);
      // Also fetch doctor report for project
      api.getProjectDoctor(selectedProject.id)
        .then(setDoctorReport)
        .catch(() => setDoctorReport(null));
    } else {
      setTasks([]);
      setSelectedTaskSummary(null);
      setTaskDetail(null);
    }
  }, [selectedProject, fetchTasks]);

  // 3. Load task detail and runs
  const refreshCurrentTask = useCallback(async (quiet = false) => {
    if (!selectedTaskSummary) return;
    if (!quiet) setLoadingTask(true);
    try {
      const [detail, runsRes] = await Promise.all([
        api.getTask(selectedTaskSummary.id),
        api.getRuns(selectedTaskSummary.id),
      ]);
      setTaskDetail(detail);
      setRuns(runsRes.runs);
      setGlobalError(null);
    } catch (err: any) {
      if (!quiet) setGlobalError(err.message || '获取任务详情失败');
    } finally {
      if (!quiet) setLoadingTask(false);
    }
  }, [selectedTaskSummary]);

  useEffect(() => {
    refreshCurrentTask();
  }, [selectedTaskSummary, refreshCurrentTask]);

  // 4. Polling loop: 2.5s for active tasks
  useEffect(() => {
    if (!selectedTaskSummary || !taskDetail) return;
    const isOngoing = taskDetail.status === 'ACTIVE';
    const intervalTime = isOngoing ? 2500 : 8000;

    const timer = setInterval(() => {
      refreshCurrentTask(true);
      // also refresh project tasks list to keep status synced
      if (selectedProject) {
        api.getProjectTasks(selectedProject.id).then((r) => setTasks(r.tasks)).catch(() => {});
      }
    }, intervalTime);

    return () => clearInterval(timer);
  }, [selectedTaskSummary, taskDetail, selectedProject, refreshCurrentTask]);

  // Actions
  const handleAddProject = async (name: string, path: string) => {
    const proj = await api.addProject(name, path);
    await fetchProjects();
    setSelectedProject(proj);
  };

  const handleAddTask = async (title: string, requirement: string) => {
    if (!selectedProject) return;
    const newTask = await api.addTask(selectedProject.id, title, requirement);
    await fetchTasks(selectedProject.id);
    setSelectedTaskSummary(newTask);
    setTaskDetail(newTask);
  };

  const handleRunDemo = async () => {
    try {
      setLoadingTask(true);
      const res = await api.runDemo();
      await fetchProjects();
      setSelectedTaskSummary(res.task);
      setTaskDetail(res.task);
    } catch (err: any) {
      setGlobalError(err.message || '执行模拟演练失败');
    } finally {
      setLoadingTask(false);
    }
  };

  const handleDispatch = async () => {
    if (!taskDetail) return;
    try {
      await api.dispatch(taskDetail.id);
      await refreshCurrentTask(true);
    } catch (err: any) {
      setGlobalError(err.message || '派发失败');
    }
  };

  const handleRunCodex = async (dispatchId: string) => {
    if (!taskDetail) return;
    try {
      await api.triggerRun(taskDetail.id, dispatchId);
      await refreshCurrentTask(true);
    } catch (err: any) {
      setGlobalError(err.message || '启动 Codex 失败');
    }
  };

  const handlePause = async () => {
    if (!taskDetail) return;
    try {
      await api.pauseTask(taskDetail.id);
      await refreshCurrentTask(true);
    } catch (err: any) {
      setGlobalError(err.message || '暂停失败');
    }
  };

  const handleResume = async () => {
    if (!taskDetail) return;
    try {
      await api.resumeTask(taskDetail.id);
      await refreshCurrentTask(true);
    } catch (err: any) {
      setGlobalError(err.message || '恢复失败');
    }
  };

  const handleRetry = async (dispatchId: string) => {
    if (!taskDetail) return;
    try {
      await api.retryDispatch(taskDetail.id, dispatchId);
      await refreshCurrentTask(true);
    } catch (err: any) {
      setGlobalError(err.message || '重试失败');
    }
  };

  const handleExpire = async () => {
    if (!taskDetail) return;
    try {
      const res = await api.expireDispatches(taskDetail.id);
      if (res.expired && res.expired.length > 0) {
        alert(`已作废 ${res.expired.length} 个超时工单`);
      } else {
        alert('当前没有超过 Deadline 的在途工单');
      }
      await refreshCurrentTask(true);
    } catch (err: any) {
      setGlobalError(err.message || '检查超时失败');
    }
  };

  const activeDispatch = taskDetail?.dispatches.find((d) => d.status === 'ACTIVE') || null;
  const activeRun = runs.find((r) => r.status === 'RUNNING' || r.status === 'PREPARED') || null;

  return (
    <div className="app-container">
      {/* Left Sidebar */}
      <ProjectSidebar
        projects={projects}
        selectedProject={selectedProject}
        onSelectProject={(p) => {
          setSelectedProject(p);
          setSelectedTaskSummary(null);
          setTaskDetail(null);
        }}
        onAddProject={handleAddProject}
        tasks={tasks}
        selectedTask={selectedTaskSummary}
        onSelectTask={(t) => setSelectedTaskSummary(t)}
        onAddTask={handleAddTask}
        onRunDemo={handleRunDemo}
        doctorReport={doctorReport}
      />

      {/* Main Stage */}
      <main className="main-stage">
        {globalError && (
          <div className="alert-banner alert-danger" style={{ margin: '12px 24px 0 24px' }}>
            <span>{globalError}</span>
            <button className="btn-secondary" style={{ padding: '2px 6px' }} onClick={() => setGlobalError(null)}>✕</button>
          </div>
        )}

        {!taskDetail ? (
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)' }}>
            <div style={{ fontSize: '40px', marginBottom: '12px' }}>🤝</div>
            <h2 style={{ fontSize: '18px', color: 'var(--text-secondary)', marginBottom: '8px' }}>
              欢迎使用 WorkTogether 本地协同平台
            </h2>
            <p style={{ fontSize: '13px', maxWidth: '400px', textAlign: 'center' }}>
              请在左侧侧边栏选择项目与任务，或点击“+ 新建”开启一个新的协同研发流程。
            </p>
          </div>
        ) : (
          <>
            {/* Task Header */}
            <TaskHeader task={taskDetail} activeRun={activeRun} />

            {/* Action Bar */}
            <ActionBar
              task={taskDetail}
              activeDispatch={activeDispatch}
              activeRun={activeRun}
              onDispatch={handleDispatch}
              onRunCodex={handleRunCodex}
              onPause={handlePause}
              onResume={handleResume}
              onRetry={handleRetry}
              onExpire={handleExpire}
              onOpenHandoff={(dId) => setHandoffModalDispatchId(dId)}
              onOpenReportModal={(dId) => setReportModalDispatchId(dId)}
            />

            {/* Navigation Tabs */}
            <div className="tabs-header">
              <button
                className={`tab-btn ${activeTab === 'timeline' ? 'active' : ''}`}
                onClick={() => setActiveTab('timeline')}
              >
                协同流程与时间线 ({taskDetail.events.length})
              </button>
              <button
                className={`tab-btn ${activeTab === 'steps' ? 'active' : ''}`}
                onClick={() => setActiveTab('steps')}
              >
                阶段步骤与验收 ({taskDetail.steps.length})
              </button>
              <button
                className={`tab-btn ${activeTab === 'runs' ? 'active' : ''}`}
                onClick={() => setActiveTab('runs')}
              >
                Codex 运行与用量 ({runs.length})
              </button>
            </div>

            {/* Stage Body */}
            <div className="stage-body">
              {loadingTask ? (
                <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-muted)' }}>
                  正在同步最新状态...
                </div>
              ) : activeTab === 'timeline' ? (
                <TimelineView task={taskDetail} />
              ) : activeTab === 'steps' ? (
                <StepsView task={taskDetail} />
              ) : (
                <RunsView runs={runs} />
              )}
            </div>
          </>
        )}
      </main>

      {/* Report Modal */}
      {reportModalDispatchId && taskDetail && (
        <ReportModal
          taskId={taskDetail.id}
          dispatchId={reportModalDispatchId}
          onClose={() => setReportModalDispatchId(null)}
          onSubmitted={() => refreshCurrentTask(true)}
        />
      )}

      {/* Handoff Modal */}
      {handoffModalDispatchId && taskDetail && (
        <HandoffModal
          taskId={taskDetail.id}
          dispatchId={handoffModalDispatchId}
          onClose={() => setHandoffModalDispatchId(null)}
        />
      )}
    </div>
  );
};
