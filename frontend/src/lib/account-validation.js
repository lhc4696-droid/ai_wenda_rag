export function usernameError(value) {
  // Match the backend's Unicode categories; reject controls before trimming.
  if (!/^[\p{L}\p{M}\p{N}\p{P}\p{S}\p{Zs}\u200c\u200d]*$/u.test(value))
    return '用户名不能包含换行、控制字符或不可见格式字符。'
  const name = value.trim()
  const length = Array.from(name).length
  if (length < 1 || length > 32) return '用户名需要 1–32 个字符。'
  const visibleName = name.replace(/[\u115f\u1160\u3164\uffa0\u2800]/gu, '')
  if (!/[\p{L}\p{N}\p{P}\p{S}]/u.test(visibleName) || /^[\u200c\u200d]|[\u200c\u200d]$/u.test(name))
    return '用户名必须包含可见字符，不能全部为空白或不可见字符。'
  return ''
}

export function passwordError(value) {
  const length = Array.from(value).length
  return length >= 8 && length <= 128 && value.trim() ? '' : '密码需要 8–128 个字符，不能全部为空格。'
}
