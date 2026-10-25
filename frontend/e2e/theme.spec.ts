import { expect, test, type Page } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url())
    let data: unknown = []
    if (url.pathname.endsWith('/me')) data = { id: 'theme-user', email: 'student@example.test' }
    else if (url.pathname.endsWith('/courses')) data = [{ id: 'c1', name: '数据库学习' }]
    else if (url.pathname.endsWith('/chat/sessions')) data = [{ id: 's1', course_id: 'c1', title: '事务隔离与并发控制', document_ids: null, created_at: '2026-10-24T00:00:00Z' }]
    else if (url.pathname.endsWith('/messages')) data = [
      { id: 'm1', role: 'user', content: '请解释数据库事务，并给出 SQL 示例。', status: 'completed', thinking: false, error: null, citations: [], created_at: '2026-10-24T00:00:00Z' },
      { id: 'm2', role: 'assistant', content: '## 数据库事务\n\n事务具有**原子性**、一致性、隔离性与持久性。\n\n关键点：`HAVING` 用于筛选分组后的结果，例如 `SUM(salary)`。仓库地址为 `https://github.com/example/study.git`。\n\n```sql\nBEGIN;\nUPDATE accounts SET balance = balance - 100;\nCOMMIT;\n```\n\n| 特性 | 含义 |\n| --- | --- |\n| 原子性 | 全部执行或全部撤销 |\n| 持久性 | 提交后保存结果 |\n\n' + '可以通过隔离级别控制事务之间的相互影响。\n\n'.repeat(8), status: 'completed', thinking: true, error: null, citations: [], created_at: '2026-10-24T00:00:00Z' },
    ]
    else if (url.pathname.endsWith('/documents')) data = [{ id: 'd1', course_id: 'c1', filename: '大学数据库.pdf', document_type: '课件', size_bytes: 643789, status: 'ready', job_id: '', created_at: '2026-10-24T00:00:00Z' }]
    await route.fulfill({ json: data })
  })
})

async function openWorkspace(page: Page) {
  await page.goto('/')
  await expect(page.getByLabel('输入问题')).toBeEnabled()
  await expect(page.locator('.chat-message')).toHaveCount(2)
}

test('月亮在账号右侧，键盘切换主题不丢草稿或改变消息滚动位置', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openWorkspace(page)
  const toggle = page.getByRole('button', { name: '切换到深色模式', exact: true })
  const email = (await page.locator('.account-email').boundingBox())!
  const button = (await toggle.boundingBox())!
  expect(button.x).toBeGreaterThanOrEqual(email.x + email.width)
  expect(Math.abs(button.y + button.height / 2 - email.y - email.height / 2)).toBeLessThan(2)
  await page.getByLabel('输入问题').fill('这段草稿需要保留')
  const scroll = page.locator('.conversation-scroll')
  await scroll.evaluate(element => { element.scrollTop = 120 })
  const position = await scroll.evaluate(element => element.scrollTop)
  const inputBox = (await page.getByRole('form', { name: '聊天输入框' }).boundingBox())!
  await toggle.focus(); await page.keyboard.press('Space')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await expect(page.getByRole('button', { name: '切换到浅色模式', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.locator('.workspace')).toHaveCSS('background-color', 'rgb(24, 31, 27)')
  await expect(page.locator('.chat-form')).toHaveCSS('background-color', 'rgb(34, 45, 38)')
  await expect(page.getByLabel('输入问题')).toHaveValue('这段草稿需要保留')
  expect(await scroll.evaluate(element => element.scrollTop)).toBe(position)
  expect((await page.getByRole('form', { name: '聊天输入框' }).boundingBox())!.y).toBe(inputBox.y)
  await scroll.evaluate(element => { element.scrollTop = 0 })
  await page.mouse.move(1000, 20)
  await page.screenshot({ path: 'test-results/theme-dark-desktop.png' })
})

test('深色偏好刷新后保持，切回浅色，退出登录也正确显示', async ({ page }) => {
  await openWorkspace(page)
  await page.getByRole('button', { name: '切换到深色模式' }).click()
  expect(await page.evaluate(() => localStorage.getItem('studyrag-theme'))).toBe('dark')
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await page.getByRole('button', { name: '切换到浅色模式' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  await expect(page.locator('.workspace')).toHaveCSS('background-color', 'rgb(250, 252, 251)')
  await expect(page.locator('.chat-form')).toHaveCSS('background-color', 'rgb(255, 255, 255)')
  await page.getByRole('button', { name: '切换到深色模式' }).click()
  await page.getByRole('button', { name: '退出', exact: true }).click()
  await expect(page.locator('.login-card')).toBeVisible()
  await expect(page.locator('.login-card')).toHaveCSS('background-color', 'rgb(34, 45, 38)')
  await expect(page.getByLabel('邮箱', { exact: true })).toHaveCSS('color', 'rgb(220, 230, 223)')
})

test('深色下资料、创建、重命名弹窗和右键菜单配色一致', async ({ page }) => {
  await openWorkspace(page)
  await page.getByRole('button', { name: '切换到深色模式' }).click()
  await page.getByRole('button', { name: '数据库学习', exact: true }).hover()
  await page.getByRole('button', { name: '数据库学习的课程资料' }).click()
  const library = page.getByRole('dialog', { name: '课程资料', exact: true })
  await expect(library.getByText('大学数据库.pdf', { exact: true })).toBeVisible()
  await expect(library).toHaveCSS('background-color', 'rgb(34, 45, 38)')
  await expect(library.getByLabel('资料类型')).toHaveCSS('color', 'rgb(220, 230, 223)')
  await page.screenshot({ path: 'test-results/theme-dark-library.png' })
  await library.getByRole('button', { name: '关闭课程资料' }).click()
  await page.getByRole('button', { name: '事务隔离与并发控制', exact: true }).click({ button: 'right' })
  await expect(page.getByRole('menu')).toHaveCSS('background-color', 'rgb(34, 45, 38)')
  await page.getByRole('menuitem', { name: '重命名', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '重命名对话' })).toHaveCSS('color', 'rgb(220, 230, 223)')
  await expect(page.getByLabel('对话名称')).toHaveCSS('background-color', 'rgb(34, 45, 38)')
  await page.keyboard.press('Escape')
  await page.locator('.side-heading').hover()
  await page.getByRole('button', { name: '添加新课程' }).click()
  await expect(page.getByRole('dialog', { name: '创建课程' })).toHaveCSS('background-color', 'rgb(34, 45, 38)')
})

test('手机抽屉内可切换模式，输入区域与代码表格文字保持清晰', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openWorkspace(page)
  await page.getByRole('button', { name: '打开课程导航' }).click()
  await page.getByRole('button', { name: '切换到深色模式' }).click()
  await expect(page.locator('.sidebar')).toHaveCSS('background-color', 'rgb(16, 45, 37)')
  await page.keyboard.press('Escape')
  await expect(page.locator('.sidebar')).toBeHidden()
  const scroll = page.locator('.conversation-scroll')
  await scroll.evaluate(element => { element.scrollTop = 0 })
  await expect(page.locator('.markdown pre')).toHaveCSS('background-color', 'rgb(34, 47, 39)')
  await expect(page.locator('.markdown th').first()).toHaveCSS('background-color', 'rgb(39, 55, 45)')
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390)
  await page.screenshot({ path: 'test-results/theme-dark-mobile.png' })
})

test('浏览器不能保存偏好时仍可切换模式', async ({ page }) => {
  await page.addInitScript(() => { Storage.prototype.setItem = () => { throw new DOMException('Storage disabled', 'SecurityError') } })
  await openWorkspace(page)
  await page.getByRole('button', { name: '切换到深色模式' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await page.getByRole('button', { name: '切换到浅色模式' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
})
