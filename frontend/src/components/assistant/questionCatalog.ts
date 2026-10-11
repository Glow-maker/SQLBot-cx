export interface QuestionTable { name: string; description: string; question: string }
export interface QuestionCatalog { tables: QuestionTable[]; total: number }

export function parseQuestionCatalog(value: unknown): QuestionCatalog {
  const data = value as QuestionCatalog
  if (!data || !Array.isArray(data.tables) || !Number.isInteger(data.total) || data.total < data.tables.length ||
      data.tables.some(item => !item || typeof item.name !== 'string' || !item.name.trim() ||
        typeof item.question !== 'string' || !item.question.trim() || typeof item.description !== 'string')) {
    throw new Error('无法读取可查询目录，请稍后重试')
  }
  return data
}

export function catalogError(error: unknown): string {
  const status = (error as { response?: { status?: number } })?.response?.status
  if (status === 401) return '登录状态已失效，请重新登录后查看可查询目录'
  if (status === 403) return '当前账号无法访问问数目录，请联系管理员确认权限'
  return '暂时无法加载可查询目录，请重试；这不代表你没有数据表权限'
}
