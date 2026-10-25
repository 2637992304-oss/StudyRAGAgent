import { expect, test, type Page } from '@playwright/test'
import type { ChatSession, ChatMessage, Document } from '../src/api'

const date = '2026-10-24T00:00:00Z'
test.beforeEach(async ({ page }) => {
  const sessions: ChatSession[] = [
    { id: 's1', course_id: 'c1', title: '事务练习', document_ids: null, created_at: date },
    { id: 's2', course_id: 'c1', title: '索引复习', document_ids: null, created_at: date },
    { id: 's3', course_id: 'c2', title: '矩阵计算', document_ids: null, created_at: date },
  ]
  const messages: Record<string, ChatMessage[]> = { s1: [{ id: 'm1', role: 'assistant', content: '事务具有原子性、一致性、隔离性和持久性。', status: 'completed', error: null, citations: [], created_at: date }] }
  const docs: Document[] = [{ id: 'd1', course_id: 'c1', filename: '数据库课件.pdf', document_type: '课件', size_bytes: 2048, status: 'ready', job_id: '', created_at: date }, { id: 'd2', course_id: 'c2', filename: '矩阵笔记.md', document_type: '笔记', size_bytes: 1024, status: 'ready', job_id: '', created_at: date }]
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname, method = request.method()
    let data: unknown = []
    if (path.endsWith('/me')) data = { id: 'workspace-user', email: 'workspace@example.test' }
    else if (path.endsWith('/courses')) data = [{ id: 'c1', name: '数据库学习' }, { id: 'c2', name: '线性代数' }]
    else if (path.endsWith('/chat/sessions')) {
      if (method === 'POST') {
        const body = request.postDataJSON()
        const session: ChatSession = { id: `new-${sessions.length}`, course_id: body.course_id, document_ids: body.document_ids, title: '新对话', title_renamed: false, created_at: date }
        sessions.unshift(session); data = session
      } else data = sessions.filter(item => item.course_id === url.searchParams.get('course_id'))
    } else if (/\/chat\/sessions\/[^/]+$/.test(path) && method === 'PATCH') {
      const session = sessions.find(item => path.endsWith(`/${item.id}`))!
      const changes = request.postDataJSON()
      Object.assign(session, changes, changes.title ? { title_renamed: true } : {})
      data = session
    } else if (path.endsWith('/messages')) {
      const sid = path.split('/').at(-2)!
      if (method === 'POST') {
        const body = request.postDataJSON(), session = sessions.find(item => item.id === sid)!
        if (!messages[sid]?.length && !session.title_renamed) session.title = body.content.trim().replace(/\s+/g, ' ').slice(0, 20) || '图片问答'
        messages[sid] = [...(messages[sid] ?? []), { id: `u-${Date.now()}`, role: 'user', content: body.content, status: 'completed', thinking: body.thinking, error: null, citations: [], created_at: date }, { id: `a-${Date.now()}`, role: 'assistant', content: '这是对问题的解答。', status: 'completed', thinking: body.thinking, error: null, citations: [], created_at: date }]
        await route.fulfill({ contentType: 'text/event-stream', body: 'event: answer_delta\ndata: {"text":"这是对问题的解答。"}\n\nevent: done\ndata: {}\n\n' }); return
      }
      data = messages[sid] ?? []
    } else if (/\/courses\/[^/]+\/documents$/.test(path)) {
      const cid = path.split('/').at(-2)!
      if (method === 'POST') {
        const doc: Document = { id: 'uploaded', course_id: cid, filename: 'revision.md', document_type: '笔记', size_bytes: 1024, status: 'ready', job_id: '', created_at: date }
        docs.push(doc); data = doc
      } else data = docs.filter(item => item.course_id === cid)
    }
    await route.fulfill({ json: data })
  })
})

async function open(page: Page) {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '事务练习', exact: true })).toBeVisible()
  await expect(page.getByLabel('输入问题')).toBeEnabled()
}
async function courseTool(page: Page, course: string, tool: string) {
  await page.getByRole('button', { name: course, exact: true }).hover()
  return page.getByRole('button', { name: tool, exact: true })
}

test('课程展开收起、对话归属、切换课程以及草稿和选择刷新恢复', async ({ page }) => {
  await open(page)
  const course = page.getByRole('button', { name: '数据库学习', exact: true })
  await course.click()
  await expect(page.getByRole('button', { name: '事务练习', exact: true })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: '事务练习', exact: true })).toBeVisible()
  await course.click()
  await page.getByLabel('输入问题').fill('这是尚未发送的草稿')
  await page.getByRole('button', { name: '索引复习', exact: true }).click()
  await expect(page.getByLabel('输入问题')).toHaveValue('')
  await page.getByRole('button', { name: '事务练习', exact: true }).click()
  await expect(page.getByLabel('输入问题')).toHaveValue('这是尚未发送的草稿')
  await page.getByRole('button', { name: '线性代数', exact: true }).click()
  await expect(page.getByRole('button', { name: '矩阵计算', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '矩阵计算', exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: '矩阵计算', exact: true })).toBeVisible()
})

test('右键重命名与更多菜单重命名，名称同步标题且刷新保留', async ({ page }) => {
  await open(page)
  await page.getByRole('button', { name: '事务练习', exact: true }).click({ button: 'right' })
  await page.getByRole('menuitem', { name: '重命名', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '重命名对话' })
  await dialog.getByLabel('对话名称').fill('期中复习计划')
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.getByRole('heading', { name: '期中复习计划', exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: '期中复习计划', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '期中复习计划', exact: true }).hover()
  await page.getByRole('button', { name: '期中复习计划的更多操作' }).click()
  await page.getByRole('menuitem', { name: '重命名' }).click()
  await expect(page.getByLabel('对话名称')).toHaveValue('期中复习计划')
  await page.keyboard.press('Escape')
  await page.getByLabel('输入问题').fill('继续解释事务')
  await page.getByRole('button', { name: '发送', exact: true }).click()
  await expect(page.getByLabel('输入问题')).toBeEnabled()
  await expect(page.getByRole('heading', { name: '期中复习计划', exact: true })).toBeVisible()
})

test('课程书本在加号左边，新建对话默认整门课程，首次发送自动命名', async ({ page }) => {
  await open(page)
  const plus = await courseTool(page, '数据库学习', '在数据库学习中新建对话')
  const book = page.getByRole('button', { name: '数据库学习的课程资料', exact: true })
  expect((await book.boundingBox())!.x).toBeLessThan((await plus.boundingBox())!.x)
  const createRequest = page.waitForRequest(request => request.method() === 'POST' && request.url().endsWith('/chat/sessions'))
  await plus.click()
  expect((await createRequest).postDataJSON()).toEqual({ course_id: 'c1', document_ids: null })
  await expect(page.getByRole('heading', { name: '新对话', exact: true })).toBeVisible()
  const question = page.getByLabel('输入问题')
  await expect(question).toBeFocused()
  const content = '数据库的事务隔离级别以及并发控制应该如何理解'
  await question.fill(content); await question.press('Enter')
  await expect(page.getByRole('heading', { name: content.slice(0, 20), exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: content.slice(0, 20), exact: true })).toBeVisible()
})

test('资料弹窗展示对应课程并直接加号上传，保留当前聊天和草稿', async ({ page }) => {
  await open(page)
  await page.getByLabel('输入问题').fill('打开资料也不要丢掉这段文字')
  await (await courseTool(page, '线性代数', '线性代数的课程资料')).click()
  const dialog = page.getByRole('dialog', { name: '课程资料', exact: true })
  await expect(dialog.getByText('矩阵笔记.md', { exact: true })).toBeVisible()
  await expect(dialog.getByText('数据库课件.pdf', { exact: true })).toHaveCount(0)
  await dialog.getByLabel('资料类型').selectOption('笔记')
  const chooser = page.waitForEvent('filechooser')
  await dialog.getByRole('button', { name: '上传课程资料' }).click()
  await (await chooser).setFiles({ name: 'revision.md', mimeType: 'text/markdown', buffer: Buffer.from('# revision') })
  await expect(dialog.getByText('revision.md', { exact: true })).toBeVisible()
  await page.screenshot({ path: 'test-results/workspace-library.png' })
  await dialog.getByRole('button', { name: '关闭课程资料' }).click()
  await expect(page.getByRole('heading', { name: '事务练习', exact: true })).toBeVisible()
  await expect(page.getByLabel('输入问题')).toHaveValue('打开资料也不要丢掉这段文字')
})

test('资料范围默认折叠，可选具体资料并保存，也能恢复整门课程', async ({ page }) => {
  await open(page)
  const details = page.locator('.composer-scope')
  await expect(details).not.toHaveAttribute('open', '')
  await details.locator('summary').click()
  await details.getByRole('checkbox', { name: '数据库课件.pdf', exact: true }).check()
  await expect(page.getByLabel('输入问题')).toBeEnabled()
  await expect(details.locator('summary')).toContainText('已选 1 份')
  await page.reload()
  await details.locator('summary').click()
  await expect(details.getByRole('checkbox', { name: '数据库课件.pdf', exact: true })).toBeChecked()
  await details.getByRole('checkbox', { name: '整门课程', exact: true }).check()
  await expect(details.locator('summary')).toContainText('整门课程')
})
