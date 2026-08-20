import { useEffect, useRef, useState } from "react";

import type { DeliveryGuide } from "../api/types";

interface DeliveryGuideModalProps {
  guide: DeliveryGuide;
  onClose: () => void;
}

export function DeliveryGuideModal({ guide, onClose }: DeliveryGuideModalProps) {
  const [copyFeedback, setCopyFeedback] = useState<{
    command: string;
    type: "success" | "error";
  } | null>(null);
  const feedbackTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (feedbackTimer.current) clearTimeout(feedbackTimer.current);
  }, []);

  async function copyCommand(command: string) {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(command);
      } else {
        const input = document.createElement("textarea");
        input.value = command;
        input.style.position = "fixed";
        input.style.opacity = "0";
        document.body.appendChild(input);
        input.select();
        const copied = document.execCommand("copy");
        input.remove();
        if (!copied) throw new Error("copy command was rejected");
      }
      setCopyFeedback({ command, type: "success" });
    } catch {
      setCopyFeedback({ command, type: "error" });
    }
    if (feedbackTimer.current) clearTimeout(feedbackTimer.current);
    feedbackTimer.current = setTimeout(() => setCopyFeedback(null), 2200);
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="delivery-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="delivery-modal-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        {copyFeedback && (
          <div className={`copy-toast ${copyFeedback.type}`} role="status" aria-live="polite">
            <span aria-hidden="true">{copyFeedback.type === "success" ? "✓" : "!"}</span>
            {copyFeedback.type === "success" ? "启动命令已复制" : "复制失败，请手动选择命令"}
          </div>
        )}
        <header className="delivery-modal-header">
          <div>
            <span className="eyebrow">项目交付说明</span>
            <h2 id="delivery-modal-title">{guide.project_name}</h2>
            <p>{guide.project_type} · {guide.summary}</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭交付说明">×</button>
        </header>

        <div className="delivery-modal-body">
          <section className="delivery-overview-card">
            <div>
              <span>项目位置</span>
              <code>{guide.root_path}</code>
            </div>
            <div>
              <span>入口文件</span>
              <code>{guide.entry_point ?? "未识别"}</code>
            </div>
          </section>

          <section className="delivery-section">
            <div className="delivery-section-heading">
              <span>01</span>
              <div><h3>启动方式</h3><p>请在项目根目录按顺序执行以下命令。</p></div>
            </div>
            <div className="delivery-command-list">
              {guide.start_commands.length ? guide.start_commands.map((item) => (
                <article className="command-card" key={item.command}>
                  <div><strong>{item.label}</strong><span>{item.description}</span></div>
                  <code>{item.command}</code>
                  <button
                    type="button"
                    className={copyFeedback?.command === item.command ? copyFeedback.type : ""}
                    onClick={() => void copyCommand(item.command)}
                  >
                    {copyFeedback?.command === item.command
                      ? copyFeedback.type === "success" ? "✓ 已复制" : "复制失败"
                      : "复制命令"}
                  </button>
                </article>
              )) : <p className="empty-copy">未识别到自动启动命令，请查看入口文件和项目说明。</p>}
            </div>
          </section>

          <section className="delivery-section">
            <div className="delivery-section-heading">
              <span>02</span>
              <div><h3>操作流程</h3><p>启动项目后，按照以下步骤体验主要功能。</p></div>
            </div>
            <ol className="operation-list">
              {guide.operation_steps.map((step) => <li key={step}>{step}</li>)}
            </ol>
          </section>

          <section className="delivery-section">
            <div className="delivery-section-heading">
              <span>03</span>
              <div><h3>项目结构</h3><p>以下是本次交付的主要文件及其职责。</p></div>
            </div>
            <div className="structure-list">
              {guide.structure.map((file) => (
                <div key={file.path}>
                  <code>{file.path}</code>
                  <span><strong>{file.kind}</strong> · {file.description}</span>
                </div>
              ))}
            </div>
          </section>

          {guide.notes.length > 0 && (
            <section className="delivery-section delivery-notes">
              <div className="delivery-section-heading">
                <span>04</span>
                <div><h3>补充说明</h3><p>运行项目时需要留意的信息。</p></div>
              </div>
              <ul>{guide.notes.map((note) => <li key={note}>{note}</li>)}</ul>
            </section>
          )}
        </div>
      </section>
    </div>
  );
}
