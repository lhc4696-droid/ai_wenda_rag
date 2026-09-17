import { test, expect } from '@playwright/test'
import { mockHistory } from './history-fixture.js'

test.beforeEach(async ({ page }) => {
  await mockHistory(page)
})

for (const mode of ['login', 'register', 'reset']) {
  test(`${mode}：密码至少 8 位，不接受 7 位或全空格`, async ({ page }) => {
    await page.route('**/api/auth/me', (route) =>
      route.fulfill({ status: 401, json: { detail: '请先登录' } }),
    )
    await page.route('**/api/health', (route) => route.fulfill({ json: { status: 'ok' } }))
    const endpoint = mode === 'reset' ? 'password/reset' : mode
    let calls = 0
    await page.route(`**/api/auth/${endpoint}`, (route) => {
      calls += 1
      const data = route.request().postDataJSON()
      expect(data[mode === 'reset' ? 'new_password' : 'password']).toBe('abcd1234')
      return mode === 'reset'
        ? route.fulfill({ json: { message: '密码已重置，请登录。' } })
        : route.fulfill({
            status: mode === 'register' ? 201 : 200,
            json: { user: { id: 1, username: 'password_user' } },
          })
    })
    await page.goto('/')
    if (mode === 'register')
      await page.getByRole('button', { name: '创建账号', exact: true }).click()
    if (mode === 'reset') await page.getByRole('button', { name: '忘记密码？' }).click()
    if (mode !== 'reset') await page.getByLabel('用户名', { exact: true }).fill('password_user')
    if (mode !== 'login') {
      await page.getByLabel('邮箱', { exact: true }).fill('password@example.com')
      await page.getByLabel('邮箱验证码').fill('345678')
      await page.getByLabel('确认密码').fill('abcd1234')
    }
    await expect(page.locator('#password-hint')).toContainText('8–128')
    const password = page.getByLabel(mode === 'reset' ? '新密码' : '密码', { exact: true })
    const submit = page.getByRole('button', {
      name: mode === 'reset' ? '重置密码' : mode === 'register' ? '创建账号并进入' : '登录并进入',
      exact: true,
    })
    for (const invalid of ['abc1234', '        ']) {
      await password.fill(invalid)
      await submit.click()
      await expect(page.getByRole('alert')).toContainText('密码需要 8–128 个字符，不能全部为空格')
      expect(calls).toBe(0)
    }
    await password.fill('abcd1234')
    await submit.click()
    if (mode === 'reset') await expect(page.locator('.auth-notice')).toContainText('密码已重置')
    else await expect(page.getByRole('textbox', { name: '输入你的问题' })).toBeVisible()
    expect(calls).toBe(1)
  })
}

test('未登录时显示登录页，注册后进入并恢复会话，退出清空聊天', async ({ page }, testInfo) => {
  let user = null
  await page.route('**/api/auth/me', (route) =>
    user
      ? route.fulfill({ json: { user } })
      : route.fulfill({ status: 401, json: { detail: '请先登录' } }),
  )
  await page.route('**/api/auth/register', (route) => {
    const data = route.request().postDataJSON()
    expect(data.password).toBe('New-password-2026!')
    expect(data.email).toBe('xiaoming@example.com')
    expect(data.code).toBe('003821')
    user = { id: 1, username: data.username, email: data.email }
    return route.fulfill({ status: 201, json: { user } })
  })
  await page.route('**/api/auth/email-code', (route) => {
    expect(route.request().postDataJSON()).toEqual({
      email: 'xiaoming@example.com',
      purpose: 'register',
    })
    return route.fulfill({
      status: 202,
      json: { message: '请求已受理，请检查邮箱。', retry_after: 60, expires_in: 300 },
    })
  })
  await page.route('**/api/auth/logout', (route) => {
    user = null
    return route.fulfill({ status: 204 })
  })
  await page.route('**/api/health', (route) => route.fulfill({ json: { status: 'ok' } }))
  await mockHistory(page, (route) =>
    route.fulfill({ json: { role: 'assistant', content: '你的聊天内容', model: 'qwen-plus' } }),
  )
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '欢迎回到知言' })).toBeVisible()
  await expect(page.getByRole('textbox', { name: '输入你的问题' })).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('login.png'), fullPage: true })
  await page.getByRole('button', { name: '创建账号', exact: true }).click()
  await page.getByLabel('用户名', { exact: true }).fill('xiaoming')
  await page.getByLabel('密码', { exact: true }).fill('New-password-2026!')
  await page.getByLabel('确认密码').fill('Different-password!')
  await page.getByRole('button', { name: '创建账号并进入' }).click()
  await expect(page.getByRole('alert')).toContainText('两次输入的密码不一致')
  await page.getByLabel('确认密码').fill('New-password-2026!')
  await page.getByLabel('邮箱', { exact: true }).fill('xiaoming@example.com')
  await page.getByRole('button', { name: '发送验证码', exact: true }).click()
  await expect(page.getByRole('button', { name: /秒后重发/ })).toBeDisabled()
  await page.getByLabel('邮箱验证码').fill('003821')
  await page.screenshot({ path: testInfo.outputPath('register.png'), fullPage: true })
  await page.getByRole('button', { name: '创建账号并进入' }).click()
  await expect(page.getByRole('textbox', { name: '输入你的问题' })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('textbox', { name: '输入你的问题' })).toBeVisible()
  await page.getByRole('textbox', { name: '输入你的问题' }).fill('这是一条个人消息')
  await page.getByRole('button', { name: '发送消息' }).click()
  await expect(page.locator('.message--assistant')).toContainText('你的聊天内容')
  await expect(page.locator('.profile-name')).toHaveText('xiaoming')
  await page.getByRole('button', { name: /^账号菜单：/ }).click()
  await page.getByRole('button', { name: '退出登录' }).click()
  await expect(page.getByRole('heading', { name: '欢迎回到知言' })).toBeVisible()
  await expect(page.locator('.message')).toHaveCount(0)
  expect(await page.evaluate(() => localStorage.length)).toBe(0)
})

test('忘记密码：邮箱验证、错误重试和重置后返回登录', async ({ page }, testInfo) => {
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({ status: 401, json: { detail: '请先登录' } }),
  )
  await page.route('**/api/auth/email-code', (route) => {
    expect(route.request().postDataJSON()).toEqual({
      email: 'owner@example.com',
      purpose: 'reset_password',
    })
    return route.fulfill({
      status: 202,
      json: {
        message: '请求已受理，若邮箱符合条件你将收到验证码。',
        retry_after: 60,
        expires_in: 300,
      },
    })
  })
  let resetCalls = 0
  await page.route('**/api/auth/password/reset', (route) => {
    resetCalls += 1
    expect(route.request().postDataJSON()).toEqual({
      email: 'owner@example.com',
      code: resetCalls === 1 ? '000000' : '003821',
      new_password: 'Replacement-password-2026!',
    })
    return resetCalls === 1
      ? route.fulfill({ status: 400, json: { detail: '验证码无效或已过期' } })
      : route.fulfill({
          json: { message: '密码已重置，所有设备的旧登录已失效。请使用新密码登录。' },
        })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '忘记密码？' }).click()
  await expect(page.getByRole('heading', { name: '找回你的知言账号' })).toBeVisible()
  await expect(page.getByLabel('用户名', { exact: true })).toHaveCount(0)
  await page.getByLabel('邮箱', { exact: true }).fill('owner@example.com')
  await page.getByRole('button', { name: '发送验证码', exact: true }).click()
  await expect(page.getByRole('button', { name: /秒后重发/ })).toBeDisabled()
  await page.getByLabel('邮箱验证码').fill('000000')
  await page.getByLabel('新密码', { exact: true }).fill('Replacement-password-2026!')
  await page.getByLabel('确认密码').fill('Replacement-password-2026!')
  await page.getByRole('button', { name: '重置密码', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('验证码无效')
  await page.getByLabel('邮箱验证码').fill('003821')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('reset-password.png'), fullPage: true })
  await page.getByRole('button', { name: '重置密码', exact: true }).click()
  await expect(page.getByRole('heading', { name: '欢迎回到知言' })).toBeVisible()
  await expect(page.locator('.auth-notice')).toContainText('密码已重置')
  await expect(page.getByLabel('密码', { exact: true })).toHaveValue('')
  expect(await page.evaluate(() => localStorage.length)).toBe(0)
})

test('发送配置错误、限流及邮箱或用途切换不会混用验证码', async ({ page }) => {
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({ status: 401, json: { detail: '请先登录' } }),
  )
  let calls = 0
  await page.route('**/api/auth/email-code', (route) => {
    calls += 1
    return calls === 1
      ? route.fulfill({ status: 503, json: { detail: '邮件服务尚未配置' } })
      : route.fulfill({
          status: 429,
          headers: { 'Retry-After': '90' },
          json: { detail: '验证码请求过于频繁' },
        })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '创建账号', exact: true }).click()
  await page.getByLabel('邮箱', { exact: true }).fill('owner@example.com')
  await page.getByRole('button', { name: '发送验证码', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('尚未配置')
  await page.getByRole('button', { name: '发送验证码', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('过于频繁')
  await expect(page.getByRole('button', { name: /秒后重发/ })).toBeDisabled()
  await page.getByLabel('邮箱验证码').fill('123456')
  await page.getByLabel('邮箱', { exact: true }).fill('other@example.com')
  await expect(page.getByLabel('邮箱验证码')).toHaveValue('')
  await page.getByLabel('邮箱', { exact: true }).fill('owner@example.com')
  await expect(page.getByRole('button', { name: /秒后重发/ })).toBeDisabled()
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByRole('button', { name: '忘记密码？' }).click()
  await expect(page.getByLabel('邮箱验证码')).toHaveValue('')
  await expect(page.getByRole('button', { name: '发送验证码', exact: true })).toBeEnabled()
})

test('密码错误保持表单，成功登录后会话失效会回到登录页', async ({ page }) => {
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({ status: 401, json: { detail: '请先登录' } }),
  )
  let calls = 0
  await page.route('**/api/auth/login', (route) => {
    calls += 1
    return calls === 1
      ? route.fulfill({ status: 401, json: { detail: '用户名或密码错误' } })
      : route.fulfill({ json: { user: { id: 2, username: 'tester' } } })
  })
  await page.route('**/api/health', (route) => route.fulfill({ json: { status: 'ok' } }))
  await mockHistory(page, (route) => route.fulfill({ status: 401, json: { detail: '登录过期' } }))
  await page.goto('/')
  await page.getByLabel('用户名', { exact: true }).fill('tester')
  await page.getByLabel('密码', { exact: true }).fill('Example-password!')
  await page.getByRole('button', { name: '显示密码' }).click()
  await expect(page.getByLabel('密码', { exact: true })).toHaveAttribute('type', 'text')
  await page.getByRole('button', { name: '登录并进入' }).click()
  await expect(page.getByRole('alert')).toContainText('用户名或密码错误')
  await page.getByRole('button', { name: '登录并进入' }).click()
  await page.getByRole('textbox', { name: '输入你的问题' }).fill('过期请求')
  await page.getByRole('button', { name: '发送消息' }).click()
  await expect(page.getByRole('heading', { name: '欢迎回到知言' })).toBeVisible()
  await expect(page.getByRole('status')).toContainText('登录已过期')
  await expect(page.locator('.message')).toHaveCount(0)
})

test('后端不可用时显示连接错误，可以重试', async ({ page }) => {
  let available = false
  await page.route('**/api/auth/me', (route) =>
    available
      ? route.fulfill({ status: 401, json: { detail: '请先登录' } })
      : route.fulfill({ status: 502, body: 'Bad Gateway' }),
  )
  await page.goto('/')
  await expect(page.getByRole('alert')).toContainText('没有返回有效数据')
  available = true
  await page.getByRole('button', { name: '重新连接' }).click()
  await expect(page.getByRole('heading', { name: '欢迎回到知言' })).toBeVisible()
})
