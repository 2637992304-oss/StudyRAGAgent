import { expect, test } from '@playwright/test'

const password = 'a long local test password'

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/me')) {
      if (route.request().headers().authorization === 'Bearer registration-token') {
        await route.fulfill({ json: { id: 'new-user', email: 'new@example.test' } })
      } else await route.fulfill({ status: 401, json: { detail: '请先登录' } })
      return
    }
    if (path.endsWith('/auth/register') || path.endsWith('/auth/login')) {
      await route.fulfill({ status: path.endsWith('/register') ? 201 : 200, json: { access_token: 'registration-token', token_type: 'bearer' } })
      return
    }
    if (path.endsWith('/courses') && route.request().method() === 'POST') {
      await route.fulfill({ status: 201, json: { id: 'new-course', name: route.request().postDataJSON().name } })
      return
    }
    await route.fulfill({ json: [] })
  })
  await page.goto('/')
  await expect(page.getByRole('button', { name: '进入学习空间', exact: true })).toBeVisible()
})

test('注册自动登录、刷新保持会话，并可从空状态创建第一门课程', async ({ page }) => {
  await page.getByRole('button', { name: '注册', exact: true }).click()
  await page.getByLabel('邮箱', { exact: true }).fill('new@example.test')
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码', { exact: true }).fill(password)
  const submitted = page.waitForRequest('**/api/v1/auth/register')
  await page.getByRole('button', { name: '注册并进入学习空间' }).click()
  expect((await submitted).postDataJSON()).toEqual({ email: 'new@example.test', password })
  await expect(page.getByRole('status')).toContainText('注册成功')
  await expect(page.locator('.account')).toContainText('new@example.test')
  expect(await page.evaluate(() => localStorage.getItem('studyrag-token'))).toBe('registration-token')
  await page.reload()
  await page.getByRole('button', { name: '创建第一门课程' }).click()
  await page.getByLabel('新课程名称').fill('第一门课程')
  await page.getByRole('button', { name: '创建', exact: true }).click()
  await expect(page.getByRole('button', { name: '第一门课程', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '退出', exact: true }).click()
  await expect(page.getByRole('button', { name: '进入学习空间', exact: true })).toBeVisible()
  await expect(page.getByLabel('密码', { exact: true })).toHaveValue('')
  expect(await page.evaluate(() => localStorage.getItem('studyrag-token'))).toBeNull()
})

test('确认密码不一致时不发送注册请求，切换登录清除密码及错误', async ({ page }) => {
  const requests: string[] = []
  page.on('request', request => { if (request.url().endsWith('/auth/register')) requests.push(request.url()) })
  await page.getByRole('button', { name: '注册', exact: true }).click()
  await page.getByLabel('邮箱', { exact: true }).fill('new@example.test')
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码', { exact: true }).fill('a different long password')
  await page.getByRole('button', { name: '注册并进入学习空间' }).click()
  await expect(page.getByRole('alert')).toHaveText('两次输入的密码不一致')
  expect(requests).toEqual([])
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('alert')).toHaveCount(0)
  await expect(page.getByLabel('密码', { exact: true })).toHaveValue('')
  await expect(page.getByLabel('确认密码', { exact: true })).toHaveCount(0)
  await expect(page.getByLabel('邮箱', { exact: true })).toHaveValue('new@example.test')
})

test('重复邮箱及后端字段校验显示可读错误，失败后可重试', async ({ page }) => {
  let attempt = 0
  await page.route('**/api/v1/auth/register', async route => {
    attempt++
    if (attempt === 1) await route.fulfill({ status: 409, json: { detail: '该邮箱已注册，请直接登录' } })
    else if (attempt === 2) await route.fulfill({ status: 422, json: { detail: [{ loc: ['body', 'password'], msg: 'String should have at least 15 characters' }] } })
    else await route.fallback()
  })
  await page.getByRole('button', { name: '注册', exact: true }).click()
  await page.getByLabel('邮箱', { exact: true }).fill('new@example.test')
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '注册并进入学习空间' }).click()
  await expect(page.getByRole('alert')).toHaveText('该邮箱已注册，请直接登录')
  await expect(page.getByLabel('邮箱', { exact: true })).toHaveValue('new@example.test')
  await page.getByRole('button', { name: '注册并进入学习空间' }).click()
  await expect(page.getByRole('alert')).toContainText('密码需为 15–128 个字符')
  await page.getByRole('button', { name: '注册并进入学习空间' }).click()
  await expect(page.getByRole('button', { name: '创建第一门课程' })).toBeVisible()
})

test('注册期间锁定表单和模式切换，阻止重复提交', async ({ page }) => {
  let finish!: () => void
  const gate = new Promise<void>(resolve => { finish = resolve })
  let requests = 0
  await page.route('**/api/v1/auth/register', async route => {
    requests++
    await gate
    await route.fallback()
  })
  await page.getByRole('button', { name: '注册', exact: true }).click()
  await page.getByLabel('邮箱', { exact: true }).fill('new@example.test')
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '注册并进入学习空间' }).click()
  await expect(page.getByRole('button', { name: '注册中…' })).toBeDisabled()
  await expect(page.getByLabel('邮箱', { exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: '登录', exact: true })).toBeDisabled()
  await page.keyboard.press('Enter')
  expect(requests).toBe(1)
  finish()
  await expect(page.getByRole('button', { name: '创建第一门课程' })).toBeVisible()
})

test('保留已有短密码登录，登录请求不套用新注册的密码规则', async ({ page }) => {
  await page.getByLabel('邮箱', { exact: true }).fill('new@example.test')
  await page.getByLabel('密码', { exact: true }).fill('old-pass')
  const submitted = page.waitForRequest('**/api/v1/auth/login')
  await page.getByRole('button', { name: '进入学习空间', exact: true }).click()
  expect((await submitted).postDataJSON().password).toBe('old-pass')
  await expect(page.getByRole('button', { name: '创建第一门课程' })).toBeVisible()
})

test('初始会话检查完成后才允许注册，避免延迟的未登录响应清除新令牌', async ({ page }) => {
  let finish!: () => void
  const gate = new Promise<void>(resolve => { finish = resolve })
  await page.route('**/api/v1/me', async route => {
    await gate
    await route.fallback()
  })
  await page.reload()
  await expect(page.getByRole('button', { name: '检查登录状态…' })).toBeDisabled()
  await expect(page.getByRole('button', { name: '注册', exact: true })).toBeDisabled()
  finish()
  await expect(page.getByRole('button', { name: '注册', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: '注册', exact: true }).click()
  await page.getByLabel('邮箱', { exact: true }).fill('new@example.test')
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '注册并进入学习空间' }).click()
  await expect(page.getByRole('button', { name: '创建第一门课程' })).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('studyrag-token'))).toBe('registration-token')
})

test('窄屏注册表单完整显示，密码长度和全空格检查阻止无效提交', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 780 })
  await page.getByRole('button', { name: '注册', exact: true }).click()
  await page.getByLabel('邮箱', { exact: true }).fill('new@example.test')
  await page.getByLabel('密码', { exact: true }).fill('short')
  await page.getByLabel('确认密码', { exact: true }).fill('short')
  expect(await page.getByLabel('密码', { exact: true }).evaluate((input: HTMLInputElement) => input.checkValidity())).toBe(false)
  await page.getByLabel('密码', { exact: true }).fill(' '.repeat(15))
  await page.getByLabel('确认密码', { exact: true }).fill(' '.repeat(15))
  await page.getByRole('button', { name: '注册并进入学习空间' }).click()
  await expect(page.getByRole('alert')).toHaveText('密码不能全部为空格')
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(360)
  await page.screenshot({ path: 'test-results/registration-mobile.png', fullPage: true })
})
