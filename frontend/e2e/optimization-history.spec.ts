import { expect, test } from '@playwright/test'

test('长对话分页重试、发送和刷新保留最新消息及已加载历史', async ({ page }) => {
  const messages = Array.from({ length: 241 }, (_, i) => ({ id: String(i).padStart(36, '0'), role: 'user', content: `历史消息 ${i}`, status: 'completed', error: null, citations: [], created_at: '2026-10-29T00:00:00Z' }))
  let failOlder = true
  await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url()), path = url.pathname
    let data: unknown = []
    if (path.endsWith('/me')) data = { id: 'history-user', email: 'history@example.test' }
    else if (path.endsWith('/courses')) data = [{ id: 'course-0', name: '分页课程' }]
    else if (path.endsWith('/chat/sessions')) data = [{ id: 'session-0', course_id: 'course-0', document_ids: null, title: '长对话', created_at: '2026-10-29T00:00:00Z' }]
    else if (path.endsWith('/messages')) {
      if (route.request().method() === 'POST') {
        messages.push({ ...messages[0], id: 'z-user', content: '新问题' }, { ...messages[0], id: 'z-answer', content: '新回答' })
        await route.fulfill({ contentType: 'text/event-stream', body: 'event: answer_delta\ndata: {"text":"新回答"}\n\nevent: done\ndata: {}\n\n' })
        return
      }
      const end = Number(url.searchParams.get('before') ?? messages.length)
      if (url.searchParams.has('before') && failOlder) {
        failOlder = false
        await route.fulfill({ status: 503, json: { detail: '稍后重试历史加载' } }); return
      }
      const start = Math.max(0, end - 50)
      data = { items: messages.slice(start, end), next_cursor: start ? String(start) : null }
    }
    await route.fulfill({ json: data })
  })
  await page.goto('/')
  await expect(page.getByText('历史消息 240', { exact: true })).toBeVisible()
  const earlier = page.getByRole('button', { name: '加载更早消息' })
  await earlier.click()
  await expect(page.getByText('稍后重试历史加载')).toBeVisible()
  await earlier.click()
  await expect(page.getByText('历史消息 141', { exact: true })).toBeAttached()
  await page.getByLabel('输入问题').fill('新问题')
  await page.getByRole('button', { name: '发送', exact: true }).click()
  await expect(page.getByText('新回答', { exact: true })).toBeVisible()
  await expect(page.getByText('历史消息 141', { exact: true })).toHaveCount(1)
  await page.reload()
  await expect(page.getByText('新回答', { exact: true })).toBeVisible()
  await expect(page.getByText('历史消息 240', { exact: true })).toHaveCount(1)
})

test('撤销仓库授权后重新核对所有已加载历史页', async ({ page }) => {
  let revoked = false
  const citation = { kind: 'github', citation_id: 'source-1', label: 1, grant_id: 'grant-1', filename: 'demo README', repository: 'example/demo', sha: 'abc123', snippet: '自制仓库内容', url: 'https://github.com/example/demo' }
  const messages = Array.from({ length: 100 }, (_, i) => ({
    id: String(i).padStart(36, '0'), role: [10, 90].includes(i) ? 'assistant' : 'user',
    content: i === 10 ? '旧的私有仓库结论' : i === 90 ? '用于撤销的答复' : `普通历史 ${i}`,
    citations: [10, 90].includes(i) ? [citation] : [], status: 'completed', error: null,
    created_at: '2026-10-29T00:00:00Z',
  }))
  await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url()), path = url.pathname
    let data: unknown = []
    if (route.request().method() === 'DELETE') {
      revoked = true; await route.fulfill({ status: 204 }); return
    }
    if (path.endsWith('/me')) data = { id: 'grant-user', email: 'grant@example.test' }
    else if (path.endsWith('/courses')) data = [{ id: 'course-0', name: '授权课程' }]
    else if (path.endsWith('/chat/sessions')) data = [{ id: 'session-0', course_id: 'course-0', title: '授权历史', document_ids: null, created_at: '2026-10-29T00:00:00Z' }]
    else if (path.endsWith('/messages')) {
      const end = Number(url.searchParams.get('before') ?? messages.length), start = Math.max(0, end - 50)
      data = { items: messages.slice(start, end).map(m => revoked && m.role === 'assistant' ? { ...m, content: '来源授权已撤销，旧回答不可用', citations: [] } : m), next_cursor: start ? String(start) : null }
    }
    await route.fulfill({ json: data })
  })
  await page.goto('/')
  await expect(page.getByText('用于撤销的答复', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '加载更早消息' }).click()
  await expect(page.getByText('旧的私有仓库结论', { exact: true })).toBeAttached()
  await page.locator('.chat-message').filter({ hasText: '用于撤销的答复' }).locator('summary').click()
  await page.getByRole('button', { name: '撤销此仓库访问' }).click()
  await expect(page.getByText('旧的私有仓库结论', { exact: true })).toHaveCount(0)
  await expect(page.getByText('来源授权已撤销，旧回答不可用', { exact: true })).toHaveCount(2)
  await expect(page.getByText('普通历史 0', { exact: true })).toBeAttached()
})
