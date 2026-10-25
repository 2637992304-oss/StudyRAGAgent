import { newConversation, selectedConversation, selectConversation } from './workspace-actions'
import { expect, test, type Page } from '@playwright/test'
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

const contextPath = process.env.AUTO5_CONTEXT
test.skip(!contextPath, 'Set AUTO5_CONTEXT to an isolated automatic-tools acceptance context.')

test('自动工具真实浏览器：自然语言、来源折叠、刷新、仓库撤销、图片、停止与账号隔离', async ({ page }) => {
  test.setTimeout(600_000)
  const ctx = JSON.parse(readFileSync(resolve(contextPath!), 'utf8'))
  const out = resolve(ctx.output, `browser-${Date.now()}`)
  mkdirSync(out, { recursive: true })
  const requests: Record<string, unknown>[] = []
  const errors: string[] = []
  const checks: string[] = []
  page.on('pageerror', e => errors.push(e.message))
  page.on('request', request => {
    if (request.method() === 'POST' && /\/messages$/.test(request.url())) {
      const data = request.postDataJSON()
      requests.push({ fields: Object.keys(data), thinking: data.thinking, content: data.content, attachments: data.attachment_ids.length })
    }
  })
  async function login(p: Page, email: string, course: string) {
    await p.getByLabel('邮箱').fill(email)
    await p.getByLabel('密码').fill('auto5-local-only')
    await p.getByRole('button', { name: '进入学习空间' }).click()
    await p.getByRole('button', { name: course, exact: true }).click()
  }
  async function send(question: string) {
    await page.getByLabel('输入问题').fill(question)
    await page.getByRole('button', { name: '发送', exact: true }).click()
    await expect(page.getByLabel('输入问题')).toBeDisabled()
    await expect(page.getByRole('button', { name: '深度思考', exact: true })).toBeDisabled()
    await expect(page.getByLabel('输入问题')).toBeEnabled({ timeout: 220_000 })
  }
  const answer = () => page.locator('.chat-message.assistant').last()
  try {
    await page.goto('/')
    await login(page, ctx.email, '自动工具隔离验收')
    await expect(page.getByRole('checkbox', { name: '联网搜索', exact: true })).toHaveCount(0)
    await expect(page.getByLabel('公开搜索关键词')).toHaveCount(0)
    await expect(page.getByLabel('GitHub仓库', { exact: true })).toHaveCount(0)
    checks.push('no-external-configuration-panel')
    await newConversation(page)
    await send('请搜索并读取 Python 官方 asyncio 文档，用一句话说明适合的任务并引用，不添加其他事实。')
    await expect(answer().locator('.source-disclosure')).toBeVisible()
    await expect(answer().locator('.source-disclosure')).not.toHaveAttribute('open', '')
    await answer().locator('.answer-text a[href^="#source-"]').first().click()
    await expect(answer().locator('.source-disclosure')).toHaveAttribute('open', '')
    await expect(answer().getByRole('link', { name: '打开网页', exact: true }).first()).toHaveAttribute('href', /^https:\/\//)
    checks.push('natural-language-search', 'default-collapsed', 'inline-citation-expands-and-locates')
    await page.screenshot({ path: resolve(out, 'web-source.png'), fullPage: true })
    const sid = await selectedConversation(page)
    await page.reload()
    await page.getByRole('button', { name: '自动工具隔离验收', exact: true }).click()
    await selectConversation(page, sid)
    await expect(answer().locator('.source-disclosure')).toBeVisible()
    await page.getByRole('button', { name: '深度思考', exact: true }).click()
    await send('继续刚才的问题，重新读取已发现的官方网页，用一句话说明适合的任务并引用。')
    await expect(answer().locator('.source-disclosure')).toBeVisible()
    checks.push('refresh', 'thinking-web-followup')
    await page.screenshot({ path: resolve(out, 'refreshed-followup.png'), fullPage: true })
    await page.getByRole('button', { name: '深度思考', exact: true }).click()
    await newConversation(page)
    await send('读取 https://github.com/octocat/Hello-World.git 的README，用一句话概述实际内容并引用文件行号。')
    await answer().getByText(/查看来源/).click()
    await expect(answer().getByRole('link', { name: '打开固定提交来源', exact: true }).last()).toHaveAttribute('href', /\/blob\/[a-f0-9]{40}\/README#L1-L1$/)
    checks.push('repo-link-only-auto-grant', 'fixed-sha-line-link')
    await page.screenshot({ path: resolve(out, 'github-source.png'), fullPage: true })
    await answer().getByRole('button', { name: '撤销此仓库访问', exact: true }).first().click()
    await expect(answer()).toContainText('仓库授权已失效')
    await expect(answer().locator('.source-card')).toHaveCount(0)
    checks.push('revocation-hides-body-and-sources')
    await page.screenshot({ path: resolve(out, 'revoked.png'), fullPage: true })
    // Public, self-authored arithmetic image. No tool-policy fields are injected.
    await newConversation(page)
    const imagePath = resolve(ctx.output, 'browser-equation.png')
    await expect(page.getByLabel('附上题目图片')).toBeEnabled()
    await page.getByLabel('附上题目图片').setInputFiles(imagePath)
    await expect(page.locator('.attachments .attachment-card img')).toBeVisible()
    await send('不要联网，计算图片里的算式，简短回答。')
    await expect(answer()).toContainText('14')
    checks.push('image-upload-and-real-answer')
    await page.getByRole('button', { name: '深度思考', exact: true }).click()
    await page.getByLabel('输入问题').fill('允许联网，请搜索Python官方文档并详细解释协程调度的每个步骤。')
    await page.getByRole('button', { name: '发送', exact: true }).click()
    await expect(page.getByLabel('输入问题')).toBeDisabled()
    await page.screenshot({ path: resolve(out, 'progress.png'), fullPage: true })
    await page.getByRole('button', { name: '停止', exact: true }).click()
    await expect(page.getByLabel('输入问题')).toBeEnabled()
    await expect(answer()).toContainText(/停止|中断/)
    checks.push('progress', 'cancel')
    await page.getByRole('button', { name: '退出', exact: true }).click()
    await login(page, 'other-' + ctx.email, '另一账号自动工具')
    await expect(page.getByRole('button', { name: '深度思考', exact: true })).toHaveAttribute('aria-pressed', 'false')
    await expect(page.locator('.source-card')).toHaveCount(0)
    checks.push('account-switch-scope-and-thinking-isolation')
    expect(errors).toEqual([])
    expect(requests.every(r => !(r.fields as string[]).some(k => ['web_enabled', 'web_queries', 'repository_grant_ids'].includes(k)))).toBeTruthy()
    checks.push('natural-request-body-no-manual-fields')
    await page.screenshot({ path: resolve(out, 'account-switch.png'), fullPage: true })
  } finally {
    writeFileSync(resolve(out, 'checks.json'), JSON.stringify({ realBrowser: true, checks, requests, errors }, null, 2))
    await page.screenshot({ path: resolve(out, 'final-state.png'), fullPage: true })
  }
})
