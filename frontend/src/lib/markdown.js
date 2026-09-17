import MarkdownIt from 'markdown-it'

const markdown = new MarkdownIt({ html: false, breaks: true, linkify: true })
const defaultLink = markdown.renderer.rules.link_open
markdown.renderer.rules.link_open = (tokens, index, options, env, renderer) => {
  tokens[index].attrSet('target', '_blank')
  tokens[index].attrSet('rel', 'noopener noreferrer')
  return defaultLink
    ? defaultLink(tokens, index, options, env, renderer)
    : renderer.renderToken(tokens, index, options)
}
// 回答中的远程图片显示为链接，不自动加载第三方资源。
markdown.renderer.rules.image = (tokens, index) => {
  const token = tokens[index]
  const src = token.attrGet('src') || ''
  const label = markdown.utils.escapeHtml(token.content || '查看图片')
  if (!/^https?:\/\//i.test(src)) return label
  return `<a href="${markdown.utils.escapeHtml(src)}" target="_blank" rel="noopener noreferrer">${label}</a>`
}

export function renderMarkdown(content) {
  return markdown.render(content)
}
