import { test, expect } from '@playwright/test'
import { mockHistory } from './history-fixture.js'

test.beforeEach(async ({ page }) => {
  await mockHistory(page)
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({
      json: {
        user: { id: 1, username: 'viewer_' + 'x'.repeat(25), email: 'viewer@example.com' },
      },
    }),
  )
  await page.route('**/api/health', (route) => route.fulfill({ json: { status: 'ok' } }))
  await page.route('**/api/chat/usage', (route) =>
    route.fulfill({
      json: {
        requests: 3,
        succeeded: 2,
        daily_limit: 100,
        resets_at: 2000000000,
      },
    }),
  )
  await page.goto('/')
})

test('右上角账号入口：点击、外部关闭、长用户名和小屏布局', async ({ page }) => {
  const trigger = page.getByRole('button', { name: /^账号菜单：/ })
  const menu = page.getByRole('group', { name: '账号菜单', exact: true })
  await expect(trigger).toBeVisible()
  expect(await trigger.evaluate((el) => Boolean(el.closest('.topbar-right')))).toBe(true)
  await trigger.click()
  await expect(trigger).toHaveAttribute('aria-expanded', 'true')
  await expect(menu.getByRole('button')).toHaveCount(2)
  await expect(page.locator('.sidebar .profile-name')).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  const box = await menu.boundingBox()
  expect(box.x).toBeGreaterThanOrEqual(0)
  expect(box.x + box.width).toBeLessThanOrEqual(page.viewportSize().width)
  await page.getByRole('textbox', { name: '输入你的问题' }).click()
  await expect(menu).toBeHidden()
  await trigger.click()
  await trigger.click()
  await expect(menu).toBeHidden()
})

test('桌面悬停展开，移动到菜单不消失，离开后收起', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === 'mobile', '触屏使用点击菜单')
  const trigger = page.getByRole('button', { name: /^账号菜单：/ })
  const menu = page.getByRole('group', { name: '账号菜单', exact: true })
  await trigger.hover()
  await expect(menu).toBeVisible()
  await menu.getByRole('button', { name: '账号设置' }).hover()
  await expect(menu).toBeVisible()
  await page.getByRole('heading', { name: '今天想聊些什么？' }).hover()
  await expect(menu).toBeHidden()
})

test('键盘展开、方向键、Esc 与 Tab 离开关闭', async ({ page }) => {
  const trigger = page.getByRole('button', { name: /^账号菜单：/ })
  const menu = page.getByRole('group', { name: '账号菜单', exact: true })
  await trigger.focus()
  await trigger.press('ArrowDown')
  await expect(menu.getByRole('button', { name: '账号设置' })).toBeFocused()
  await page.keyboard.press('ArrowDown')
  await expect(menu.getByRole('button', { name: '退出登录' })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(menu).toBeHidden()
  await expect(trigger).toBeFocused()
  await trigger.press('Enter')
  await page.keyboard.press('End')
  await expect(menu.getByRole('button', { name: '退出登录' })).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(menu).toBeHidden()
})

test('统一设置入口、切换清理密码和关闭后恢复焦点', async ({ page }) => {
  const trigger = page.getByRole('button', { name: /^账号菜单：/ })
  await trigger.click()
  await page.getByRole('button', { name: '账号设置', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '账号设置' })
  const security = dialog.getByRole('tab', { name: '安全设置' })
  const profile = dialog.getByRole('tab', { name: '账号资料' })
  await expect(profile).toHaveAttribute('aria-selected', 'true')
  await security.click()
  await expect(security).toHaveAttribute('aria-selected', 'true')
  await expect(dialog.getByRole('tabpanel', { name: '账号资料' })).toBeHidden()
  await dialog.getByLabel('当前密码', { exact: true }).fill('testpass8')
  await dialog.getByLabel('新密码', { exact: true }).fill('newpass8')
  await security.focus()
  await security.press('ArrowLeft')
  await expect(profile).toBeFocused()
  await expect(profile).toHaveAttribute('aria-selected', 'true')
  await expect(dialog.getByRole('tabpanel', { name: '安全设置' })).toBeHidden()
  await expect(dialog.getByText('viewer@example.com', { exact: true })).toBeVisible()
  await profile.press('ArrowRight')
  await expect(dialog.getByLabel('当前密码', { exact: true })).toHaveValue('')
  await expect(dialog.getByLabel('新密码', { exact: true })).toHaveValue('')
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
  await expect(trigger).toBeFocused()
})

test('模型回答期间不能打开修改表单', async ({ page }) => {
  let release
  const pending = new Promise((resolve) => {
    release = resolve
  })
  await mockHistory(page, async (route) => {
    await pending
    await route.fulfill({ json: { role: 'assistant', content: '回答结束', model: 'test' } })
  })
  try {
    await page.getByRole('textbox', { name: '输入你的问题' }).fill('稍等一下')
    await page.getByRole('button', { name: '发送消息' }).click()
    await expect(page.locator('.pending-message')).toBeVisible()
    await page.getByRole('button', { name: /^账号菜单：/ }).click()
    const menu = page.getByRole('group', { name: '账号菜单', exact: true })
    await expect(menu.getByRole('button', { name: '账号设置' })).toBeDisabled()
    await expect(menu.getByRole('button', { name: '修改密码' })).toHaveCount(0)
    await expect(menu.getByRole('button', { name: '退出登录' })).toBeEnabled()
  } finally {
    release()
  }
  await expect(page.locator('.message--assistant')).toContainText('回答结束')
})
