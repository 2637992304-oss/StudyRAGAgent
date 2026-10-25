import { execFileSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'

// Opt in against the local API/PostgreSQL; create and remove only this run's data.
test('真实注册、重复邮箱、并发注册、刷新登录、课程引导与账号隔离', async ({ page, request }, testInfo) => {
  test.skip(process.env.REGISTRATION_LIVE !== '1', '设置 REGISTRATION_LIVE=1 后验证本地服务')
  const prefix = `registration-e2e-${randomUUID()}`
  const emails = [`${prefix}-owner@example.test`, `${prefix}-new@example.test`, `${prefix}-race@example.test`]
  const password = 'a long local test password'
  const api = 'http://127.0.0.1:8000/api/v1'
  try {
    const ownerResponse = await request.post(`${api}/auth/register`, { data: { email: emails[0], password } })
    expect(ownerResponse.status()).toBe(201)
    const ownerHeaders = { Authorization: `Bearer ${(await ownerResponse.json()).access_token}` }
    const courseResponse = await request.post(`${api}/courses`, { headers: ownerHeaders, data: { name: '另一个账号的私人课程' } })
    expect(courseResponse.status()).toBe(201)
    const ownerCourse = (await courseResponse.json()).id as string

    await page.goto('/')
    await page.getByRole('button', { name: '注册', exact: true }).click()
    await page.getByLabel('邮箱', { exact: true }).fill(emails[1])
    await page.getByLabel('密码', { exact: true }).fill(password)
    await page.getByLabel('确认密码', { exact: true }).fill(password)
    await page.screenshot({ path: testInfo.outputPath('registration-desktop.png'), fullPage: true })
    await page.getByRole('button', { name: '注册并进入学习空间' }).click()
    await expect(page.getByRole('status')).toContainText('注册成功')
    await expect(page.locator('.account')).toContainText(emails[1])
    await expect(page.getByRole('navigation', { name: '课程列表' }).getByRole('button')).toHaveCount(0)
    const token = await page.evaluate(() => localStorage.getItem('studyrag-token'))
    const newHeaders = { Authorization: `Bearer ${token}` }
    expect((await request.get(`${api}/courses/${ownerCourse}/documents`, { headers: newHeaders })).status()).toBe(404)
    await page.reload()
    await expect(page.locator('.account')).toContainText(emails[1])
    await page.getByRole('button', { name: '创建第一门课程' }).click()
    await page.getByLabel('新课程名称').fill('注册后的第一门课程')
    await page.getByRole('button', { name: '创建', exact: true }).click()
    await expect(page.getByRole('button', { name: '注册后的第一门课程', exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: '另一个账号的私人课程', exact: true })).toHaveCount(0)
    expect((await (await request.get(`${api}/courses`, { headers: ownerHeaders })).json()).map((course: { id: string }) => course.id)).toEqual([ownerCourse])
    await page.screenshot({ path: testInfo.outputPath('new-account-course.png'), fullPage: true })

    await page.getByRole('button', { name: '退出', exact: true }).click()
    await page.getByRole('button', { name: '注册', exact: true }).click()
    await page.getByLabel('邮箱', { exact: true }).fill(emails[1].toUpperCase())
    await page.getByLabel('密码', { exact: true }).fill(password)
    await page.getByLabel('确认密码', { exact: true }).fill(password)
    await page.getByRole('button', { name: '注册并进入学习空间' }).click()
    await expect(page.getByRole('alert')).toHaveText('该邮箱已注册，请直接登录')
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await page.getByLabel('密码', { exact: true }).fill(password)
    await page.getByRole('button', { name: '进入学习空间', exact: true }).click()
    await expect(page.getByRole('button', { name: '注册后的第一门课程', exact: true })).toBeVisible()

    const concurrent = await Promise.all([
      request.post(`${api}/auth/register`, { data: { email: emails[2], password } }),
      request.post(`${api}/auth/register`, { data: { email: emails[2], password } }),
    ])
    expect(concurrent.map(response => response.status()).sort()).toEqual([201, 409])
  } finally {
    const backend = fileURLToPath(new URL('../../backend/', import.meta.url))
    const python = fileURLToPath(new URL('../../backend/.venv/Scripts/python.exe', import.meta.url))
    execFileSync(python, ['-c', `
import json, sys
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from app.db.models import Course, User
from app.db.session import get_engine
emails = json.loads(sys.argv[1])
assert len(emails) == 3 and all(email.startswith('registration-e2e-') for email in emails)
with Session(get_engine()) as db, db.begin():
    ids = list(db.scalars(select(User.id).where(User.email.in_(emails))))
    db.execute(delete(Course).where(Course.owner_id.in_(ids)))
    db.execute(delete(User).where(User.id.in_(ids)))
print('Temporary registration accounts and courses cleaned up.')
`, JSON.stringify(emails)], { cwd: backend, stdio: 'pipe' })
  }
})
