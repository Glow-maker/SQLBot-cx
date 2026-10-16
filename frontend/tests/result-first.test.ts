import test from 'node:test'
import assert from 'node:assert/strict'
import { createRenderer, h, reactive, ref, nextTick } from 'vue'
import ChartAnswer from '../src/views/chat/answer/ChartAnswer.vue'
import ChartBlock from '../src/views/chat/chat-block/ChartBlock.vue'
import ChartComponent from '../src/views/chat/component/ChartComponent.vue'
import { Table } from '../src/views/chat/component/charts/Table'
import { setResponse, chatApi } from './chat-fixture'
import { drawings, assistant } from './result-first-fixture'

type Node = { text?: string, children: Node[], parent?: Node, props: any }
const node = (text = ''): Node => ({ text, children: [], props: {} })
const renderer = createRenderer<Node, Node>({
  createElement: () => node(), createText: text => node(text), createComment: () => node(),
  insert(child, parent, anchor) { child.parent = parent; const at = anchor ? parent.children.indexOf(anchor) : -1; if (at < 0) parent.children.push(child); else parent.children.splice(at, 0, child) },
  remove(child) { const siblings = child.parent?.children; if (siblings) siblings.splice(siblings.indexOf(child), 1) },
  setText(child, text) { child.text = text }, setElementText(child, text) { child.text = text; child.children = [] },
  patchProp(child, key, _old, value) { child.props[key] = value },
  parentNode: child => child.parent ?? null,
  nextSibling(child) { const siblings = child.parent?.children; return siblings?.[siblings.indexOf(child) + 1] ?? null },
})
const text = (root: Node): string => (root.text ?? '') + root.children.map(text).join(' ')
const table = (status = 'pending') => JSON.stringify({ type: 'table', title: '结果', columns: [{ name: '金额', value: 'amount' }], render_status: status })
const result = { data: [{ amount: 0 }, { amount: null }], fields: ['amount'] }
async function tickUntil(condition: () => boolean) {
  for (let n = 0; n < 30 && !condition(); n++) await new Promise(resolve => setTimeout(resolve, 0))
  assert.ok(condition(), 'expected asynchronous checkpoint was reached')
}
function mountBlock(record: any, typing = true) {
  const message = reactive({ index: 0, record, isTyping: typing })
  const root = node()
  const app = renderer.createApp({ render: () => h(ChartBlock as any, { message }) })
  app.config.warnHandler = () => {}
  app.mount(root)
  return { app, root, message }
}
function mountAnswer() {
  const first = reactive({ id: 1, question: 'fixture' } as any)
  const chat = ref({ id: 1, records: [first] })
  const selected = ref(0)
  const child = ref<any>()
  const events: any[] = []
  const outcomes: string[] = []
  const app = renderer.createApp({ render: () => h({ ...(ChartAnswer as any), render: () => null }, {
    ref: child, currentChat: chat.value, currentChatId: chat.value.id,
    message: { index: selected.value, record: chat.value.records[selected.value], isTyping: true },
    reasoningName: ['sql_answer', 'chart_answer'], onAvatarState: (event: any) => events.push(event),
    onFinish: () => outcomes.push('finish'), onError: () => outcomes.push('error'), onStop: () => outcomes.push('stop'),
  }) })
  app.mount(node())
  return { app, child, first, chat, selected, events, outcomes }
}
function controlledStream() {
  let stream!: ReadableStreamDefaultController
  let signal!: AbortSignal
  setResponse(async controller => {
    signal = controller.signal
    return new Response(new ReadableStream({ start(value) { stream = value } }))
  })
  return {
    send(event: any) { stream.enqueue(new TextEncoder().encode(`data:${JSON.stringify(event)}\n\n`)) },
    close() { stream.close() }, fail() { stream.error(new Error('fixture disconnect')) },
    get aborted() { return signal?.aborted },
  }
}

test('typing answer renders returned rows immediately, including zero and NULL, and then redraws the final chart', async () => {
  drawings.length = 0
  const mounted = mountBlock({ id: 1, sql: 'SELECT amount', chart: table(), data: result })
  try {
    await nextTick(); await nextTick()
    assert.match(text(mounted.root), /本次返回 2 行/)
    assert.match(text(mounted.root), /图表生成中/)
    assert.equal(drawings.at(-1)?.type, 'table')
    assert.deepEqual(drawings.at(-1)?.data, result.data)
    const initial = drawings.at(-1)
    mounted.message.record.chart = JSON.stringify({ type: 'bar', title: '结果', axis: { x: { name: '金额', value: 'amount' }, y: { name: '金额', value: 'amount' } }, render_status: 'ready' })
    await nextTick(); await nextTick()
    assert.equal(drawings.at(-1)?.type, 'bar')
    assert.equal(initial.destroyed, true)
    assert.doesNotMatch(text(mounted.root), /图表生成中/)
  } finally { mounted.app.unmount() }
  assert.ok(drawings.every(drawing => drawing.destroyed))
})

test('historical and embedded empty results remain visible with nonfatal chart failure text', async () => {
  assistant.getAssistant = true; assistant.getEmbedded = true
  const mounted = mountBlock({ id: 1, chart: JSON.stringify({ ...JSON.parse(table('failed')), render_message: '图表生成失败，已保留数据明细' }), data: { data: [] } }, false)
  try { await nextTick(); assert.match(text(mounted.root), /本次返回 0 行/); assert.match(text(mounted.root), /已保留数据明细/) }
  finally { mounted.app.unmount(); assistant.getAssistant = false; assistant.getEmbedded = false }
})

test('table replacement cancels pending resize rendering after its instance is destroyed', async () => {
  const savedDocument = Object.getOwnPropertyDescriptor(globalThis, 'document')
  const savedObserver = Object.getOwnPropertyDescriptor(globalThis, 'ResizeObserver')
  let disconnected = false
  let resize!: (entries: any[]) => void
  Object.defineProperty(globalThis, 'document', { configurable: true, value: { getElementById: () => ({ parentElement: {} }) } })
  Object.defineProperty(globalThis, 'ResizeObserver', { configurable: true, value: class {
    constructor(callback: any) { resize = callback } observe() {} disconnect() { disconnected = true }
  } })
  let renders = 0, destroyed = false
  try {
    const table = new Table('fixture')
    table.table = { render: () => { renders++ }, changeSheetSize: () => {}, destroy: () => { destroyed = true } } as any
    resize([{ borderBoxSize: [{ inlineSize: 800, blockSize: 400 }] }])
    table.destroy()
    await new Promise(resolve => setTimeout(resolve, 250))
    assert.equal(renders, 0)
    assert.equal(destroyed, true)
    assert.equal(disconnected, true)
  } finally {
    for (const [name, saved] of [['document', savedDocument], ['ResizeObserver', savedObserver]] as const) {
      if (saved) Object.defineProperty(globalThis, name, saved); else delete (globalThis as any)[name]
    }
  }
})

test('unmount before the scheduled first drawing does not create a detached chart', async () => {
  drawings.length = 0
  const app = renderer.createApp(ChartComponent, { id: 'unmounted', type: 'table', data: result.data })
  app.mount(node()); app.unmount(); await nextTick()
  assert.equal(drawings.length, 0)
})

test('same-type chart configuration and data updates destroy the old drawing and keep export data current', async () => {
  drawings.length = 0
  const params = reactive({ id: 1, type: 'table', data: result.data, columns: [{ name: '金额', value: 'amount' }] })
  const child = ref<any>()
  const app = renderer.createApp({ render: () => h(ChartComponent, { ...params, ref: child }) })
  app.mount(node()); await nextTick(); await nextTick()
  try {
    const first = drawings.at(-1)
    params.columns = [{ name: '更新金额', value: 'amount' }]
    params.data = [{ amount: 3 }]
    await nextTick(); await nextTick()
    assert.equal(first.destroyed, true)
    assert.equal(drawings.at(-1).axis[0].name, '更新金额')
    assert.deepEqual(child.value.getExcelData().data, [{ amount: 3 }])
    assert.equal(drawings.filter(drawing => !drawing.destroyed).length, 1)
  } finally { app.unmount() }
})

test('stream data is assigned before delayed final chart, and chart warnings finish successfully', async () => {
  const original = chatApi.get_chart_data
  let resolve!: (value: any) => void
  chatApi.get_chart_data = () => new Promise(yes => { resolve = yes })
  const stream = controlledStream(), mounted = mountAnswer()
  try {
    const sent = mounted.child.value.sendMessage()
    await nextTick()
    stream.send({ type: 'chart', content: table() }); stream.send({ type: 'sql-data', content: 'execute-success' })
    await tickUntil(() => !!resolve)
    resolve(result)
    await tickUntil(() => !!mounted.first.data)
    assert.deepEqual(mounted.first.data, result)
    assert.equal(mounted.outcomes.length, 0)
    stream.send({ type: 'chart', content: table('failed') })
    stream.send({ type: 'chart-warning', message: '图表失败，已保留数据明细' })
    stream.send({ type: 'finish' })
    await sent
    assert.equal(mounted.first.chart_warning, '图表失败，已保留数据明细')
    assert.equal(mounted.first.error, undefined)
    assert.deepEqual(mounted.outcomes, ['finish'])
    assert.equal(mounted.events.at(-1)?.state, 'success')
  } finally { mounted.app.unmount(); chatApi.get_chart_data = original }
})

for (const mode of ['close', 'fail', 'stop'] as const) test(`partial results survive ${mode} without becoming a query error`, async () => {
  const original = chatApi.get_chart_data
  chatApi.get_chart_data = async () => result
  const stream = controlledStream(), mounted = mountAnswer()
  try {
    const sent = mounted.child.value.sendMessage(); await nextTick()
    stream.send({ type: 'chart', content: table() }); stream.send({ type: 'sql-data' })
    await tickUntil(() => !!mounted.first.data)
    if (mode === 'stop') { mounted.child.value.stop(); stream.close() } else stream[mode]()
    await sent
    assert.deepEqual(mounted.first.data, result)
    assert.equal(mounted.first.error, undefined)
    assert.ok(mounted.first.chart_warning)
    assert.ok(!mounted.outcomes.includes('error'))
  } finally { mounted.app.unmount(); chatApi.get_chart_data = original }
})

test('switching question in the same chat rejects old fetch and stream events', async () => {
  const original = chatApi.get_chart_data
  let resolve!: (value: any) => void
  chatApi.get_chart_data = () => new Promise(yes => { resolve = yes })
  const stream = controlledStream(), mounted = mountAnswer()
  try {
    const sent = mounted.child.value.sendMessage(); await nextTick()
    stream.send({ type: 'sql-data' }); await tickUntil(() => !!resolve)
    mounted.chat.value.records.push({ id: 2, question: 'second' }); mounted.selected.value = 1
    await nextTick()
    resolve(result); stream.send({ type: 'chart', content: table('ready') }); stream.close()
    await sent
    assert.equal(mounted.first.data, undefined)
    assert.equal(mounted.first.chart, undefined)
    assert.equal(mounted.chat.value.records[1].data, undefined)
    assert.equal(stream.aborted, true)
  } finally { mounted.app.unmount(); chatApi.get_chart_data = original }
})

test('disconnect waits for the already requested data load and preserves an empty result', async () => {
  const original = chatApi.get_chart_data
  let resolve!: (value: any) => void
  chatApi.get_chart_data = () => new Promise(yes => { resolve = yes })
  const stream = controlledStream(), mounted = mountAnswer()
  try {
    const sent = mounted.child.value.sendMessage(); await nextTick()
    stream.send({ type: 'chart', content: table() }); stream.send({ type: 'sql-data' })
    await tickUntil(() => !!resolve)
    stream.close(); await nextTick()
    assert.equal(mounted.outcomes.length, 0)
    resolve({ data: [] }); await sent
    assert.deepEqual(mounted.first.data, { data: [] })
    assert.equal(mounted.first.error, undefined)
    assert.deepEqual(mounted.outcomes, ['stop'])
  } finally { mounted.app.unmount(); chatApi.get_chart_data = original }
})
