import { useState } from "react";

export function FileFingerprint({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  const shortValue = `${value.slice(0, 10)}…${value.slice(-10)}`;

  const copy = async () => {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };

  return (
    <div className="file-fingerprint">
      <code title={value}>{shortValue}</code>
      <span>用于确认文件内容是否变化，不是代码内容</span>
      <button type="button" onClick={copy}>{copied ? "已复制" : "复制完整指纹"}</button>
    </div>
  );
}
