import { test, expect } from '@playwright/test'
import { mockHistory } from './history-fixture.js'

test.beforeEach(async ({ page }) => {
  await mockHistory(page)
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({ json: { user: { id: 1, username: 'chat_tester' } } }),
  )
  await page.route('**/api/health', (route) => route.fulfill({ json: { status: 'ok' } }))
})

test('欢迎页、输入方式、连续追问和新建对话', async ({ page }, testInfo) => {
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  const calls = []
  await mockHistory(page, async (route) => {
    const payload = route.request().postDataJSON()
    calls.push(payload)
    await route.fulfill({
      json: {
        role: 'assistant',
        content: '**你好，小明。**\n\n```python\nprint("Hello")\n```',
        model: 'qwen-plus',
      },
    })
  })
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('今天想聊些什么？')
  await expect(page.getByRole('button', { name: '服务已连接' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('welcome.png'), fullPage: true })
  const input = page.getByRole('textbox', { name: '输入你的问题' })
  await page.getByRole('button', { name: /理解一个概念/ }).click()
  await expect(input).toHaveValue(/大语言模型/)
  await input.fill('我叫小明')
  await input.dispatchEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true })
  expect(calls).toHaveLength(0)
  await input.press('Shift+Enter')
  await expect(input).toHaveValue('我叫小明\n')
  await input.press('Enter')
  await expect(page.locator('.message--assistant')).toHaveCount(1)
  await expect(page.locator('.markdown-body strong')).toHaveText('你好，小明。')
  await input.fill('我叫什么？')
  await page.getByRole('button', { name: '发送消息' }).click()
  await expect(page.locator('.message--assistant')).toHaveCount(2)
  expect(calls[1].content).toBe('我叫什么？')
  expect(calls[1].expected_last_sequence).toBe(2)
  expect(calls[1]).not.toHaveProperty('messages')
  await page.screenshot({ path: testInfo.outputPath('conversation.png'), fullPage: true })
  if (testInfo.project.name === 'mobile')
    await page.getByRole('button', { name: '打开导航' }).click()
  await page.getByRole('button', { name: '新建对话' }).click()
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  await expect(input).toHaveValue('')
  expect(errors).toEqual([])
})

test('失败提示、修改问题和重试不会重复用户消息', async ({ page }) => {
  let calls = 0
  await mockHistory(page, async (route) => {
    calls += 1
    if (calls <= 2) await route.fulfill({ status: 503, json: { detail: 'AI 服务暂时不可用' } })
    else await route.fulfill({ json: { role: 'assistant', content: '已恢复', model: 'qwen-plus' } })
  })
  await page.goto('/')
  const input = page.getByRole('textbox', { name: '输入你的问题' })
  await input.fill('原始问题')
  await input.press('Enter')
  await expect(page.getByRole('alert')).toContainText('AI 服务暂时不可用')
  await page.getByRole('button', { name: '修改后作为新问题' }).click()
  await expect(input).toHaveValue('原始问题')
  await input.fill('修改后的问题')
  await input.press('Enter')
  await expect(page.getByRole('alert')).toBeVisible()
  await page.getByRole('button', { name: '重新尝试' }).click()
  await expect(page.locator('.message--assistant')).toContainText('已恢复')
  await expect(page.locator('.message--user')).toHaveCount(2)
})

test('等待提示和刷新后恢复聊天', async ({ page }) => {
  let release
  const waitForRelease = new Promise((resolve) => {
    release = resolve
  })
  await mockHistory(page, async (route) => {
    await waitForRelease
    await route.fulfill({ json: { role: 'assistant', content: '完成', model: 'qwen-plus' } })
  })
  await page.goto('/')
  await page.getByRole('textbox').fill('测试等待')
  await page.getByRole('button', { name: '发送消息' }).click()
  await expect(page.locator('.pending-message')).toContainText('正在思考')
  await expect(page.getByRole('button', { name: '发送消息' })).toBeDisabled()
  release()
  await expect(page.locator('.message--assistant')).toHaveCount(1)
  await page.reload()
  await expect(page.locator('.message')).toHaveCount(2)
  await expect(page.locator('.message--assistant')).toContainText('完成')
})

test('连接状态反映健康检查失败且可以重新检查', async ({ page }) => {
  await page.unroute('**/api/health')
  let healthy = false
  await page.route('**/api/health', (route) =>
    healthy
      ? route.fulfill({ json: { status: 'ok' } })
      : route.fulfill({ status: 502, body: 'Bad Gateway' }),
  )
  await page.goto('/')
  await expect(page.getByRole('button', { name: '服务未连接' })).toBeVisible()
  healthy = true
  await page.getByRole('button', { name: '服务未连接' }).click()
  await expect(page.getByRole('button', { name: '服务已连接' })).toBeVisible()
})
