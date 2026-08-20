import { FormEvent, useState } from "react";

import type { IterationKind, Task } from "../api/types";

interface IterationModalProps {
  task: Task;
  busy: boolean;
  onClose: () => void;
  onSubmit: (kind: IterationKind, request: string) => Promise<void>;
}

const iterationOptions: Array<{
  kind: IterationKind;
  title: string;
  description: string;
  placeholder: string;
}> = [
  {
    kind: "BUG_FIX",
    title: "反馈故障",
    description: "项目无法启动、页面异常或功能与预期不符",
    placeholder: "例如：按照启动说明执行后浏览器没有显示页面，请定位原因并修复，同时验证启动流程。",
  },
  {
    kind: "REQUIREMENT_CHANGE",
    title: "变更需求",
    description: "调整已有功能、规则、交互或交付目标",
    placeholder: "例如：增加暂停和继续功能，并支持保存最高分。",
  },
  {
    kind: "OPTIMIZATION",
    title: "优化项目",
    description: "改善界面、性能、代码质量或使用体验",
    placeholder: "例如：优化移动端操作体验，增加触屏方向控制，并改善游戏界面。",
  },
];

export function IterationModal({ task, busy, onClose, onSubmit }: IterationModalProps) {
  const [kind, setKind] = useState<IterationKind>("BUG_FIX");
  const [request, setRequest] = useState("");
  const selected = iterationOptions.find((item) => item.kind === kind)!;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await onSubmit(kind, request.trim());
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={busy ? undefined : onClose}>
      <form
        className="modal-card iteration-modal"
        onSubmit={(event) => void submit(event)}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="modal-heading">
          <div>
            <span className="eyebrow">继续迭代</span>
            <h2>告诉智能体接下来要修改什么</h2>
          </div>
          <button type="button" className="icon-button" disabled={busy} onClick={onClose} aria-label="关闭迭代窗口">×</button>
        </div>

        <div className="iteration-context">
          <span>基于已完成任务</span>
          <strong title={task.requirement}>{task.title}</strong>
          <p>新任务会继续使用当前项目目录，并携带上一轮需求、代码、产物和项目记忆。</p>
        </div>

        <fieldset className="iteration-kind-list">
          <legend>本轮迭代类型</legend>
          {iterationOptions.map((option) => (
            <label key={option.kind} className={kind === option.kind ? "selected" : ""}>
              <input
                type="radio"
                name="iteration-kind"
                value={option.kind}
                checked={kind === option.kind}
                onChange={() => setKind(option.kind)}
              />
              <span><strong>{option.title}</strong><small>{option.description}</small></span>
              <i aria-hidden="true" />
            </label>
          ))}
        </fieldset>

        <label className="iteration-request">
          详细说明
          <textarea
            value={request}
            onChange={(event) => setRequest(event.target.value)}
            placeholder={selected.placeholder}
            minLength={5}
            maxLength={8000}
            required
            autoFocus
          />
          <small>建议说明复现步骤、期望结果和当前实际结果，智能体会重新执行分析、开发、审查与测试。</small>
        </label>

        <div className="iteration-actions">
          <button type="button" className="ghost-button" disabled={busy} onClick={onClose}>暂不修改</button>
          <button type="submit" className="primary-button" disabled={busy || request.trim().length < 5}>
            {busy ? "正在创建迭代…" : "创建并启动迭代"}
          </button>
        </div>
      </form>
    </div>
  );
}
