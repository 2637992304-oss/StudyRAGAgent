import { expect, test, type Page } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url())
    let data: unknown = []
    if (url.pathname.endsWith('/me')) data = { id: 'layout-user', email: 'layout@example.test' }
    else if (url.pathname.endsWith('/courses')) data = Array.from({ length: 40 }, (_, index) => ({ id: `course-${index}`, name: index === 0 ? '数据库学习' : `课程 ${index + 1}` }))
    else if (url.pathname.endsWith('/chat/sessions')) data = [{ id: 'session-0', course_id: url.searchParams.get('course_id'), title: '事务隔离与并发控制', document_ids: null, created_at: '2026-10-24T00:00:00Z' }]
    else if (url.pathname.endsWith('/messages')) data = Array.from({ length: 30 }, (_, index) => ({ id: `message-${index}`, role: index % 2 ? 'assistant' : 'user', content: `第 ${index + 1} 条消息。\n\n` + '数据库事务保证操作的完整性。'.repeat(10), status: 'completed', thinking: false, citations: [], attachments: [], error: null, created_at: '2026-10-24T00:00:00Z' }))
    await route.fulfill({ json: data })
  })
})

async function openWorkspace(page: Page) {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '事务隔离与并发控制', exact: true })).toBeVisible()
  await expect(page.locator('.chat-message')).toHaveCount(30)
}
async function dragBy(page: Page, amount: number) {
  const box = (await page.getByRole('separator').boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width / 2 + amount, box.y + box.height / 2, { steps: 12 })
  await page.mouse.up()
}

test('左右独立滚动，右侧只有消息区域滚动，输入框固定在底部', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 800 })
  await openWorkspace(page)
  await expect(page).toHaveTitle('StudyRagAgent')
  await expect(page.getByText('PERSONAL LIBRARY', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: '添加学习资料' })).toHaveCount(0)
  const sidebar = page.getByRole('complementary')
  const scroll = page.locator('.conversation-scroll')
  const composer = page.getByRole('form', { name: '聊天输入框' })
  const before = (await composer.boundingBox())!
  const messageScroll = await scroll.evaluate(element => element.scrollTop)
  await sidebar.hover(); await page.mouse.wheel(0, 400)
  await expect.poll(() => sidebar.evaluate(element => element.scrollTop)).toBeGreaterThan(100)
  expect(await scroll.evaluate(element => element.scrollTop)).toBe(messageScroll)
  const leftScroll = await sidebar.evaluate(element => element.scrollTop)
  await scroll.hover(); await page.mouse.wheel(0, -600)
  await expect.poll(() => scroll.evaluate(element => element.scrollTop)).toBeLessThan(messageScroll - 100)
  const after = (await composer.boundingBox())!
  expect(Math.abs(after.y - before.y)).toBeLessThan(1)
  expect(after.y + after.height).toBeLessThanOrEqual(800)
  expect(await sidebar.evaluate(element => element.scrollTop)).toBe(leftScroll)
  expect(await page.locator('.chat-history').evaluate(element => getComputedStyle(element).overflowY)).toBe('visible')
  expect(await page.evaluate(() => window.scrollY)).toBe(0)
  await expect(page.getByRole('button', { name: '回到最新消息' })).toBeVisible()
  await page.getByRole('button', { name: '回到最新消息' }).click()
  await expect.poll(() => scroll.evaluate(element => element.scrollHeight - element.scrollTop - element.clientHeight)).toBeLessThan(64)
  await page.screenshot({ path: 'test-results/workspace-desktop.png' })
})

test('隐形分隔条拖动、宽度边界、刷新记忆、双击复位', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 800 }); await openWorkspace(page)
  const divider = page.getByRole('separator')
  await dragBy(page, 100); await expect(divider).toHaveAttribute('aria-valuenow', '356')
  await page.reload(); await expect(divider).toHaveAttribute('aria-valuenow', '356')
  await dragBy(page, 600); await expect(divider).toHaveAttribute('aria-valuenow', '480')
  await dragBy(page, -450); await expect(divider).toHaveAttribute('aria-valuenow', '256')
  await dragBy(page, 100); await divider.dblclick(); await expect(divider).toHaveAttribute('aria-valuenow', '256')
})

test('键盘调宽与窗口缩放保留足够的聊天空间', async ({ page }) => {
  await openWorkspace(page)
  const divider = page.getByRole('separator')
  await divider.focus(); await page.keyboard.press('ArrowRight'); await expect(divider).toHaveAttribute('aria-valuenow', '272')
  await page.keyboard.press('Shift+ArrowRight'); await expect(divider).toHaveAttribute('aria-valuenow', '304')
  await page.keyboard.press('End'); await expect(divider).toHaveAttribute('aria-valuenow', '480')
  await page.setViewportSize({ width: 900, height: 800 }); await expect(divider).toHaveAttribute('aria-valuenow', '412')
  expect((await page.getByRole('main').boundingBox())!.width).toBeGreaterThanOrEqual(480)
  await page.setViewportSize({ width: 1440, height: 800 }); await expect(divider).toHaveAttribute('aria-valuenow', '480')
})

test('手机以抽屉展示课程，聊天和输入框占满主界面', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 }); await openWorkspace(page)
  await expect(page.getByRole('separator')).toBeHidden()
  await expect(page.locator('.sidebar')).toBeHidden()
  const composer = (await page.getByRole('form', { name: '聊天输入框' }).boundingBox())!
  expect(composer.y + composer.height).toBeGreaterThan(800)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390)
  await page.getByRole('button', { name: '打开课程导航' }).click()
  await expect(page.getByRole('complementary')).toBeVisible()
  await page.getByRole('button', { name: '关闭课程导航' }).click({ position: { x: 370, y: 400 } })
  await expect(page.locator('.sidebar')).toBeHidden()
  await page.screenshot({ path: 'test-results/workspace-mobile.png' })
})
