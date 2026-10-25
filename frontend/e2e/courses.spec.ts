import { expect, test, type Page } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  const courseList = [{ id: 'course-0', name: '数据库学习' }]
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname
    let data: unknown = []
    if (path.endsWith('/me')) data = { id: 'courses-user', email: 'courses@example.test' }
    else if (path.endsWith('/courses')) {
      if (route.request().method() === 'POST') {
        const course = { id: `course-${courseList.length}`, name: route.request().postDataJSON().name as string }
        courseList.push(course)
        await route.fulfill({ status: 201, json: course })
        return
      }
      data = courseList
    }
    await route.fulfill({ json: data })
  })
})

async function openDialog(page: Page) {
  await page.goto('/')
  await expect(page.getByRole('button', { name: '数据库学习', exact: true })).toBeVisible()
  await page.locator('.side-heading').hover()
  await page.getByRole('button', { name: '添加新课程', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '创建课程', exact: true })
  await expect(dialog).toBeVisible()
  return dialog
}

test('悬停显示加号，弹窗全屏居中、自动聚焦、限制焦点并支持 Escape 关闭', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('button', { name: '数据库学习', exact: true })).toBeVisible()
  const add = page.getByRole('button', { name: '添加新课程', exact: true })
  await expect(add).toHaveCSS('opacity', '0')
  await expect(page.getByLabel('新课程名称')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '添加', exact: true })).toHaveCount(0)
  await page.locator('.side-heading').hover()
  await expect(add).toHaveCSS('opacity', '1')
  await add.click()
  const dialog = page.getByRole('dialog', { name: '创建课程', exact: true })
  const input = dialog.getByLabel('新课程名称')
  const create = dialog.getByRole('button', { name: '创建', exact: true })
  await expect(input).toBeFocused()
  await expect(create).toBeDisabled()
  await input.fill('   ')
  await expect(create).toBeDisabled()
  const bounds = (await dialog.boundingBox())!
  const viewport = page.viewportSize()!
  expect(Math.abs(bounds.x + bounds.width / 2 - viewport.width / 2)).toBeLessThan(2)
  expect(Math.abs(bounds.y + bounds.height / 2 - viewport.height / 2)).toBeLessThan(2)
  await input.fill('线性代数')
  await page.keyboard.press('Tab')
  await expect(create).toBeFocused()
  await add.evaluate(element => element.focus())
  await expect(create).toBeFocused()
  await page.keyboard.press('Shift+Tab')
  await expect(input).toBeFocused()
  await page.keyboard.press('Shift+Tab')
  await expect(dialog.getByRole('button', { name: '关闭创建课程' })).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(input).toBeFocused()
  await page.screenshot({ path: 'test-results/create-course-desktop.png' })
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  await expect(add).toBeFocused()
})

test('Enter 创建课程会去除两侧空格，阻止重复提交并切换到新课程', async ({ page }) => {
  let finish!: () => void
  const gate = new Promise<void>(resolve => { finish = resolve })
  const submitted: unknown[] = []
  await page.route('**/api/v1/courses', async route => {
    if (route.request().method() !== 'POST') { await route.fallback(); return }
    submitted.push(route.request().postDataJSON())
    await gate
    await route.fulfill({ status: 201, json: { id: 'new-course', name: '线性代数' } })
  })
  const dialog = await openDialog(page)
  await dialog.getByLabel('新课程名称').fill('  线性代数  ')
  await page.keyboard.press('Enter')
  await expect(dialog.getByRole('button', { name: '创建中…', exact: true })).toBeDisabled()
  await expect(dialog.getByLabel('新课程名称')).toBeDisabled()
  await expect(dialog.getByRole('button', { name: '关闭创建课程' })).toBeDisabled()
  await page.keyboard.press('Enter')
  await page.keyboard.press('Escape')
  await expect(dialog).toBeVisible()
  expect(submitted).toEqual([{ name: '线性代数' }])
  finish()
  await expect(dialog).toHaveCount(0)
  await expect(page.getByRole('navigation', { name: '课程列表' }).getByRole('button', { name: '线性代数', exact: true })).toHaveAttribute('aria-expanded', 'true')
  await expect(page.getByRole('button', { name: '线性代数', exact: true })).toBeVisible()
  await expect(page.getByRole('status')).toContainText('课程已创建')
})

test('创建失败留在弹窗内显示错误并保留名称，支持重试', async ({ page }) => {
  let attempts = 0
  await page.route('**/api/v1/courses', async route => {
    if (route.request().method() !== 'POST') { await route.fallback(); return }
    attempts++
    if (attempts === 1) await route.fulfill({ status: 503, json: { detail: '课程创建失败，请重试。' } })
    else await route.fulfill({ status: 201, json: { id: 'new-course', name: '高等数学' } })
  })
  const dialog = await openDialog(page)
  await dialog.getByLabel('新课程名称').fill('高等数学')
  await dialog.getByRole('button', { name: '创建', exact: true }).click()
  await expect(dialog.getByRole('alert')).toHaveText('课程创建失败，请重试。')
  await expect(dialog.getByLabel('新课程名称')).toHaveValue('高等数学')
  await dialog.getByRole('button', { name: '创建', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  await expect(page.getByRole('button', { name: '高等数学', exact: true })).toBeVisible()
})

test('窄屏可直接使用加号，关闭按钮与遮罩均可退出且不创建课程', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 780 })
  await page.goto('/')
  await page.getByRole('button', { name: '打开课程导航' }).click()
  const add = page.getByRole('button', { name: '添加新课程', exact: true })
  await expect(add).toHaveCSS('opacity', '1')
  await add.click()
  const dialog = page.getByRole('dialog', { name: '创建课程', exact: true })
  await expect(dialog).toBeVisible()
  const bounds = (await dialog.boundingBox())!
  expect(bounds.x).toBeGreaterThanOrEqual(16)
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(344)
  await page.screenshot({ path: 'test-results/create-course-mobile.png' })
  await dialog.getByRole('button', { name: '关闭创建课程' }).click()
  await expect(dialog).toHaveCount(0)
  await add.click()
  await dialog.getByLabel('新课程名称').fill('取消的课程')
  await page.mouse.click(4, 4)
  await expect(dialog).toHaveCount(0)
  await expect(page.getByRole('navigation', { name: '课程列表' }).getByRole('button', { name: '数据库学习', exact: true })).toHaveCount(1)
})
