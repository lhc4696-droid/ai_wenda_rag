export function usernameError(value) {
  return /^[A-Za-z0-9_]{3,32}$/.test(value.trim()) ? '' : '用户名需要 3–32 位英文字母、数字或下划线。'
}

export function passwordError(value) {
  const length = Array.from(value).length
  return length >= 8 && length <= 128 && value.trim() ? '' : '密码需要 8–128 个字符，不能全部为空格。'
}
