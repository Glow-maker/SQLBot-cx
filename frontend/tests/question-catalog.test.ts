import test from 'node:test'
import assert from 'node:assert/strict'
import { parseQuestionCatalog, catalogError } from '../src/components/assistant/questionCatalog'
import { indexFailureReason, indexFailureTable } from '../src/views/system/index-sync/indexFailure'

test('authorized question catalog uses server-provided names, questions and total', () => {
  const table = { name: '销售订单', description: '虚构业务测试数据', question: '销售订单有多少条数据？' }
  const result = parseQuestionCatalog({ tables: [table], total: 9 })
  assert.deepEqual(result.tables, [table])
  assert.equal(result.total, 9)
  assert.deepEqual(parseQuestionCatalog({ tables: [], total: 0 }), { tables: [], total: 0 })
})

test('invalid catalog is not rendered as an empty authorization result', () => {
  for (const value of [undefined, {}, { tables: [], total: -1 }, { tables: [{ name: 'x' }], total: 1 }, { tables: [], total: '0' }]) {
    assert.throws(() => parseQuestionCatalog(value), /无法读取/)
  }
  assert.match(catalogError({ response: { status: 401 } }), /登录状态已失效/)
  assert.match(catalogError({ response: { status: 403 } }), /确认权限/)
  assert.match(catalogError({ response: { status: 502 } }), /不代表你没有/)
})

test('index failure details show safe categories and never echo raw provider errors', () => {
  assert.match(indexFailureReason('METADATA_READ_FAILED'), /元数据和数据源连接/)
  assert.match(indexFailureReason('TABLE_EMBEDDING_FAILED'), /Embedding/)
  assert.match(indexFailureReason('PROCESS_INTERRUPTED'), /服务中断/)
  assert.doesNotMatch(indexFailureReason('password=secret'), /secret|password/)
  assert.equal(indexFailureTable(123), '表 ID 123')
  assert.equal(indexFailureTable('password=secret'), '任务级错误')
})
