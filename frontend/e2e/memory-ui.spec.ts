import { expect, test } from '@playwright/test'

for (const theme of ['light', 'dark'] as const) {
  for (const width of [1440, 390]) {
    test(`${theme} ${width}px：顶部右侧入口、简洁回答与透明滚动条`, async ({ page }) => {
      await page.setViewportSize({ width, height: 800 })
      await page.addInitScript(value => localStorage.setItem('studyrag-theme', value), theme)
      await page.route('**/api/v1/**', async route => {
        const path = new URL(route.request().url()).pathname
        let data: unknown = []
        if (path.endsWith('/memory/capabilities')) data = { engine: 'legacy', max_content_chars: 1000 }
        else if (path.endsWith('/me')) data = { id: 'memory-ui-user', email: 'memory-ui@example.test' }
        else if (path.endsWith('/courses')) data = [{ id: 'c1', name: '界面自制样本课程' }]
        else if (path.endsWith('/chat/sessions')) data = [{ id: 's1', course_id: 'c1', title: '一个较长的对话标题用于检查窄屏下顶部按钮的排列', document_ids: null, created_at: '2026-11-03T00:00:00Z' }]
        else if (path.endsWith('/messages')) data = [
          { id: 'm1', role: 'user', content: '请解释事务。', status: 'completed', thinking: false, error: null, citations: [] },
          { id: 'm2', role: 'assistant', content: '事务保证一组操作的完整性。', status: 'completed', thinking: true, error: null, citations: [] },
        ]
        else if (path.endsWith('/memory/preferences')) {
          const control = { use: true, write: true, suggest: true, version: 1, suppressed: [] }
          data = { enabled: true, items: [], effective: { code_language: 'Python' }, settings: control, global_settings: control, versions: {}, values: {} }
        } else if (path.endsWith('/memory')) data = { available: true, enabled: false, version: 1, summary: null, covered: 0, cutoff: null, updated_at: null, jobs: [] }
        await route.fulfill({ json: data })
      })
      await page.goto('/')
      await expect(page.getByLabel('输入问题')).toBeEnabled()
      const answer = page.locator('.chat-message.assistant')
      await expect(answer).toContainText('事务保证一组操作的完整性。')
      await expect(answer.locator(':scope > strong, .thinking-history')).toHaveCount(0)
      await expect(page.getByRole('button', { name: '深度思考', exact: true })).toBeVisible()
      const button = page.getByRole('button', { name: '记忆管理', exact: true })
      const header = (await page.locator('.conversation-header').boundingBox())!
      const box = (await button.boundingBox())!
      const title = (await page.locator('.conversation-title').boundingBox())!
      expect(header.x + header.width - box.x - box.width).toBeLessThanOrEqual(30)
      expect(box.x).toBeGreaterThanOrEqual(title.x + title.width)
      expect(box.y).toBeGreaterThanOrEqual(header.y)
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width)
      await button.click()
      const dialog = page.getByRole('dialog', { name: '记忆管理', exact: true })
      await expect(dialog.getByText('偏好记忆可用', { exact: true })).toBeVisible()
      await expect(dialog.getByText('明确长期要求可在聊天里直接保存；推断建议经你确认后才生效。')).toHaveCount(0)
      const scrollbar = await dialog.evaluate(element => {
        const style = getComputedStyle(element)
        const webkit = CSS.supports('selector(::-webkit-scrollbar)')
        return {
          overflows: element.scrollHeight > element.clientHeight,
          thin: webkit ? getComputedStyle(element, '::-webkit-scrollbar').width === '6px' : style.scrollbarWidth === 'thin',
          transparent: webkit ? getComputedStyle(element, '::-webkit-scrollbar-track').backgroundColor === 'rgba(0, 0, 0, 0)' : style.scrollbarColor.endsWith('rgba(0, 0, 0, 0)'),
        }
      })
      expect(scrollbar).toEqual({ overflows: true, thin: true, transparent: true })
      await dialog.hover()
      await page.mouse.wheel(0, 400)
      await expect.poll(() => dialog.evaluate(element => element.scrollTop)).toBeGreaterThan(0)
      await page.keyboard.press('Escape')
      await expect(dialog).toHaveCount(0)
      await expect(button).toBeFocused()
    })
  }
}
