import { newConversation, selectedConversation, selectConversation } from './workspace-actions'
import { expect, test } from '@playwright/test'
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

const contextPath = process.env.ANSWER_REVIEW_CONTEXT
test.skip(!contextPath, 'Set ANSWER_REVIEW_CONTEXT to the isolated SQL image context.')

test('SQL图片：思考模式直接解释、刷新后继续', async ({ page }) => {
  test.setTimeout(300_000)
  const ctx = JSON.parse(readFileSync(resolve(contextPath!), 'utf8'))
  const out = resolve(ctx.output, `browser-${Date.now()}`)
  mkdirSync(out, { recursive: true })
  const requests: unknown[] = []
  const errors: string[] = []
  page.on('pageerror', e => errors.push(e.message))
  page.on('request', r => {
    if (r.method() === 'POST' && /\/messages$/.test(r.url())) {
      const body = r.postDataJSON()
      requests.push({ fields: Object.keys(body), thinking: body.thinking, question: body.content, images: body.attachment_ids.length })
    }
  })
  try {
    await page.goto('/')
    await page.getByLabel('邮箱').fill(ctx.email)
    await page.getByLabel('密码').fill('review-local-only')
    await page.getByRole('button', { name: '进入学习空间' }).click()
    await page.getByRole('button', { name: 'SQL图片核验隔离验收', exact: true }).click()
    await newConversation(page)
    await expect(page.getByLabel('附上题目图片')).toBeEnabled()
    await page.getByLabel('附上题目图片').setInputFiles(resolve(ctx.output, 'sql.png'))
    await expect(page.locator('.attachment-card img')).toBeVisible()
    const toggle = page.getByRole('button', { name: '深度思考', exact: true })
    await toggle.click()
    await page.getByLabel('输入问题').fill('图片中的SQL查询是什么意思？')
    await page.getByRole('button', { name: '发送', exact: true }).click()
    await expect(toggle).toBeDisabled()
    await expect(page.getByLabel('输入问题')).toBeEnabled({ timeout: 180_000 })
    const answer = page.locator('.chat-message.assistant').last()
    await expect(answer).toContainText(/部门|系/)
    await expect(answer).toContainText('平均')
    await expect(answer).not.toContainText('【限制】')
    await page.screenshot({ path: resolve(out, 'sql-answer.png'), fullPage: true })
    const sid = await selectedConversation(page)
    await page.reload()
    await page.getByRole('button', { name: 'SQL图片核验隔离验收', exact: true }).click()
    await selectConversation(page, sid)
    await expect(answer).toContainText(/部门|系/)
    await toggle.click()
    await page.getByLabel('输入问题').fill('继续刚才图片，三个部门总额为100、200、300时，哪些会被选中？')
    await page.getByRole('button', { name: '发送', exact: true }).click()
    await expect(page.getByLabel('输入问题')).toBeDisabled()
    await expect(page.getByLabel('输入问题')).toBeEnabled({ timeout: 180_000 })
    await expect(answer).toContainText('200')
    await expect(answer).toContainText('300')
    await expect(answer).not.toContainText('【限制】')
    await page.screenshot({ path: resolve(out, 'sql-followup.png'), fullPage: true })
    expect(errors).toEqual([])
    writeFileSync(resolve(out, 'passed.json'), JSON.stringify({ checks: ['image-upload', 'thinking-snapshot', 'sql-explanation', 'refresh', 'plain-followup'], requests, errors }, null, 2))
  } finally {
    writeFileSync(resolve(out, 'requests.json'), JSON.stringify({ requests, errors }, null, 2))
    await page.screenshot({ path: resolve(out, 'final-state.png'), fullPage: true })
  }
})
