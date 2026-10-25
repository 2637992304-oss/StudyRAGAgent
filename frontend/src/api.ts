const baseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'

export type Course = { id: string; name: string }
export type Document = { id: string; course_id: string; filename: string; document_type: string; size_bytes: number; status: string; job_id: string; created_at: string }
export type Job = { id: string; status: string; stage: string; progress: number; error: string | null }
export type User = { id: string; email: string }
export type ChatSession = { id: string; course_id: string; document_ids: string[] | null; created_at: string; title?: string; title_renamed?: boolean }
export type Citation = { kind?: 'course' | 'web' | 'github'; grant_id?: string; url?: string; accessed_at?: string; repository?: string; sha?: string; path?: string; line_start?: number; line_end?: number; truncated?: boolean; citation_id: string; label: number; chunk_id: string; document_id: string; filename: string; document_type: string; section_path: string | null; page_start: number | null; page_end: number | null; slide_no: number | null; snippet: string; file_url: string }
export type Attachment = { id: string; filename: string; mime_type: string; size_bytes: number; width: number; height: number; message_id: string | null; saved_document_id: string | null; file_url: string }
export type ChatMessage = { id: string; role: 'user' | 'assistant'; content: string; status: string; error: string | null; created_at: string; citations: Citation[]; attachments?: Attachment[]; stop_reason?: string; thinking?: boolean | null; tool_policy?: { web_enabled: boolean; grant_ids: string[] } | null }
export type ChatEvent = { event: string; data: Record<string, unknown> }
export type ToolPolicy = { web_enabled: boolean; web_queries: string[]; repository_grant_ids: string[] }
export type RepositoryGrant = { id: string; repository: string; ref: string; private: boolean; active: boolean }
export type ToolCapabilities = { web_search: boolean; web_reason: string; search_provider: string; github: boolean; github_private_repositories: string[] }
export const toolCapabilities = () => request<ToolCapabilities>('/tools/capabilities')
export const repositoryGrants = (sid: string) => request<RepositoryGrant[]>(`/chat/sessions/${sid}/repositories`)
export const authorizeRepository = (sid: string, repository: string, ref: string) => request<RepositoryGrant>(`/chat/sessions/${sid}/repositories`, { method: 'POST', body: JSON.stringify({ repository, ref }) })
export const revokeRepository = (sid: string, id: string) => request<void>(`/chat/sessions/${sid}/repositories/${id}`, { method: 'DELETE' })

let token = localStorage.getItem('studyrag-token') ?? ''

export function setToken(value: string) {
  token = value
  if (value) localStorage.setItem('studyrag-token', value)
  else localStorage.removeItem('studyrag-token')
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  const response = await fetch(`${baseUrl}/api/v1${path}`, { ...init, headers })
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string | { loc?: (string | number)[]; msg?: string }[] } | null
    if (typeof payload?.detail === 'string') throw new Error(payload.detail)
    if (Array.isArray(payload?.detail)) {
      const messages = payload.detail.map(item => {
        const field = item.loc?.[1]
        if (field === 'email') return '请输入有效的邮箱地址（最多 255 个字符）'
        if (field === 'password') return '密码需为 15–128 个字符，且不能全部为空格'
        return item.msg ?? '请检查填写内容'
      })
      throw new Error([...new Set(messages)].join('；'))
    }
    throw new Error(`请求失败：HTTP ${response.status}`)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export async function login(email: string, password: string): Promise<void> {
  const result = await request<{ access_token: string }>('/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) })
  setToken(result.access_token)
}

export async function register(email: string, password: string): Promise<void> {
  const result = await request<{ access_token: string }>('/auth/register', { method: 'POST', body: JSON.stringify({ email, password }) })
  setToken(result.access_token)
}

export const me = () => request<User>('/me')
export const courses = () => request<Course[]>('/courses')
export const createCourse = (name: string) => request<Course>('/courses', { method: 'POST', body: JSON.stringify({ name }) })
export const documents = (courseId: string) => request<Document[]>(`/courses/${courseId}/documents`)
export const job = (jobId: string) => request<Job>(`/jobs/${jobId}`)
export const retry = (documentId: string) => request<void>(`/documents/${documentId}/retry`, { method: 'POST' })
export const remove = (documentId: string) => request<void>(`/documents/${documentId}`, { method: 'DELETE' })
export const chatSessions = (courseId: string) => request<ChatSession[]>(`/chat/sessions?course_id=${encodeURIComponent(courseId)}`)
export const createChatSession = (courseId: string, documentIds: string[] | null) => request<ChatSession>('/chat/sessions', { method: 'POST', body: JSON.stringify({ course_id: courseId, document_ids: documentIds }) })
export const chatMessages = (sessionId: string) => request<ChatMessage[]>(`/chat/sessions/${sessionId}/messages`)
export const updateChatSession = (sessionId: string, changes: { title?: string; document_ids?: string[] | null }) => request<ChatSession>(`/chat/sessions/${sessionId}`, { method: 'PATCH', body: JSON.stringify(changes) })

export const cancelChat = (sessionId: string) => request<void>(`/chat/sessions/${sessionId}/cancel`, { method: 'POST' })

export async function askChat(sessionId: string, content: string, onEvent: (item: ChatEvent) => void, signal?: AbortSignal, attachmentIds: string[] = [], thinking = false, tools?: ToolPolicy): Promise<void> {
  const response = await fetch(`${baseUrl}/api/v1/chat/sessions/${sessionId}/messages`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, attachment_ids: attachmentIds, thinking, ...tools }),
    signal,
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string } | null
    throw new Error(payload?.detail ?? `提问失败：HTTP ${response.status}`)
  }
  if (!response.body) throw new Error('浏览器不支持流式响应')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let doneSeen = false
  const dispatch = (block: string) => {
    const lines = block.split('\n')
    const event = lines.find(line => line.startsWith('event:'))?.slice(6).trim()
    const data = lines.filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n')
    if (!event || !data) return
    const parsed = JSON.parse(data) as Record<string, unknown>
    onEvent({ event, data: parsed })
    if (event === 'done') doneSeen = true
    if (event === 'error') throw new Error(String(parsed.message ?? '回答生成失败'))
  }
  try {
    while (true) {
      const { value, done } = await reader.read()
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true })
      buffer = buffer.replace(/\r\n/g, '\n')
      let boundary = buffer.indexOf('\n\n')
      while (boundary >= 0) {
        dispatch(buffer.slice(0, boundary))
        buffer = buffer.slice(boundary + 2)
        boundary = buffer.indexOf('\n\n')
      }
      if (done) break
    }
    if (buffer.trim()) dispatch(buffer)
    if (!doneSeen) throw new Error('回答流提前结束，请重试')
  } finally { reader.releaseLock() }
}

export async function openDocument(documentId: string, page: number | null): Promise<void> {
  const tab = window.open('about:blank', '_blank')
  if (!tab) throw new Error('浏览器阻止了新窗口，请允许弹窗后重试')
  try {
    const response = await fetch(`${baseUrl}/api/v1/documents/${documentId}/file`, { headers: { Authorization: `Bearer ${token}` } })
    if (!response.ok) throw new Error(`无法打开原文件：HTTP ${response.status}`)
    const url = URL.createObjectURL(await response.blob())
    tab.location.href = url + (page ? `#page=${page}` : '')
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
  } catch (error) { tab.close(); throw error }
}

export function upload(courseId: string, file: File, documentType: string, onProgress: (value: number) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const body = new FormData()
    body.append('file', file)
    body.append('document_type', documentType)
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${baseUrl}/api/v1/courses/${courseId}/documents`)
    xhr.setRequestHeader('Authorization', `Bearer ${token}`)
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round(event.loaded / event.total * 100))
    }
    xhr.onerror = () => reject(new Error('上传连接失败'))
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve()
      else {
        try { reject(new Error(JSON.parse(xhr.responseText).detail)) }
        catch { reject(new Error(`上传失败：HTTP ${xhr.status}`)) }
      }
    }
    xhr.send(body)
  })
}


export const listAttachments = (sessionId: string) => request<Attachment[]>(`/chat/sessions/${sessionId}/attachments`)
export const deleteAttachment = (id: string) => request<void>(`/chat/attachments/${id}`, { method: 'DELETE' })
export const saveAttachment = (id: string) => request<{document_id: string}>(`/chat/attachments/${id}/save`, { method: 'POST', body: JSON.stringify({ document_type: '参考资料' }) })
export const deleteChatSession = (id: string) => request<void>(`/chat/sessions/${id}`, { method: 'DELETE' })
export async function uploadAttachment(sessionId: string, file: File): Promise<Attachment> {
  const body = new FormData()
  body.append('file', file)
  return request<Attachment>(`/chat/sessions/${sessionId}/attachments`, { method: 'POST', body })
}
export async function attachmentBlob(id: string): Promise<string> {
  const response = await fetch(`${baseUrl}/api/v1/chat/attachments/${id}/file`, { headers: { Authorization: `Bearer ${token}` } })
  if (!response.ok) throw new Error('图片不可用或已移除')
  return URL.createObjectURL(await response.blob())
}

export async function documentImageBlob(documentId: string): Promise<string> {
  const response = await fetch(`${baseUrl}/api/v1/documents/${documentId}/file`, { headers: { Authorization: `Bearer ${token}` } })
  if (!response.ok) throw new Error('原图不可用或已移除')
  return URL.createObjectURL(await response.blob())
}
