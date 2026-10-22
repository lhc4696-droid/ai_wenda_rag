import test from 'node:test'
import assert from 'node:assert/strict'
import { usernameError } from '../src/lib/account-validation.js'

test('用户名允许单字、中文、多语言、标点、内部空格和表情', () => {
  for (const name of ['唯', 'a', 'ab', '平泽唯', 'Alice·陈-01', '小 明', 'Élodie',
    '山田さん', 'مرحبا', '🙂', '👩‍💻', '❤\ufe0f', 'e\u0301', "<小明>&'", ' \u3000平泽唯 \u3000', ' new_name ']) {
    assert.equal(usernameError(name), '', JSON.stringify(name))
  }
})

test('32 个 Unicode 码点以内可用，表情不按 UTF-16 长度误判', () => {
  for (const name of ['中'.repeat(32), '🙂'.repeat(32)]) assert.equal(usernameError(name), '')
  for (const name of ['中'.repeat(33), '🙂'.repeat(33)]) assert.ok(usernameError(name))
})

test('拒绝空白、换行、控制字符、不可见占位和孤立代理码', () => {
  for (const name of ['', ' \u3000 ', '\n小明', '小明\n', '小\t明', '小\x00明',
    '小\x7f明', '小\u0085明', '小\u2028明', '小\u2029明', '小\u202e明',
    '小\u2066明', '小\u200b明', '\ufeff小明', '\ud800', '\u200d', '\u0301',
    '\ufe0f', '\u200d小明', '小明\u200d', '\u115f', '\u1160', '\u3164', '\uffa0', '\u2800']) {
    assert.ok(usernameError(name), JSON.stringify(name))
  }
})
