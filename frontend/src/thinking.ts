// Protocol state stays on the server; only a preference is kept in this browser.
export function readThinking(accountId: string): boolean {
  try { return localStorage.getItem(`studyrag-thinking:${accountId}`) === 'true' }
  catch { return false }
}
export function rememberThinking(accountId: string, value: boolean): void {
  try { localStorage.setItem(`studyrag-thinking:${accountId}`, String(value)) }
  catch { /* A disabled local store must not prevent sending. */ }
}
export function thinkingLabel(value: boolean | null | undefined): string {
  return value == null ? '思考模式未记录（旧版本）' : value ? '深度思考已开启' : '深度思考已关闭'
}
