import { test, expect } from '@playwright/test'
import { mockHistory } from './history-fixture.js'

test('无文件且自动模式拿不准时先选择，不会提前发送', async ({ page }) => {
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({ json: { user: { id: 1, username: 'route_user' } } }),
  )
  await page.route('**/api/health', (route) => route.fulfill({ json: { status: 'ok' } }))
  const state = await mockHistory(page)
  await page.goto('/')
  await page.getByRole('textbox', { name: '输入你的问题' }).fill('那它是什么意思？')
  await page.getByRole('button', { name: '发送消息' }).click()
  await expect(page.getByRole('dialog', { name: '这次需要查资料吗？' })).toBeVisible()
  expect(state.conversations.size).toBe(0)
  await page.getByRole('button', { name: /直接聊天.*不查询 Qdrant/ }).click()
  await expect(page.locator('.message--assistant')).toContainText('模拟回答')
  expect(state.conversations.size).toBe(1)
})

test('显式资料模式没有文件时引导上传且保留草稿', async ({ page }) => {
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({ json: { user: { id: 1, username: 'route_user' } } }),
  )
  await page.route('**/api/health', (route) => route.fulfill({ json: { status: 'ok' } }))
  const state = await mockHistory(page)
  await page.goto('/')
  await page.getByRole('group', { name: '资料使用模式' }).getByRole('button', { name: '查资料' }).click()
  await page.getByRole('textbox', { name: '输入你的问题' }).fill('根据资料回答')
  await page.getByRole('button', { name: '发送消息' }).click()
  await expect(page.getByRole('alert')).toContainText('请先上传文字文档')
  await expect(page.getByRole('textbox', { name: '输入你的问题' })).toHaveValue('根据资料回答')
  expect(state.conversations.size).toBe(0)
})
