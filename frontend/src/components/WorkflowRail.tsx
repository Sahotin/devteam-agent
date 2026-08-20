import type { TaskState } from "../api/types";
import { stageStatus, WORKFLOW_STAGES } from "../lib/workflow";

export function WorkflowRail({ state }: { state: TaskState }) {
  return (
    <div className="workflow-rail" aria-label="研发工作流进度">
      {WORKFLOW_STAGES.map((stage, index) => {
        const status = stageStatus(state, stage.state);
        return (
          <div className={`workflow-stage ${status}`} key={stage.state}>
            <div className="stage-track">
              <span className="stage-node">{status === "done" ? "✓" : index + 1}</span>
              {index < WORKFLOW_STAGES.length - 1 && <span className="stage-line" />}
            </div>
            <div className="stage-copy">
              <strong>{stage.label}</strong>
              <small>{stage.agent}</small>
            </div>
          </div>
        );
      })}
    </div>
  );
}
