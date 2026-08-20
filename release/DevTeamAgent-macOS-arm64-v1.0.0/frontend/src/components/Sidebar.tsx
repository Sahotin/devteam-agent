import type { Project, Task } from "../api/types";
import { stateLabel } from "../lib/workflow";

interface SidebarProps {
  projects: Project[];
  tasks: Task[];
  selectedProjectId: string | null;
  selectedTaskId: string | null;
  onSelectProject: (id: string) => void;
  onSelectTask: (id: string) => void;
  onCreateProject: () => void;
  onCreateTask: () => void;
  version: string;
}

export function Sidebar({
  projects,
  tasks,
  selectedProjectId,
  selectedTaskId,
  onSelectProject,
  onSelectTask,
  onCreateProject,
  onCreateTask,
  version,
}: SidebarProps) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>
        <div>
          <strong>智能研发团队</strong>
          <span>软件交付工作台</span>
        </div>
      </div>

      <section className="side-section">
        <div className="section-heading">
          <span>项目空间</span>
          <button className="icon-button" onClick={onCreateProject} aria-label="创建项目">
            ＋
          </button>
        </div>
        <label className="select-label">
          当前项目
          <select
            value={selectedProjectId ?? ""}
            onChange={(event) => onSelectProject(event.target.value)}
          >
            {projects.map((project) => (
              <option key={project.id} value={project.id}>{project.name}</option>
            ))}
          </select>
        </label>
      </section>

      <section className="side-section task-section">
        <div className="section-heading">
          <span>研发任务</span>
          <button
            className="icon-button"
            onClick={onCreateTask}
            disabled={!selectedProjectId}
            aria-label="创建任务"
          >
            ＋
          </button>
        </div>
        <div className="task-list">
          {tasks.length === 0 && <p className="empty-copy">还没有研发任务</p>}
          {tasks.map((task) => (
            <button
              key={task.id}
              className={`task-item ${selectedTaskId === task.id ? "selected" : ""}`}
              onClick={() => onSelectTask(task.id)}
            >
              <span className={`status-dot state-${task.state.toLowerCase()}`} />
              <span className="task-copy">
                <strong title={task.requirement}>{task.title}</strong>
                <small>{stateLabel(task.state)} · 版本 {task.state_version}</small>
              </span>
            </button>
          ))}
        </div>
      </section>

      <div className="sidebar-footer">
        <span className="pulse-dot" />
        <span>编排服务在线</span>
        <small>模型服务已连接 · 版本 {version}</small>
      </div>
    </aside>
  );
}
