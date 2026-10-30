import { expect, test } from '@playwright/test'

test('首段渲染机会从浏览器请求开始计时，状态事件不算正文', async ({ page }) => {
  const messages: unknown[] = []
  await page.addInitScript(() => {
    const target = window as unknown as { observations: unknown[] }
    target.observations = []
    window.addEventListener('studyrag:answer-visible', event => target.observations.push((event as CustomEvent).detail))
  })
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname
    let data: unknown = []
    if (path.endsWith('/me')) data = { id: 'metrics-user', email: 'metrics@example.test' }
    else if (path.endsWith('/courses')) data = [{ id: 'metrics-course', name: '计时课程' }]
    else if (path.endsWith('/chat/sessions')) data = [{ id: 'metrics-session', course_id: 'metrics-course', document_ids: null, title: '计时', created_at: '2026-10-30T00:00:00Z' }]
    else if (path.endsWith('/messages')) {
      if (route.request().method() === 'POST') {
        await new Promise(resolve => setTimeout(resolve, 200))
        messages.push({ id: 'answer', role: 'assistant', content: '已经核验的正文', citations: [], status: 'completed', created_at: '2026-10-30T00:00:00Z' })
        await route.fulfill({ contentType: 'text/event-stream', body: 'event: tool_status\ndata: {"message":"正在核验"}\n\nevent: answer_delta\ndata: {"text":""}\n\nevent: answer_delta\ndata: {"text":"已经核验的正文"}\n\nevent: done\ndata: {}\n\n' })
        return
      }
      data = { items: messages, next_cursor: null }
    }
    await route.fulfill({ json: data })
  })
  await page.goto('/')
  await page.getByLabel('输入问题').fill('计时问题')
  await page.getByRole('button', { name: '发送', exact: true }).click()
  await expect(page.getByText('已经核验的正文', { exact: true })).toBeVisible()
  await expect.poll(() => page.evaluate(() => (window as unknown as { observations: unknown[] }).observations.length)).toBe(1)
  const observation = await page.evaluate(() => (window as unknown as { observations: { elapsed_ms: number; method: string }[] }).observations[0])
  expect(observation.elapsed_ms).toBeGreaterThanOrEqual(150)
  expect(observation.method).toBe('first-render-opportunity')
})
