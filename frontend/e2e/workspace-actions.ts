import { expect, type Page } from '@playwright/test'

export async function newConversation(page: Page) {
  await page.locator('.course-row.current .course').hover()
  await page.locator('.course-row.current button[title="新建对话"]').click()
  await expect(page.getByLabel('输入问题')).toBeEnabled()
}

export async function selectedConversation(page: Page) {
  return page.locator('.session-link[aria-current="page"]').getAttribute('data-session-id')
}

export async function selectConversation(page: Page, id: string | null) {
  expect(id).toBeTruthy()
  const course = page.locator('.course-row.current .course')
  if (await course.getAttribute('aria-expanded') === 'false') await course.click()
  await page.locator(`.session-link[data-session-id="${id}"]`).click()
  await expect(page.getByLabel('输入问题')).toBeEnabled()
}
