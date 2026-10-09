// 这里只对明确线索做判断；拿不准的指代问题交给用户，不增加一次模型路由调用。
const documentWords = /上传的|附件|文档|文件|资料|原文|第\s*\d+\s*页|出处|引用来源|知识库|根据.*(?:手册|报告|PDF)/i
const ambiguousWords = /这个|那个|这份|那份|它|他们|上面|前面|刚才|其中|里面|所说的|这些|那些/i

export function decideRetrievalRoute(question, attachments = [], priorDocuments = false) {
  if (attachments.some((item) => item.kind === 'document')) return 'documents'
  if (documentWords.test(question)) return 'documents'
  if (ambiguousWords.test(question)) return priorDocuments ? 'documents' : 'choice'
  return 'chat'
}
