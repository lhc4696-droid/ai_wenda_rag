import { test, expect } from '@playwright/test'

test.beforeEach(async ({ page }, testInfo) => {
  await page.goto('/')
  await page.getByLabel('用户名').fill(`stream_${testInfo.project.name}`)
  await page.getByLabel('密码', { exact: true }).fill('test-stream-password')
  await page.getByRole('button', { name: '登录并进入', exact: true }).click()
  await expect(page.getByRole('textbox', { name: '输入你的问题' })).toBeVisible()
  if (await page.getByRole('button', { name: '打开导航' }).isVisible())
    await page.getByRole('button', { name: '打开导航' }).click()
  await page.getByRole('button', { name: '新建对话' }).click()
})

test('真实 HTTP 增量在完成前可见，完成和刷新后只有一组已保存消息', async ({ page }, testInfo) => {
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.getByRole('textbox', { name: '输入你的问题' }).fill('请流式回答')
  await page.getByRole('button', { name: '发送消息' }).click()
  await expect(page.locator('.message--assistant')).toContainText('第一段：你好🙂。')
  await expect(page.getByRole('button', { name: '停止生成' })).toBeVisible()
  await expect(page.locator('.message--assistant')).not.toContainText('第二段')
  await page.screenshot({ path: testInfo.outputPath('streaming.png'), fullPage: true })
  await expect(page.locator('.message--assistant')).toContainText('第二段：流式回答完成。')
  await expect(page.getByRole('button', { name: '停止生成' })).toHaveCount(0)
  await expect(page.locator('.markdown-body strong')).toHaveText('第二段：流式回答完成。')
  await page.reload()
  await expect(page.locator('.message--user')).toHaveCount(1)
  await expect(page.locator('.message--assistant')).toHaveCount(1)
  await expect(page.locator('.message--assistant')).toContainText('第二段')
  expect(errors).toEqual([])
})

test('停止真实流释放请求并保留一个可重试的问题', async ({ page }) => {
  await page.getByRole('textbox', { name: '输入你的问题' }).fill('测试停止')
  await page.getByRole('button', { name: '发送消息' }).click()
  await expect(page.locator('.message--assistant')).toContainText('第一段')
  await page.getByRole('button', { name: '停止生成' }).click()
  await expect(page.getByRole('button', { name: '重新尝试' })).toBeVisible({ timeout: 10000 })
  await expect(page.locator('.message--user')).toHaveCount(1)
  await expect(page.locator('.message--assistant')).toHaveCount(0)
  await page.reload()
  await expect(page.getByRole('button', { name: '重新尝试' })).toBeVisible()
})

test('模型中途失败移除半成品，修改后发送能够继续使用', async ({ page }) => {
  await page.getByRole('textbox', { name: '输入你的问题' }).fill('测试失败')
  await page.getByRole('button', { name: '发送消息' }).click()
  await expect(page.locator('.message--assistant')).toContainText('第一段')
  await expect(page.getByRole('alert')).toContainText('模拟模型中断')
  await expect(page.locator('.message--assistant')).toHaveCount(0)
  await page.getByRole('button', { name: '修改后作为新问题' }).click()
  await page.getByRole('textbox', { name: '输入你的问题' }).fill('正常回答')
  await page.getByRole('button', { name: '发送消息' }).click()
  await expect(page.locator('.message--assistant')).toContainText('第二段')
  await expect(page.locator('.message--user')).toHaveCount(2)
})

test('真实附件随流式问题保存，刷新后可下载原文件', async ({ page }) => {
  await expect(page.getByRole('button', { name: '添加附件' })).toBeEnabled()
  await page.getByLabel('选择聊天附件').setInputFiles({
    name: 'notes.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('streaming attachment text'),
  })
  await expect(page.locator('.attachment-drafts')).toContainText('正文已提取')
  await page.getByRole('textbox', { name: '输入你的问题' }).fill('请总结附件')
  await page.getByRole('button', { name: '发送消息' }).click()
  await expect(page.locator('.message--assistant')).toContainText('第二段')
  await expect(page.getByRole('button', { name: '停止生成' })).toHaveCount(0)
  await page.reload()
  await expect(page.locator('.message-attachments')).toContainText('notes.txt')
  const downloaded = page.waitForEvent('download')
  await page.locator('.message-attachments a').click()
  expect((await downloaded).suggestedFilename()).toBe('notes.txt')
})

async function openNavigation(page) {
  const button = page.getByRole('button', { name: '打开导航' })
  if (await button.isVisible()) await button.click()
}

function conversationId(page) {
  return new URL(page.url()).hash.replace('#conversation=', '')
}

test('新建与切换对话保留后台生成，完成后回到原会话可看到已保存回答', async ({ page }) => {
  let calls = 0
  page.on('request', (request) => {
    if (request.method() === 'POST' && new URL(request.url()).pathname.endsWith('/messages/stream'))
      calls++
  })
  await page.getByRole('textbox', { name: '输入你的问题' }).fill('后台继续生成')
  await page.getByRole('button', { name: '发送消息' }).click()
  await expect(page.locator('.message--assistant')).toContainText('第一段')
  const id = conversationId(page)
  await openNavigation(page)
  await page.getByRole('button', { name: '新建对话' }).click()
  await page.getByRole('textbox', { name: '输入你的问题' }).fill('另一会话的草稿')
  await expect
    .poll(
      async () => {
        const response = await page.request.get(`/api/conversations/${id}/messages`)
        return (await response.json()).messages.at(-1)?.status
      },
      { timeout: 15000 },
    )
    .toBe('completed')
  const saved = await (await page.request.get(`/api/conversations/${id}/messages`)).json()
  expect(saved.messages).toHaveLength(2)
  expect(saved.messages[1].content).toContain('第二段')
  await expect(page.getByRole('textbox', { name: '输入你的问题' })).toHaveValue('另一会话的草稿')
  await expect(page.locator('.message')).toHaveCount(0)
  await openNavigation(page)
  await page.getByRole('button', { name: '后台继续生成', exact: true }).click()
  await expect(page.locator('.message--assistant')).toContainText('第二段')
  expect(calls).toBe(1)
})

test('切回原会话可继续看到增量并停止，退出页面会取消后台生成', async ({ page, context }) => {
  await page.getByRole('textbox', { name: '输入你的问题' }).fill('测试停止')
  await page.getByRole('button', { name: '发送消息' }).click()
  await expect(page.locator('.message--assistant')).toContainText('第一段')
  const id = conversationId(page)
  await openNavigation(page)
  await page.getByRole('button', { name: '新建对话' }).click()
  await openNavigation(page)
  await page.locator('.history-select').first().click()
  await expect(page.locator('.message--assistant')).toContainText('第一段')
  await expect(page.getByRole('button', { name: '停止生成' })).toBeVisible()
  await page.getByRole('button', { name: '停止生成' }).click()
  await expect(page.getByRole('button', { name: '重新尝试' })).toBeVisible()
  const stopped = await (await page.request.get(`/api/conversations/${id}/messages`)).json()
  expect(stopped.messages).toHaveLength(1)
  expect(stopped.messages[0].error_code).toBe('REQUEST_CANCELLED')
  await openNavigation(page)
  await page.getByRole('button', { name: '新建对话' }).click()
  await page.getByRole('textbox', { name: '输入你的问题' }).fill('退出时测试停止')
  await page.getByRole('button', { name: '发送消息' }).click()
  await expect(page.locator('.message--assistant')).toContainText('第一段')
  const closingId = conversationId(page)
  await openNavigation(page)
  await page.getByRole('button', { name: '新建对话' }).click()
  await page.close()
  await expect
    .poll(
      async () => {
        const response = await context.request.get(
          `http://127.0.0.1:15173/api/conversations/${closingId}/messages`,
        )
        return (await response.json()).messages[0]?.error_code
      },
      { timeout: 15000 },
    )
    .toBe('REQUEST_CANCELLED')
})
