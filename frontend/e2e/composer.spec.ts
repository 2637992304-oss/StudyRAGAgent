import { expect, test, type Page } from '@playwright/test'
import type { Attachment, ChatMessage } from '../src/api'

const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=', 'base64')
const imageFile = { name: 'question.png', mimeType: 'image/png', buffer: png }

test.beforeEach(async ({ page }) => {
  let attachments: Attachment[] = []
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname
    const method = route.request().method()
    let data: unknown = []
    if (path.endsWith('/me')) data = { id: 'composer-user', email: 'composer@example.test' }
    else if (path.endsWith('/courses')) data = [{ id: 'course-0', name: '数据库学习' }]
    else if (path.endsWith('/chat/sessions')) data = [{ id: 'session-0', course_id: 'course-0', document_ids: null, created_at: '2026-10-24T00:00:00Z' }]
    else if (path.endsWith('/attachments')) {
      if (method === 'POST') {
        const attachment: Attachment = { id: `image-${attachments.length}`, filename: 'question.png', mime_type: 'image/png', size_bytes: png.length, width: 1, height: 1, message_id: null, saved_document_id: null, file_url: '' }
        attachments.push(attachment)
        data = attachment
      } else data = attachments
    } else if (path.endsWith('/file')) {
      await route.fulfill({ contentType: 'image/png', body: png })
      return
    } else if (method === 'DELETE') {
      attachments = attachments.filter(item => !path.endsWith(`/${item.id}`))
      await route.fulfill({ status: 204 })
      return
    }
    await route.fulfill({ json: data })
  })
})

async function openComposer(page: Page) {
  await page.goto('/')
  await expect(page.getByLabel('输入问题')).toBeEnabled()
  const form = page.getByRole('form', { name: '聊天输入框' })
  await form.scrollIntoViewIfNeeded()
  return form
}

async function chooseImage(page: Page) {
  const chooser = page.waitForEvent('filechooser')
  await page.getByRole('button', { name: '添加图片', exact: true }).click()
  await (await chooser).setFiles(imageFile)
  await expect(page.locator('.composer-attachments img')).toBeVisible()
  await expect(page.getByLabel('输入问题')).toBeEnabled()
}

test('输入框内左下角加号、右下角思考与箭头排列，支持键盘切换', async ({ page }) => {
  const form = await openComposer(page)
  await expect(page.getByText('适合复杂推导、错题分析和多步问题，回答可能需要更长时间。')).toHaveCount(0)
  const attach = form.getByRole('button', { name: '添加图片', exact: true })
  const thinking = form.getByRole('button', { name: '深度思考', exact: true })
  const send = form.getByRole('button', { name: '发送', exact: true })
  await expect(send).toBeDisabled()
  await expect(send).toHaveText('')
  await expect(page.getByLabel('附上题目图片')).toBeHidden()
  const attachBox = (await attach.boundingBox())!
  const thinkingBox = (await thinking.boundingBox())!
  const sendBox = (await send.boundingBox())!
  expect(attachBox.x + attachBox.width).toBeLessThan(thinkingBox.x)
  expect(thinkingBox.x + thinkingBox.width).toBeLessThan(sendBox.x)
  expect(Math.abs(attachBox.y - sendBox.y)).toBeLessThan(2)
  await thinking.focus()
  await page.keyboard.press('Space')
  await expect(thinking).toHaveAttribute('aria-pressed', 'true')
  await page.getByLabel('输入问题').fill('请解释数据库的事务隔离级别。')
  await expect(send).toBeEnabled()
  await form.screenshot({ path: 'test-results/composer-desktop.png' })
})

test('加号打开图片选择，预览可移除，图文和思考设置随箭头发送', async ({ page }) => {
  await openComposer(page)
  await chooseImage(page)
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: '移除图片', exact: true }).click()
  await expect(page.locator('.composer-attachments')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeDisabled()
  await chooseImage(page)
  const question = page.getByLabel('输入问题')
  await question.fill('这张图片中的题目怎么解？')
  await page.getByRole('button', { name: '深度思考', exact: true }).click()
  let completeResponse!: () => void
  const responseGate = new Promise<void>(resolve => { completeResponse = resolve })
  let history: ChatMessage[] = []
  await page.route('**/api/v1/chat/sessions/session-0/messages', async route => {
    if (route.request().method() === 'GET') { await route.fulfill({ json: history }); return }
    await responseGate
    history = [{ id: 'answer-0', role: 'assistant', content: '这是图片题目的解答。', status: 'completed', error: null, created_at: '2026-10-24T00:00:00Z', citations: [], thinking: true }]
    await route.fulfill({ contentType: 'text/event-stream', body: 'event: answer_delta\ndata: {"text":"这是图片题目的解答。"}\n\nevent: done\ndata: {}\n\n' })
  })
  const sent = page.waitForRequest(request => request.method() === 'POST' && request.url().endsWith('/messages'))
  await page.getByRole('button', { name: '发送', exact: true }).click()
  expect((await sent).postDataJSON()).toMatchObject({ content: '这张图片中的题目怎么解？', thinking: true, attachment_ids: ['image-0'] })
  await expect(question).toBeDisabled()
  await expect(page.getByRole('button', { name: '添加图片', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: '深度思考', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: '停止', exact: true })).toBeVisible()
  // A successful send associates uploaded images with the message, clearing drafts.
  await page.route('**/api/v1/chat/sessions/session-0/attachments', route => route.fulfill({ json: [] }))
  completeResponse()
  await expect(question).toBeEnabled()
  await expect(page.locator('.chat-message.assistant')).toContainText('这是图片题目的解答。')
  await expect(page.getByRole('button', { name: '停止', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeDisabled()
})

test('生成时可用右下角停止按钮取消', async ({ page }) => {
  await openComposer(page)
  let completeResponse!: () => void
  const responseGate = new Promise<void>(resolve => { completeResponse = resolve })
  await page.route('**/api/v1/chat/sessions/session-0/messages', async route => {
    if (route.request().method() === 'GET') { await route.fulfill({ json: [] }); return }
    await responseGate
    await route.abort().catch(() => undefined)
  })
  await page.getByLabel('输入问题').fill('详细解释事务隔离。')
  await page.getByRole('button', { name: '发送', exact: true }).click()
  const cancelled = page.waitForRequest(request => request.url().endsWith('/cancel') && request.method() === 'POST')
  await page.getByRole('button', { name: '停止', exact: true }).click()
  await cancelled
  await expect(page.getByLabel('输入问题')).toBeEnabled()
  await expect(page.getByRole('button', { name: '添加图片', exact: true })).toBeEnabled()
  await expect(page.getByRole('button', { name: '停止', exact: true })).toHaveCount(0)
  completeResponse()
})

test('窄屏下图片与工具栏保持在输入框内，长文本自动增高', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 844 })
  const form = await openComposer(page)
  await chooseImage(page)
  const question = page.getByLabel('输入问题')
  const originalHeight = (await question.boundingBox())!.height
  await question.fill('请结合图片里的例子详细解释数据库事务。\n'.repeat(10))
  expect((await question.boundingBox())!.height).toBeGreaterThan(originalHeight)
  const box = (await form.boundingBox())!
  for (const name of ['添加图片', '深度思考', '发送']) {
    const button = (await form.getByRole('button', { name, exact: true }).boundingBox())!
    expect(button.x).toBeGreaterThanOrEqual(box.x)
    expect(button.x + button.width).toBeLessThanOrEqual(box.x + box.width)
  }
  expect(await form.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true)
  await form.screenshot({ path: 'test-results/composer-mobile.png' })
})
