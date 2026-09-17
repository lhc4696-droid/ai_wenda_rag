import { test, expect } from '@playwright/test'
import { mockHistory } from './history-fixture.js'

test('账号设置改名和主动改密均保留登录、当前聊天与草稿', async ({ page }) => {
  let user = { id: 1, username: 'account_user', email: 'owner@example.com' }
  await page.route('**/api/auth/me', (route) =>
    user
      ? route.fulfill({ json: { user } })
      : route.fulfill({ status: 401, json: { detail: '请先登录' } }),
  )
  await page.route('**/api/health', (route) => route.fulfill({ json: { status: 'ok' } }))
  await mockHistory(page, (route) =>
    route.fulfill({ json: { role: 'assistant', content: '这段对话需要保留', model: 'test' } }),
  )
  await page.route('**/api/chat/usage', (route) =>
    route.fulfill({ json: { requests: 1, succeeded: 1, daily_limit: 100, resets_at: 2000000000 } }),
  )
  await page.route('**/api/auth/username', (route) => {
    const data = route.request().postDataJSON()
    expect(data).toEqual({ username: 'new_name' })
    user = { ...user, username: data.username }
    return route.fulfill({ json: { user } })
  })
  await page.route('**/api/auth/password/change', (route) => {
    expect(route.request().postDataJSON()).toEqual({
      current_password: 'abcd1234',
      new_password: 'newpass8',
    })
    return route.fulfill({
      json: { message: '密码已修改，当前浏览器保持登录，其他设备的旧登录已失效。' },
    })
  })
  await page.goto('/')
  await page.getByRole('textbox', { name: '输入你的问题' }).fill('保留我的对话')
  await page.getByRole('button', { name: '发送消息' }).click()
  await expect(page.locator('.message--assistant')).toContainText('这段对话需要保留')
  await page.getByRole('button', { name: /^账号菜单：/ }).click()
  await page.getByRole('button', { name: '账号设置', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '账号设置' })).toBeVisible()
  await page.getByLabel('新用户名', { exact: true }).fill('new_name')
  await expect(page.locator('#account-panel-profile input[type="password"]')).toHaveCount(0)
  await page.getByRole('button', { name: '保存用户名' }).click()
  await expect(page.locator('.account-dialog .auth-notice')).toContainText('用户名已更新')
  await page.getByRole('button', { name: '关闭账号设置' }).click()
  await expect(page.locator('.message--assistant')).toContainText('这段对话需要保留')
  await expect(page.getByRole('button', { name: '账号菜单：new_name' })).toBeVisible()
  await page.getByRole('textbox', { name: '输入你的问题' }).fill('保留未发送的草稿')
  await page.getByRole('button', { name: /^账号菜单：/ }).click()
  await page.getByRole('button', { name: '账号设置', exact: true }).click()
  await page.getByRole('tab', { name: '安全设置' }).click()
  await expect(page.getByRole('tab', { name: '安全设置' })).toHaveAttribute('aria-selected', 'true')
  await page.getByLabel('当前密码', { exact: true }).fill('abcd1234')
  await page.getByLabel('新密码', { exact: true }).fill('newpass8')
  await page.getByLabel('确认新密码', { exact: true }).fill('newpass8')
  await page.getByRole('button', { name: '保存新密码' }).click()
  await expect(page.locator('.account-dialog .auth-notice')).toContainText('当前浏览器保持登录')
  await expect(page.getByLabel('当前密码', { exact: true })).toHaveValue('')
  await expect(page.getByLabel('新密码', { exact: true })).toHaveValue('')
  await expect(page.getByLabel('确认新密码', { exact: true })).toHaveValue('')
  await page.getByRole('button', { name: '关闭账号设置' }).click()
  await expect(page.getByRole('heading', { name: '欢迎回到知言' })).toHaveCount(0)
  await expect(page.locator('.message--assistant')).toContainText('这段对话需要保留')
  await expect(page.getByRole('textbox', { name: '输入你的问题' })).toHaveValue('保留未发送的草稿')
})
