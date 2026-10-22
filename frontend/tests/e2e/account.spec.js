import { test, expect } from '@playwright/test'
import { mockHistory } from './history-fixture.js'

test('账号统计区分默认额度、个人 API 和服务器向量调用', async ({ page }) => {
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({
      json: { user: { id: 1, username: 'usage_user', email: 'usage@example.com' } },
    }),
  )
  await page.route('**/api/health', (route) => route.fulfill({ json: { status: 'ok' } }))
  await mockHistory(page)
  await page.route('**/api/chat/usage', (route) =>
    route.fulfill({
      json: {
        requests: 3,
        succeeded: 2,
        input_tokens: 50,
        output_tokens: 20,
        daily_limit: 100,
        legacy_requests: 1,
        resets_at: 2000000000,
        custom: {
          requests: 127,
          succeeded: 125,
          input_tokens: 1200,
          output_tokens: 700,
          daily_limit: null,
        },
        embedding: {
          requests: 5,
          succeeded: 5,
          input_tokens: null,
          output_tokens: null,
          daily_limit: 100,
        },
      },
    }),
  )
  await page.goto('/')
  await page.getByRole('button', { name: /^账号菜单：/ }).click()
  await page.getByRole('button', { name: '账号设置', exact: true }).click()
  const defaults = page.getByRole('region', { name: '默认模型调用' })
  const custom = page.getByRole('region', { name: '个人 API 调用' })
  const embedding = page.getByRole('region', { name: '资料向量调用' })
  await expect(defaults).toContainText('3')
  await expect(defaults).toContainText('/ 100 次')
  await expect(custom).toContainText('127 次')
  await expect(custom).toContainText('成功 125 次')
  await expect(custom).toContainText('输入 1200')
  await expect(custom).toContainText('输出 700')
  await expect(custom).not.toContainText('/ 100')
  await expect(custom).toContainText('不扣默认模型额度')
  await expect(embedding).toContainText('5')
  await expect(embedding).toContainText('服务器')
  await expect(page.locator('.account-usage')).toContainText('旧调用 1 次')
})

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
