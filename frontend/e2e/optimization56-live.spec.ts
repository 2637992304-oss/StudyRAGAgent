import { randomUUID } from 'node:crypto'
import { expect, test } from '@playwright/test'

test('独立真实服务：上传入库、预算收尾、SSE、来源和刷新', async ({ page, request }) => {
  test.skip(process.env.OPT56_LIVE !== '1', '仅由隔离优化验收入口开启')
  const api = `${process.env.OPT56_API_URL}/api/v1`
  expect((await (await request.get(`${api}/validation-identity`)).json()).run_id).toBe(process.env.OPT34_RUN_ID)
  const registered = await request.post(`${api}/auth/register`, { data: { email: `opt56-browser-${randomUUID()}@example.test`, password: 'self authored browser acceptance password' } })
  expect(registered.status()).toBe(201)
  const token = (await registered.json()).access_token as string
  const headers = { Authorization: `Bearer ${token}` }
  const course = await (await request.post(`${api}/courses`, { headers, data: { name: '实际服务验收课程' } })).json()
  const uploaded = await request.post(`${api}/courses/${course.id}/documents`, { headers, multipart: { document_type: '笔记', file: { name: 'browser-self-authored.txt', mimeType: 'text/plain', buffer: Buffer.from('自制验收规则：有效期17秒，校验失败时不得复用。') } } })
  expect(uploaded.status()).toBe(202)
  const document = await uploaded.json()
  await expect.poll(async () => {
    const documents = await (await request.get(`${api}/courses/${course.id}/documents`, { headers })).json()
    return documents.find((item: { id: string }) => item.id === document.document_id)?.status
  }, { timeout: 60_000 }).toBe('ready')
  await page.addInitScript(value => {
    localStorage.setItem('studyrag-token', value)
    const observations: unknown[] = []
    ;(window as unknown as { observations: unknown[] }).observations = observations
    window.addEventListener('studyrag:answer-visible', event => observations.push((event as CustomEvent).detail))
  }, token)
  await page.goto('/')
  await expect(page.getByLabel('输入问题')).toBeEnabled()
  await page.getByLabel('输入问题').fill('严格根据资料说明自制验收规则')
  await page.getByRole('button', { name: '发送', exact: true }).click()
  await expect(page.locator('.chat-message.assistant')).toContainText('不得复用')
  await expect.poll(() => page.evaluate(() => (window as unknown as { observations: unknown[] }).observations.length)).toBe(1)
  await page.locator('.source-disclosure summary').click()
  await expect(page.locator('.sources')).toContainText('browser-self-authored.txt')
  await page.reload()
  await expect(page.locator('.chat-message.assistant')).toContainText('不得复用')
})
