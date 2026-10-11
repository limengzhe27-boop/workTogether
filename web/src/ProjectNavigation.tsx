import React, { useState } from 'react';
import { ChevronDown, ChevronRight, Folder, FolderOpen, MessageSquare, Plus, Search, Settings2, SquarePen, X } from 'lucide-react';
import { Project, TaskSummary } from './types';

export function ProjectNavigation({ projects, project, tasks, taskId, busy, onProject, onTask, onNew, onAdd, onSearch, onClose, onSettings }: {
  projects: Project[]; project: string; tasks: TaskSummary[]; taskId: string; busy: boolean;
  onProject: (id: string) => void; onTask: (id: string) => void; onNew: () => void;
  onAdd: () => void; onSearch: () => void; onClose: () => void; onSettings: () => void;
}) {
  const [folded, setFolded] = useState<string[]>([]);
  const labels: Record<string, string> = { ACTIVE: '待推进', BLOCKED: '需要处理', PAUSED: '已暂停', COMPLETED: '已完成' };
  return <>
    <div className="sidebar-heading">
      <span className="brand">WorkTogether</span>
      <button className="icon-button sidebar-search" onClick={onSearch} aria-label="搜索当前项目任务" title="搜索当前项目会话"><Search /></button>
      <button className="icon-button mobile-nav nav-close" onClick={onClose} aria-label="关闭导航"><X /></button>
    </div>
    <div className="sidebar-section-bar">
      <span className="section-label small">项目</span>
      <button className="icon-button section-add-project" disabled={busy} onClick={onAdd} aria-label="添加项目" title="新增项目">
        <Plus />
      </button>
    </div>
    <nav aria-label="项目与任务列表">
      {projects.map(p => {
        const expanded = p.id === project && !folded.includes(p.id);
        return <div className="project-group" key={p.id}>
          <div className="project-row">
            <button className="project-link" disabled={busy} aria-expanded={expanded} title={`本地目录：${p.path}`}
              data-current={p.id === project} onClick={() => {
                if (p.id !== project) { setFolded(ids => ids.filter(id => id !== p.id)); onProject(p.id); }
                else setFolded(ids => expanded ? [...ids, p.id] : ids.filter(id => id !== p.id));
              }}>
              {expanded ? <ChevronDown className="project-chevron" /> : <ChevronRight className="project-chevron" />}
              {expanded ? <FolderOpen /> : <Folder />}
              <span>{p.name}</span>
            </button>
            <button className="icon-button project-new-btn" disabled={busy}
              aria-label={`在 ${p.name} 下新建会话`} title={`在 ${p.name} 下新建会话`}
              onClick={() => {
                setFolded(ids => ids.filter(id => id !== p.id));
                if (p.id !== project) onProject(p.id);
                onNew();
              }}>
              <SquarePen />
            </button>
          </div>
          {expanded && <div className="project-tasks">
            {!taskId && <div className="task-link draft-session" aria-current="page">
              <MessageSquare className="task-icon" />
              <span>新会话（待发送…）</span>
            </div>}
            {tasks.map(t => <button key={t.id} disabled={busy} className="task-link"
              aria-current={t.id === taskId ? 'page' : undefined}
              title={t.title + ' · ' + (labels[t.status] || t.status)} onClick={() => onTask(t.id)}>
              <MessageSquare className="task-icon" />
              <span>{t.title}</span>
              {['BLOCKED', 'PAUSED'].includes(t.status) && <span className={'task-state ' + t.status.toLowerCase()} aria-label={labels[t.status]} />}
            </button>)}
            {!tasks.length && taskId && <p className="small empty-nav">暂无历史会话</p>}
          </div>}
        </div>;
      })}
      {!projects.length && <p className="small empty-nav">点击右上角 + 添加本地项目后开始协作。</p>}
    </nav>
    <div className="sidebar-bottom">
      <button className="sidebar-settings-btn" onClick={onSettings} aria-label="工作区 Agent 设置" title="配置与管理 Agent">
        <Settings2 /><span>Agent 设置</span>
      </button>
    </div>
  </>;
}
