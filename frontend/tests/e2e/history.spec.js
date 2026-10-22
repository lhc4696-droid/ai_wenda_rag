import { test, expect } from '@playwright/test'
import { mockHistory } from './history-fixture.js'

test('两场对话切换、刷新恢复、改名和确认删除', async ({ page }, testInfo) => {
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({ json: { user: { id: 1, username: 'history_user' } } }),
  )
  await page.route('**/api/health', (route) => route.fulfill({ json: { status: 'ok' } }))
  await mockHistory(page, (route) =>
    route.fulfill({
      json: { content: '回答：' + route.request().postDataJSON().content, model: 'test' },
    }),
  )
  async function navigation() {
    if (testInfo.project.name === 'mobile')
      await page.getByRole('button', { name: '打开导航' }).click()
  }
  await page.goto('/')
  const input = page.getByRole('textbox', { name: '输入你的问题' })
  await expect(input).toBeEnabled()
  await input.fill('第一场对话')
  await input.press('Enter')
  await expect(page.locator('.message--assistant')).toContainText('第一场对话')
  await navigation()
  await page.getByRole('button', { name: /^新建对话/ }).click()
  await input.fill('第二场对话')
  await input.press('Enter')
  await expect(page.locator('.message--assistant')).toContainText('第二场对话')
  const composerBefore = await page.locator('.composer').boundingBox()
  let releaseHistory
  const delayedHistory = new Promise((resolve) => {
    releaseHistory = resolve
  })
  await page.route(/\/api\/conversations\/[^/]+\/messages(?:\?.*)?$/, async (route) => {
    if (route.request().method() === 'GET') await delayedHistory
    await route.fallback()
  })
  await navigation()
  await page
    .getByRole('navigation', { name: '历史对话' })
    .getByRole('button', { name: '第一场对话', exact: true })
    .click()
  try {
    await expect(page.locator('.history-skeleton')).toBeVisible()
    await expect(input).toBeDisabled()
    await expect(page.locator('.conversation-heading h1')).toHaveText('第一场对话')
    const during = await page.locator('.composer').boundingBox()
    expect(Math.abs(during.y - composerBefore.y)).toBeLessThan(2)
    const panel = await page.locator('.main-panel').boundingBox()
    const heading = await page.locator('.conversation-heading').boundingBox()
    expect(Math.abs(heading.x + heading.width / 2 - panel.x - panel.width / 2)).toBeLessThan(2)
  } finally {
    releaseHistory()
  }
  await expect(page.locator('.message--assistant')).toContainText('第一场对话')
  await expect(page.locator('.history-skeleton')).toHaveCount(0)
  await expect(input).not.toBeFocused()
  await page.reload()
  await expect(page.locator('.message--assistant')).toContainText('第一场对话')
  await navigation()
  await page.getByRole('button', { name: '重命名对话：第一场对话', exact: true }).click()
  await page.getByLabel('对话标题').fill('改名后的对话')
  await page.getByRole('button', { name: '保存标题', exact: true }).click()
  await expect(page.getByRole('dialog')).toBeHidden()
  await page.getByRole('button', { name: '删除对话：改名后的对话', exact: true }).click()
  await page.getByRole('button', { name: '取消', exact: true }).click()
  await expect(
    page.getByRole('button', { name: '删除对话：改名后的对话', exact: true }),
  ).toBeVisible()
  await page.getByRole('button', { name: '删除对话：改名后的对话', exact: true }).click()
  await page.getByRole('button', { name: '确认删除', exact: true }).click()
  await expect(page.getByRole('dialog')).toBeHidden()
  await expect(page.locator('.history-select')).toHaveCount(1)
  await expect(page.locator('.message')).toHaveCount(0)
})
