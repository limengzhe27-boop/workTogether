import React, { useState } from 'react';
import { ChevronDown, ChevronRight, Folder, FolderOpen, Plus, Search, Settings2, SquarePen, X } from 'lucide-react';
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
      <button className="icon-button sidebar-search" onClick={onSearch} aria-label="搜索当前项目任务" title="搜索任务"><Search /></button>
      <button className="icon-button mobile-nav nav-close" onClick={onClose} aria-label="关闭导航"><X /></button>
    </div>
    <button className="new-task" disabled={!project || busy} onClick={onNew}><SquarePen /><span>新建任务</span></button>
    <p className="section-label small">项目</p>
    <nav aria-label="项目与任务列表">
      {projects.map(p => {
        const expanded = p.id === project && !folded.includes(p.id);
        return <div className="project-group" key={p.id}>
          <button className="project-link" disabled={busy} aria-expanded={expanded} title={p.path}
            data-current={p.id === project} onClick={() => {
              if (p.id !== project) { setFolded(ids => ids.filter(id => id !== p.id)); onProject(p.id); }
              else setFolded(ids => expanded ? [...ids, p.id] : ids.filter(id => id !== p.id));
            }}>
            {expanded ? <FolderOpen /> : <Folder />}<span>{p.name}</span>
            {expanded ? <ChevronDown className="project-chevron" /> : <ChevronRight className="project-chevron" />}
          </button>
          {expanded && <div className="project-tasks">
            {tasks.map(t => <button key={t.id} disabled={busy} className="task-link"
              aria-current={t.id === taskId ? 'page' : undefined}
              title={t.title + ' · ' + (labels[t.status] || t.status)} onClick={() => onTask(t.id)}>
              <span>{t.title}</span>
              {['BLOCKED', 'PAUSED'].includes(t.status) && <span className={'task-state ' + t.status.toLowerCase()} aria-label={labels[t.status]} />}
            </button>)}
            {!tasks.length && <p className="small empty-nav">还没有任务</p>}
          </div>}
        </div>;
      })}
      {!projects.length && <p className="small empty-nav">添加本地项目后开始协作。</p>}
      <button className="add-project" disabled={busy} onClick={onAdd}><Plus /><span>添加项目</span></button>
    </nav>
    <div className="sidebar-bottom"><span className="small">本地工作区 · {tasks.length} 个任务</span>
      <button className="icon-button mobile-settings" onClick={onSettings} aria-label="工作区 Agent 设置" title="Agent 设置"><Settings2 /></button>
    </div>
  </>;
}
