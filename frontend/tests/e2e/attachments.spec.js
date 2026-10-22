import { test, expect } from '@playwright/test'
import { mockHistory } from './history-fixture.js'

test('上传文档、绑定问题、刷新保留私有附件下载入口', async ({ page }) => {
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({ json: { user: { id: 1, username: 'file_user' } } }),
  )
  await page.route('**/api/health', (route) => route.fulfill({ json: { status: 'ok' } }))
  const item = {
    id: 'f'.repeat(32),
    filename: 'notes.txt',
    kind: 'document',
    media_type: 'text/plain',
    size: 12,
    status: 'ready',
    warning: '',
    error_message: '',
  }
  const files = new Map([[item.id, item]])
  await mockHistory(
    page,
    (route) => {
      expect(route.request().postDataJSON().attachment_ids).toEqual([item.id])
      return route.fulfill({ json: { content: '文件总结完成', model: 'test' } })
    },
    files,
  )
  await page.route('**/api/attachments', (route) => {
    expect(route.request().headers()['content-type']).toContain('multipart/form-data; boundary=')
    return route.fulfill({ status: 201, json: item })
  })
  await page.goto('/')
  await expect(page.getByRole('button', { name: '添加附件' })).toBeEnabled()
  await page.getByLabel('选择聊天附件').setInputFiles({
    name: 'notes.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('private text'),
  })
  await expect(page.locator('.attachment-drafts')).toContainText('正文已提取')
  await page.getByRole('textbox', { name: '输入你的问题' }).fill('帮我总结文件')
  await page.getByRole('button', { name: '发送消息' }).click()
  await expect(page.locator('.message--assistant')).toContainText('文件总结完成')
  await expect(page.locator('.attachment-drafts')).toHaveCount(0)
  await expect(page.locator('.message-attachments')).toContainText('notes.txt')
  await page.reload()
  await expect(page.locator('.message-attachments')).toContainText('notes.txt')
  // This suite mocks page APIs. Native download response/filename behavior is
  // covered against the real offline HTTP backend in streaming.spec.js.
  const download = page.locator('.message-attachments a')
  await expect(download).toHaveText('notes.txt')
  await expect(download).toHaveAttribute('href', `/api/attachments/${item.id}/content`)
  await expect(download).toHaveAttribute('download', '')
})
