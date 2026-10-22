import { test, expect } from '@playwright/test'
import { mockHistory } from './history-fixture.js'

test('横屏低高度窗口中欢迎卡片与输入框保持分离，可滚动到输入框', async ({ page }) => {
  await page.setViewportSize({ width: 844, height: 420 })
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await mockHistory(page)
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({ json: { user: { id: 1, username: 'layout_user' } } }),
  )
  await page.route('**/api/health', (route) => route.fulfill({ json: { status: 'ok' } }))
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '今天想聊些什么？' })).toBeVisible()
  const card = await page.getByRole('button', { name: /整理我的思路/ }).boundingBox()
  const input = page.getByRole('textbox', { name: '输入你的问题' })
  const composer = await page.locator('.composer').boundingBox()
  expect(card.y + card.height).toBeLessThanOrEqual(composer.y)
  await input.fill('横屏也可以正常输入')
  await expect(input).toBeInViewport()
  await expect(page.getByRole('button', { name: '发送消息' })).toBeInViewport()
})

test('窄屏编程面板完整显示，并能从面板内关闭', async ({ page }) => {
  await page.setViewportSize({ width: Math.min(page.viewportSize().width, 820), height: 720 })
  await mockHistory(page)
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({ json: { user: { id: 1, username: 'layout_user' } } }),
  )
  await page.route('**/api/health', (route) => route.fulfill({ json: { status: 'ok' } }))
  await page.route('**/api/coding/local/**', (route) =>
    route.fulfill({
      json: route.request().url().endsWith('/capabilities')
        ? { model: 'test-coder' }
        : { items: [] },
    }),
  )
  await page.goto('/')
  if (page.viewportSize().width <= 760) await page.getByRole('button', { name: '打开导航' }).click()
  await page.getByRole('button', { name: '编程工作台' }).click()
  await expect(page.getByRole('heading', { name: '把想法，写进你的项目' })).toBeVisible()

  await page.locator('#coding-toggle-process').click()
  const process = page.getByRole('complementary', { name: '执行过程', exact: true })
  await expect(process).toBeInViewport({ ratio: 1 })
  expect((await process.boundingBox()).width).toBeGreaterThan(250)
  await process.getByRole('button', { name: '收起执行过程', exact: true }).click()
  await expect(process).toBeHidden()

  if (page.viewportSize().width <= 760) {
    await page.locator('#coding-toggle-files').click()
    const files = page.getByRole('complementary', { name: '项目目录', exact: true })
    await expect(files).toBeInViewport({ ratio: 1 })
    expect((await files.boundingBox()).width).toBeGreaterThan(250)
    await files.getByRole('button', { name: '收起项目目录', exact: true }).click()
    await expect(files).toBeHidden()
  }
  await expect(page.getByRole('textbox', { name: '输入编程需求' })).toBeEnabled()
})
