import { execFileSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'

test('真实服务：创建对话、浏览器重命名、刷新保存、账号隔离与清理', async ({ page, request }) => {
  test.skip(process.env.WORKSPACE_LIVE !== '1', '设置 WORKSPACE_LIVE=1 验证本地服务')
  const email = `workspace-e2e-${randomUUID()}@example.test`
  const api = 'http://127.0.0.1:8000/api/v1'
  try {
    const registered = await request.post(`${api}/auth/register`, { data: { email, password: 'local workspace acceptance password' } })
    expect(registered.status()).toBe(201)
    const token = (await registered.json()).access_token as string
    const headers = { Authorization: `Bearer ${token}` }
    const courseResponse = await request.post(`${api}/courses`, { headers, data: { name: '课程对话界面验收' } })
    expect(courseResponse.status()).toBe(201)
    const course = await courseResponse.json()
    const sessionResponse = await request.post(`${api}/chat/sessions`, { headers, data: { course_id: course.id, document_ids: null } })
    expect(sessionResponse.status()).toBe(201)
    const session = await sessionResponse.json()
    expect(session.title).toBe('新对话')
    await page.addInitScript(value => localStorage.setItem('studyrag-token', value), token)
    await page.goto('/')
    await expect(page.getByLabel('输入问题')).toBeEnabled()
    await page.getByRole('button', { name: '新对话', exact: true }).click({ button: 'right' })
    await page.getByRole('menuitem', { name: '重命名', exact: true }).click()
    await page.getByLabel('对话名称').fill('真实接口保存验收')
    await page.getByRole('button', { name: '保存', exact: true }).click()
    await expect(page.getByRole('heading', { name: '真实接口保存验收', exact: true })).toBeVisible()
    await page.reload()
    await expect(page.getByRole('heading', { name: '真实接口保存验收', exact: true })).toBeVisible()
    await page.getByRole('button', { name: '课程对话界面验收', exact: true }).hover()
    await page.getByRole('button', { name: '在课程对话界面验收中新建对话', exact: true }).click()
    await expect(page.getByRole('heading', { name: '新对话', exact: true })).toBeVisible()
    const sessions = await (await request.get(`${api}/chat/sessions?course_id=${course.id}`, { headers })).json()
    expect(sessions).toHaveLength(2)
    expect(sessions.find((item: { id: string }) => item.id === session.id)).toMatchObject({ title: '真实接口保存验收', title_renamed: true })
    expect((await request.patch(`${api}/chat/sessions/${session.id}`, { data: { title: '无权限更新' } })).status()).toBe(401)
    await page.screenshot({ path: 'test-results/workspace-live.png' })
  } finally {
    const python = fileURLToPath(new URL('../../backend/.venv/Scripts/python.exe', import.meta.url))
    const cwd = fileURLToPath(new URL('../../backend/', import.meta.url))
    execFileSync(python, ['-c', `
import sys
from sqlalchemy import select, delete
from sqlalchemy.orm import Session
from app.db.models import User, Course, ChatSession
from app.db.session import get_engine
email = sys.argv[1]
assert email.startswith('workspace-e2e-') and email.endswith('@example.test')
with Session(get_engine()) as db, db.begin():
    user = db.scalar(select(User).where(User.email == email))
    if user:
        db.execute(delete(ChatSession).where(ChatSession.owner_id == user.id))
        db.execute(delete(Course).where(Course.owner_id == user.id))
        db.delete(user)
print('Temporary workspace acceptance account removed.')
`, email], { cwd, stdio: 'pipe' })
  }
})
