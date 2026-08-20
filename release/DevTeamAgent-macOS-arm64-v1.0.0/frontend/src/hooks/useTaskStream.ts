import { useEffect, useRef, useState } from "react";

import { api } from "../api/client";
import type { TaskEvent } from "../api/types";

const EVENT_TYPES = [
  "task.created",
  "task.state_changed",
  "artifact.created",
  "tool.called",
  "tool.returned",
  "execution.queued",
  "execution.started",
  "execution.progress",
  "execution.finished",
  "execution.recovered",
  "execution.requeued",
];

export type StreamStatus = "idle" | "connecting" | "connected" | "reconnecting";

export function useTaskStream(
  taskId: string | null,
  onEvent: (event: TaskEvent) => void,
): StreamStatus {
  const [status, setStatus] = useState<StreamStatus>("idle");
  const callbackRef = useRef(onEvent);
  callbackRef.current = onEvent;

  useEffect(() => {
    if (!taskId) {
      setStatus("idle");
      return;
    }
    setStatus("connecting");
    const source = new EventSource(api.eventStreamUrl(taskId));
    const handleEvent = (raw: Event) => {
      const message = raw as MessageEvent<string>;
      callbackRef.current(JSON.parse(message.data) as TaskEvent);
    };
    for (const eventType of EVENT_TYPES) {
      source.addEventListener(eventType, handleEvent);
    }
    source.onopen = () => setStatus("connected");
    source.onerror = () => setStatus("reconnecting");
    return () => {
      for (const eventType of EVENT_TYPES) {
        source.removeEventListener(eventType, handleEvent);
      }
      source.close();
    };
  }, [taskId]);

  return status;
}
