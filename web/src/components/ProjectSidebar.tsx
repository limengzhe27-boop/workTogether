import React, { useState } from 'react';
import { Project, TaskSummary, DoctorReport } from '../types';

interface ProjectSidebarProps {
  projects: Project[];
  selectedProject: Project | null;
  onSelectProject: (proj: Project) => void;
  onAddProject: (name: string, path: string) => Promise<void>;
  tasks: TaskSummary[];
  selectedTask: TaskSummary | null;
  onSelectTask: (task: TaskSummary) => void;
  onAddTask: (title: string, requirement: string) => Promise<void>;
  onRunDemo: () => Promise<void>;
  doctorReport: DoctorReport | null;
}

export const ProjectSidebar: React.FC<ProjectSidebarProps> = ({
  projects,
  selectedProject,
  onSelectProject,
  onAddProject,
  tasks,
  selectedTask,
  onSelectTask,
  onAddTask,
  onRunDemo,
  doctorReport,
}) => {
  const [showAddProject, setShowAddProject] = useState(false);
  const [showAddTask, setShowAddTask] = useState(false);
  const [showDoctor, setShowDoctor] = useState(false);

  // Form states
  const [projName, setProjName] = useState('');
  const [projPath, setProjPath] = useState('');
  const [taskTitle, setTaskTitle] = useState('');
  const [taskReq, setTaskReq] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handleAddProjectSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!projName.trim() || !projPath.trim()) return;
    setIsSubmitting(true);
    setErrorMsg(null);
    try {
      await onAddProject(projName.trim(), projPath.trim());
      setProjName('');
      setProjPath('');
      setShowAddProject(false);
    } catch (err: any) {
      setErrorMsg(err.message || '添加项目失败');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleAddTaskSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!taskTitle.trim() || !taskReq.trim()) return;
    setIsSubmitting(true);
    setErrorMsg(null);
    try {
      await onAddTask(taskTitle.trim(), taskReq.trim());
      setTaskTitle('');
      setTaskReq('');
      setShowAddTask(false);
    } catch (err: any) {
      setErrorMsg(err.message || '创建任务失败');
    } finally {
      setIsSubmitting(false);
    }
  };

  const getStatusBadge = (status: string, phase: string, blockReason: string | null) => {
    if (status === 'COMPLETED') return <span className="status-badge badge-completed">完成</span>;
    if (status === 'PAUSED') return <span className="status-badge badge-paused">已暂停</span>;
    if (status === 'BLOCKED') return <span className="status-badge badge-blocked">{blockReason || '阻塞'}</span>;
    return <span className="status-badge badge-active">{phase}</span>;
  };

  return (
    <aside className="sidebar">
      {/* Brand Header */}
      <div className="sidebar-header">
        <div className="logo-area">
          <span className="logo-badge">WT</span>
          <span className="logo-title">WorkTogether</span>
        </div>
        <button
          className="btn-icon-add"
          title="运行全流程模拟演示 (明确标记 SIMULATED)"
          onClick={onRunDemo}
        >
          模拟演练
        </button>
      </div>

      {/* Projects Section */}
      <div className="section-box">
        <div className="section-header">
          <span className="section-title">本地项目 ({projects.length})</span>
          <button className="btn-icon-add" onClick={() => setShowAddProject(true)}>
            + 添加
          </button>
        </div>

        {projects.length === 0 ? (
          <div style={{ color: 'var(--text-muted)', fontSize: '12px', padding: '6px 0' }}>
            暂无已注册项目，请点击上方添加
          </div>
        ) : (
          <select
            className="project-selector"
            value={selectedProject?.id || ''}
            onChange={(e) => {
              const p = projects.find((x) => x.id === e.target.value);
              if (p) onSelectProject(p);
            }}
          >
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} ({p.task_count || 0} 任务)
              </option>
            ))}
          </select>
        )}

        {selectedProject && (
          <div className="project-meta">
            <span
              title={selectedProject.path}
              style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '200px' }}
            >
              📂 {selectedProject.path}
            </span>
            <span
              style={{ cursor: 'pointer', color: 'var(--accent-blue)', textDecoration: 'underline' }}
              onClick={() => setShowDoctor(true)}
            >
              环境诊断
            </span>
          </div>
        )}
      </div>

      {/* Tasks Section */}
      <div className="section-box" style={{ borderBottom: 'none', paddingBottom: '4px' }}>
        <div className="section-header">
          <span className="section-title">协同任务 ({tasks.length})</span>
          <button
            className="btn-icon-add"
            disabled={!selectedProject}
            onClick={() => setShowAddTask(true)}
          >
            + 新建
          </button>
        </div>
      </div>

      {/* Task List */}
      <div className="task-list-container">
        {!selectedProject ? (
          <div style={{ color: 'var(--text-muted)', fontSize: '12px', textAlign: 'center', marginTop: '30px' }}>
            请先在上方选择或添加一个项目
          </div>
        ) : tasks.length === 0 ? (
          <div style={{ color: 'var(--text-muted)', fontSize: '12px', textAlign: 'center', marginTop: '30px' }}>
            该项目下暂无任务，请点击“+ 新建”
          </div>
        ) : (
          tasks.map((task) => {
            const isSelected = selectedTask?.id === task.id;
            return (
              <div
                key={task.id}
                className={`task-item ${isSelected ? 'active' : ''}`}
                onClick={() => onSelectTask(task)}
              >
                <div className="task-item-title">
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', marginRight: '6px' }}>
                    {task.title}
                  </span>
                  {getStatusBadge(task.status, task.phase, task.block_reason)}
                </div>
                <div className="task-item-meta">
                  <span style={{ color: 'var(--text-muted)' }}>
                    {task.steps_count} 步骤 / {task.dispatches_count} 派发
                  </span>
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Add Project Modal */}
      {showAddProject && (
        <div className="modal-overlay">
          <div className="modal-dialog">
            <div className="modal-header">
              <h3>添加本地已存在项目</h3>
              <button className="btn-secondary" onClick={() => setShowAddProject(false)}>✕</button>
            </div>
            <form onSubmit={handleAddProjectSubmit}>
              <div className="modal-body">
                {errorMsg && <div className="alert-banner alert-danger">{errorMsg}</div>}
                <div className="form-group">
                  <label className="form-label">项目名称</label>
                  <input
                    className="form-input"
                    placeholder="例如: 视频播放器前端"
                    value={projName}
                    onChange={(e) => setProjName(e.target.value)}
                    required
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">本地绝对路径 (必须真实存在)</label>
                  <input
                    className="form-input"
                    placeholder="/Users/username/Desktop/my-project"
                    value={projPath}
                    onChange={(e) => setProjPath(e.target.value)}
                    required
                  />
                </div>
              </div>
              <div className="modal-footer">
                <button type="button" className="btn btn-secondary" onClick={() => setShowAddProject(false)}>
                  取消
                </button>
                <button type="submit" className="btn btn-primary" disabled={isSubmitting}>
                  {isSubmitting ? '保存中...' : '确认添加'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Add Task Modal */}
      {showAddTask && (
        <div className="modal-overlay">
          <div className="modal-dialog">
            <div className="modal-header">
              <h3>创建协同开发任务</h3>
              <button className="btn-secondary" onClick={() => setShowAddTask(false)}>✕</button>
            </div>
            <form onSubmit={handleAddTaskSubmit}>
              <div className="modal-body">
                {errorMsg && <div className="alert-banner alert-danger">{errorMsg}</div>}
                <div className="form-group">
                  <label className="form-label">所属项目</label>
                  <input className="form-input" disabled value={selectedProject?.name || ''} />
                </div>
                <div className="form-group">
                  <label className="form-label">任务标题</label>
                  <input
                    className="form-input"
                    placeholder="例如: 优化进度持久化，支持刷新恢复"
                    value={taskTitle}
                    onChange={(e) => setTaskTitle(e.target.value)}
                    required
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">详细需求与约束 (Requirement)</label>
                  <textarea
                    className="form-textarea"
                    placeholder="输入该任务的业务目标、关键验收标准和技术约束..."
                    value={taskReq}
                    onChange={(e) => setTaskReq(e.target.value)}
                    required
                  />
                </div>
              </div>
              <div className="modal-footer">
                <button type="button" className="btn btn-secondary" onClick={() => setShowAddTask(false)}>
                  取消
                </button>
                <button type="submit" className="btn btn-primary" disabled={isSubmitting}>
                  {isSubmitting ? '创建中...' : '提交任务'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Doctor Modal */}
      {showDoctor && doctorReport && (
        <div className="modal-overlay">
          <div className="modal-dialog">
            <div className="modal-header">
              <h3>项目环境诊断 (Doctor)</h3>
              <button className="btn-secondary" onClick={() => setShowDoctor(false)}>✕</button>
            </div>
            <div className="modal-body">
              <div>
                <strong>Git 状态:</strong>
                <pre>{doctorReport.git.output || 'Git 正常或未修改'}</pre>
              </div>
              <div>
                <strong>HEAD 提交:</strong>
                <pre>{doctorReport.head.output || '无 HEAD 记录'}</pre>
              </div>
              <div>
                <strong>可用命令行:</strong>
                <pre>{JSON.stringify(doctorReport.executables, null, 2)}</pre>
              </div>
              <div style={{ color: 'var(--text-muted)', fontSize: '11px' }}>
                {doctorReport.note}
              </div>
            </div>
            <div className="modal-footer">
              <button className="btn btn-primary" onClick={() => setShowDoctor(false)}>关闭</button>
            </div>
          </div>
        </div>
      )}
    </aside>
  );
};
