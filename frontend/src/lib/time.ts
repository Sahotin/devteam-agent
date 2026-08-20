/**
 * 后端数据库统一保存 UTC 时间。SQLite 序列化后可能不带时区后缀，
 * 因此无后缀的 API 时间必须按 UTC 解析，不能交给浏览器按本地时间猜测。
 */
export function parseApiDate(value: string): Date {
  const normalized = /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`;
  return new Date(normalized);
}

export function elapsedSecondsSince(value: string, now: number): number {
  const startedAt = parseApiDate(value).getTime();
  if (!Number.isFinite(startedAt)) return 0;
  return Math.max(0, Math.floor((now - startedAt) / 1000));
}
