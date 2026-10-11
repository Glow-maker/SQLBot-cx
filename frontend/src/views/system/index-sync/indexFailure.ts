const reasons: Record<string, string> = {
  METADATA_READ_FAILED: '读取表结构失败，请在中台检查该表的元数据和数据源连接，再重新同步。',
  TABLE_EMBEDDING_FAILED: '生成该表索引失败，请管理员检查 Embedding 服务后重新同步。',
  PROCESS_INTERRUPTED: '同步期间服务中断，请重新同步；未变化的索引会自动跳过。',
  INDEX_JOB_FAILED: '索引任务失败，请管理员检查索引服务后重新同步。',
}
export function indexFailureReason(code: unknown): string {
  return typeof code === 'string' && Object.prototype.hasOwnProperty.call(reasons, code)
    ? reasons[code]! : '同步未完成，请管理员核对服务日志后重新同步。'
}
export function indexFailureTable(tableId: unknown): string {
  return /^\d+$/.test(String(tableId ?? '')) ? `表 ID ${tableId}` : '任务级错误'
}
