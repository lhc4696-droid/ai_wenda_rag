// 真实浏览器 -> Vite 代理 -> FastAPI -> 临时 SQLite。不调用千问。
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { mkdtemp, readFile, unlink, rmdir } from 'node:fs/promises'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium, expect } from '@playwright/test'
import { createServer } from 'vite'

const frontendDir = fileURLToPath(new URL('../../', import.meta.url))
const projectDir = path.dirname(frontendDir.replace(/[\\/]$/, ''))
const temporaryDir = await mkdtemp(path.join(tmpdir(), 'zhiyan-auth-test-'))
const database = path.join(temporaryDir, 'auth.sqlite3')
let backend
let vite
let browser
let output = ''
const password = 'abcd1234'
const newPassword = 'newpass8'

async function deliveredCode(purpose) {
  let result
  await expect
    .poll(async () => {
      try {
        const codes = JSON.parse(await readFile(path.join(temporaryDir, 'outbox.json'), 'utf8'))
        result = codes.findLast(
          (item) => item.purpose === purpose && item.email === 'integration@example.com',
        )?.code
        return Boolean(result)
      } catch {
        return false
      }
    })
    .toBe(true)
  return result
}

async function availablePort() {
  const server = createNetServer()
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const port = server.address().port
  await new Promise((resolve) => server.close(resolve))
  return port
}

try {
  const backendPort = await availablePort()
  const frontendPort = await availablePort()
  const baseURL = `http://127.0.0.1:${frontendPort}`
  const backendURL = `http://127.0.0.1:${backendPort}`
  const python =
    process.platform === 'win32'
      ? path.join(projectDir, 'venv', 'Scripts', 'python.exe')
      : path.join(projectDir, 'venv', 'bin', 'python')
  backend = spawn(
    python,
    [
      '-m',
      'uvicorn',
      'tests.integration_app:app',
      '--app-dir',
      path.join(projectDir, 'backend'),
      '--host',
      '127.0.0.1',
      '--port',
      String(backendPort),
    ],
    {
      cwd: projectDir,
      windowsHide: true,
      env: {
        ...process.env,
        ZHIYAN_TEST_ROOT: temporaryDir,
        AUTH_DB_PATH: database,
        DASHSCOPE_API_KEY: '',
        CORS_ORIGINS: baseURL,
        SESSION_COOKIE_SECURE: 'false',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  )
  backend.on('error', (error) => {
    output += error.message
  })
  backend.stdout.on('data', (data) => {
    output = (output + data.toString()).slice(-4000)
  })
  backend.stderr.on('data', (data) => {
    output = (output + data.toString()).slice(-4000)
  })
  let ready = false
  for (let i = 0; i < 100; i += 1) {
    try {
      ready = (await fetch(`${backendURL}/api/health`, { signal: AbortSignal.timeout(500) })).ok
    } catch {
      /* 等待本次测试启动的服务 */
    }
    if (ready) break
    if (backend.exitCode !== null) throw new Error(output)
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  assert.ok(ready, `测试后端未就绪：${output}`)
  vite = await createServer({
    configFile: path.join(frontendDir, 'vite.config.js'),
    root: frontendDir,
    logLevel: 'error',
    server: {
      port: frontendPort,
      host: '127.0.0.1',
      strictPort: true,
      proxy: { '/api': { target: backendURL, changeOrigin: true } },
    },
  })
  await vite.listen()
  browser = await chromium.launch({
    channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge',
    headless: true,
  })
  const context = await browser.newContext()
  const page = await context.newPage()
  await page.goto(baseURL)
  await expect(page.getByRole('heading', { name: '欢迎回到知言' })).toBeVisible()
  assert.equal(
    (
      await page.request.post(`${baseURL}/api/chat`, {
        data: { messages: [{ role: 'user', content: 'test' }] },
      })
    ).status(),
    401,
  )
  await page.getByRole('button', { name: '创建账号', exact: true }).click()
  await page.getByLabel('用户名', { exact: true }).fill('integration_user')
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码').fill(password)
  await page.getByLabel('邮箱', { exact: true }).fill('integration@example.com')
  await page.getByRole('button', { name: '发送验证码', exact: true }).click()
  await expect(page.locator('.auth-notice')).toContainText('请求已受理')
  await page.getByLabel('邮箱验证码').fill(await deliveredCode('register'))
  await page.getByRole('button', { name: '创建账号并进入' }).click()
  await expect(page.getByRole('textbox', { name: '输入你的问题' })).toBeVisible()
  const cookie = (await context.cookies(`${baseURL}/api`)).find(
    (item) => item.name === 'zhiyan_session',
  )
  assert.ok(cookie?.httpOnly)
  assert.ok(!(await page.evaluate(() => document.cookie)).includes('zhiyan_session'))
  assert.equal((await page.request.get(`${baseURL}/api/auth/me`)).status(), 200)
  await page.reload()
  await expect(page.locator('.profile-name')).toHaveText('integration_user')

  // 已登录可以进入聊天处理，但临时后端没有 API Key，保证不产生模型费用。
  const chat = await page.request.post(`${baseURL}/api/chat`, {
    data: { messages: [{ role: 'user', content: 'test' }] },
  })
  assert.equal(chat.status(), 503)
  assert.match((await chat.json()).detail, /DASHSCOPE_API_KEY/)
  await page.getByRole('button', { name: /^账号菜单：/ }).click()
  await page.getByRole('button', { name: '退出登录' }).click()
  await expect(page.getByRole('heading', { name: '欢迎回到知言' })).toBeVisible()
  assert.equal((await page.request.get(`${baseURL}/api/auth/me`)).status(), 401)
  const replay = await fetch(`${backendURL}/api/auth/me`, {
    headers: { Cookie: `zhiyan_session=${cookie.value}` },
  })
  assert.equal(replay.status, 401)
  await page.getByLabel('用户名', { exact: true }).fill('integration_user')
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '登录并进入' }).click()
  await expect(page.getByRole('textbox', { name: '输入你的问题' })).toBeVisible()
  const beforeReset = (await context.cookies(`${baseURL}/api`)).find(
    (item) => item.name === 'zhiyan_session',
  )
  // 第二个浏览器上下文重置密码，验证第一个设备的旧 Cookie 在服务端被撤销。
  const recoveryContext = await browser.newContext()
  const recovery = await recoveryContext.newPage()
  await recovery.goto(baseURL)
  await recovery.getByRole('button', { name: '忘记密码？' }).click()
  await recovery.getByLabel('邮箱', { exact: true }).fill('integration@example.com')
  await recovery.getByRole('button', { name: '发送验证码', exact: true }).click()
  await expect(recovery.locator('.auth-notice')).toContainText('请求已受理')
  await recovery.getByLabel('邮箱验证码').fill(await deliveredCode('reset_password'))
  await recovery.getByLabel('新密码', { exact: true }).fill(newPassword)
  await recovery.getByLabel('确认密码').fill(newPassword)
  await recovery.getByRole('button', { name: '重置密码', exact: true }).click()
  await expect(recovery.getByRole('heading', { name: '欢迎回到知言' })).toBeVisible()
  await expect(recovery.locator('.auth-notice')).toContainText('密码已重置')
  assert.equal(
    (
      await fetch(`${backendURL}/api/auth/me`, {
        headers: { Cookie: `zhiyan_session=${beforeReset.value}` },
      })
    ).status,
    401,
  )
  assert.equal(
    (
      await recovery.request.post(`${baseURL}/api/auth/login`, {
        data: { username: 'integration_user', password },
      })
    ).status(),
    401,
  )
  await recovery.getByLabel('用户名', { exact: true }).fill('integration_user')
  await recovery.getByLabel('密码', { exact: true }).fill(newPassword)
  await recovery.getByRole('button', { name: '登录并进入' }).click()
  await expect(recovery.getByRole('textbox', { name: '输入你的问题' })).toBeVisible()
  await recoveryContext.close()
  assert.equal(await page.evaluate(() => localStorage.length), 0)
  console.log(
    'PASS: 邮箱验证码注册、Cookie、刷新恢复、鉴权、退出、忘记密码及跨设备会话撤销；模拟邮件、临时数据库独立。',
  )
} finally {
  await browser?.close()
  await vite?.close()
  if (backend && backend.exitCode === null) {
    const exited = once(backend, 'exit')
    backend.kill()
    await exited
  }
  // 只清理本测试生成的三个精确文件和其空目录，不递归删除目录。
  for (const suffix of ['', '-wal', '-shm']) {
    await unlink(database + suffix).catch((error) => {
      if (error.code !== 'ENOENT') throw error
    })
  }
  for (const name of ['outbox.json', 'outbox.pending']) {
    await unlink(path.join(temporaryDir, name)).catch((error) => {
      if (error.code !== 'ENOENT') throw error
    })
  }
  await rmdir(temporaryDir)
}
