import { readFileSync } from 'node:fs'
import { expect, test } from '@playwright/test'

test('容器部署：真实登录、课程历史、引用展开和刷新', async ({ page }) => {
  test.skip(process.env.STAGE6_LIVE !== '1', '仅在阶段6隔离部署验收时启用')
  const state = JSON.parse(readFileSync(process.env.STAGE6_STATE ?? '../.runtime/stage6/live-state.json', 'utf8'))
  await page.goto('/')
  await page.getByLabel('邮箱', { exact: true }).fill(state.email)
  await page.getByLabel('密码', { exact: true }).fill('isolated stage six acceptance password')
  await page.getByRole('button', { name: '进入学习空间', exact: true }).click()
  const course = page.getByRole('button', { name: '阶段6公开示例：微积分', exact: true })
  await expect(course).toBeVisible()
  if (await course.getAttribute('aria-expanded') === 'false') await course.click()
  await page.locator(`.session-link[data-session-id="${state.session_id}"]`).click()
  await expect(page.locator('.chat-history')).toContainText('瞬时变化率')
  await page.reload()
  await expect(page.locator('.chat-history')).toContainText('瞬时变化率')
  await page.locator('.source-disclosure summary').click()
  await expect(page.locator('.source-card')).toContainText('derivative.md')
  await expect(page.locator('.source-card')).toContainText('瞬时变化率')
  await page.screenshot({ path: '../docs/stage6/images/course-chat.png', fullPage: true })
})
