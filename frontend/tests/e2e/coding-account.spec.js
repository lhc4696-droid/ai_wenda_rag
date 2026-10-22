import { test, expect } from '@playwright/test'
import { mockHistory } from './history-fixture.js'

// Mock-only browser regression source; never calls a real model or directory picker.
async function setup(page, activeTasks = []) {
  let user = { id: 1, username: 'coding_user', email: 'coding@example.com' }
  let profile = {
    storage_ready: true,
    enabled: true,
    has_api_key: true,
    base_url: 'https://models.example.com/v1',
    chat_model: 'chat-before',
    coding_model: 'coding-before',
    vision_model: '',
    default_chat_model: 'default-chat',
    default_coding_model: 'default-coding',
    default_vision_model: '',
  }
  await mockHistory(page)
  await page.route('**/api/auth/me', (route) => route.fulfill({ json: { user } }))
  await page.route('**/api/auth/username', (route) => {
    user = { ...user, username: route.request().postDataJSON().username }
    return route.fulfill({ json: { user } })
  })
  await page.route('**/api/health', (route) => route.fulfill({ json: { status: 'ok' } }))
  await page.route('**/api/chat/usage', (route) =>
    route.fulfill({
      json: { requests: 0, succeeded: 0, daily_limit: 100, resets_at: 2000000000 },
    }),
  )
  await page.route('**/api/attachments/capabilities', (route) =>
    route.fulfill({
      json: {
        extensions: [],
        max_files: 3,
        max_bytes: 10485760,
        images_enabled: false,
        chat_model: profile.chat_model,
      },
    }),
  )
  await page.route('**/api/model-settings', (route) => {
    if (route.request().method() === 'PATCH')
      profile = { ...profile, ...route.request().postDataJSON() }
    return route.fulfill({ json: profile })
  })
  await page.route('**/api/coding/local/**', (route) => {
    const endpoint = new URL(route.request().url()).pathname.split('/').at(-1)
    if (endpoint === 'capabilities')
      return route.fulfill({ json: { model: profile.coding_model, max_steps: 12 } })
    return route.fulfill({ json: { items: endpoint === 'tasks' ? activeTasks : [] } })
  })
  await page.goto('/')
  const entry = page.getByRole('button', { name: '编程工作台' })
  if (!(await entry.isVisible())) await page.getByRole('button', { name: '打开导航' }).click()
  await entry.click()
  await expect(page.locator('.cc-header .model-name')).toHaveText('coding-before')
}

test('编程工作台直接修改模型、账号资料，保留草稿并同步基础聊天', async ({ page }) => {
  await setup(page)
  const shell = page.locator('.coding-chat-shell')
  await shell.locator('textarea').fill('保留我的编程需求')
  await shell.getByRole('button', { name: /^账号菜单：/ }).click()
  await page
    .locator('#coding-account-dropdown')
    .getByRole('button', { name: '模型设置', exact: true })
    .click()
  const dialog = page.getByRole('dialog', { name: '账号设置', exact: true })
  await expect(dialog.getByRole('tab', { name: '模型设置' })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  await dialog.locator('#model-coding').fill('coding-after')
  await dialog.locator('#model-chat').fill('chat-after')
  await dialog.getByRole('button', { name: '保存并使用我的模型' }).click()
  await expect(dialog).toContainText('配置已保存')
  await dialog.getByRole('button', { name: '关闭账号设置' }).click()
  await expect(shell.locator('.model-name')).toHaveText('coding-after')
  await expect(shell.locator('textarea')).toHaveValue('保留我的编程需求')
  await shell.getByRole('button', { name: /^账号菜单：/ }).click()
  await page
    .locator('#coding-account-dropdown')
    .getByRole('button', { name: '账号设置', exact: true })
    .click()
  await dialog.getByLabel('新用户名', { exact: true }).fill('renamed_coder')
  await dialog.getByRole('button', { name: '保存用户名' }).click()
  await expect(dialog).toContainText('用户名已更新')
  await dialog.getByRole('button', { name: '关闭账号设置' }).click()
  await expect(shell.getByRole('button', { name: '账号菜单：renamed_coder' })).toBeVisible()
  await shell.getByRole('button', { name: '返回基础聊天' }).click()
  await expect(
    page.locator('.topbar-right').getByRole('button', { name: '账号菜单：renamed_coder' }),
  ).toBeVisible()
  await expect(page.locator('.app-shell .model-name')).toHaveText('chat-after')
})

test('执行中账号菜单仍可点击，但提示先结束任务再改模型', async ({ page }) => {
  await setup(page, [{ id: 'active-task', status: 'running', instruction: '模拟未结束执行' }])
  const shell = page.locator('.coding-chat-shell')
  await shell.getByRole('button', { name: /^账号菜单：/ }).click()
  const menu = page.locator('#coding-account-dropdown')
  await expect(menu.getByRole('button', { name: '模型设置', exact: true })).toBeDisabled()
  await expect(menu.getByRole('button', { name: '账号设置', exact: true })).toBeDisabled()
  await expect(menu).toContainText('等待结束或先停止')
  await expect(menu.getByRole('button', { name: '退出登录' })).toBeEnabled()
})
