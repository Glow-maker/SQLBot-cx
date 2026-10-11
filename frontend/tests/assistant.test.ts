import test from 'node:test'
import assert from 'node:assert/strict'
import { createRenderer, h, reactive, ref, nextTick } from 'vue'
import ChartAnswer from '../src/views/chat/answer/ChartAnswer.vue'
import AnalysisAnswer from '../src/views/chat/answer/AnalysisAnswer.vue'
import PredictAnswer from '../src/views/chat/answer/PredictAnswer.vue'
import AssistantAvatar from '../src/components/assistant/AssistantAvatar.vue'
import AssistantCompanion from '../src/components/assistant/AssistantCompanion.vue'
import { setResponse, chatApi } from './chat-fixture'
import {
  acceptAvatarEvent,
  pointerTarget,
  sampleAvatar,
  springStep,
  EYE_SAFE,
  streamAvatarState,
  type AssistantState,
  type AvatarEvent,
} from '../src/components/assistant/model'
import { useAvatarRequest } from '../src/components/assistant/useAvatarRequest'
import { idlePose, nearestAngle, type IdleMotion } from '../src/components/assistant/idleMotion'

test('idle gestures are bounded, settle between gestures and rotate without a reverse lap', () => {
  for (const mode of ['auto', 'calm', 'bounce', 'sway', 'spin', 'float'] as IdleMotion[]) {
    for (let t = 0; t < 80; t += 0.02) {
      const pose = idlePose(mode, t)
      assert.ok(pose.y >= -24 && pose.y <= 0)
      assert.ok(pose.angle >= -14 && pose.angle <= 360)
    }
    assert.deepEqual(idlePose(mode, 5), { y: 0, angle: 0 })
  }
  assert.deepEqual(idlePose('auto', 1), { y: 0, angle: 0 })
  assert.ok(idlePose('auto', 2.5).y < 0)
  assert.ok(idlePose('spin', 1.59).angle > 359)
  assert.equal(nearestAngle(359, 0), 360)
  assert.equal(nearestAngle(361, 0), 360)
  assert.equal(nearestAngle(-2, 0), 0)
})

test('pointer directions, neutral center, scroll/resize coordinates and invalid rectangles', () => {
  const rect = { left: 300, top: 100, width: 112, height: 112 }
  assert.deepEqual(pointerTarget({ x: 356, y: 156 }, rect), { x: 0, y: 0 })
  for (const [x, y] of [
    [0, 156],
    [1000, 156],
    [356, 0],
    [356, 1000],
  ]) {
    const p = pointerTarget({ x, y }, rect)
    assert.equal(Math.sign(p.x), Math.sign(x - 356))
    assert.equal(Math.sign(p.y), Math.sign(y - 156))
    assert.ok(Math.abs(p.x) <= 9.6 && Math.abs(p.y) <= 7.2)
  }
  assert.deepEqual(pointerTarget({ x: 44, y: 44 }, { left: 0, top: 0, width: 88, height: 88 }), {
    x: 0,
    y: 0,
  })
  assert.deepEqual(pointerTarget(null, rect), { x: 0, y: 0 })
  assert.deepEqual(pointerTarget({ x: NaN, y: 0 }, rect), { x: 0, y: 0 })
  assert.deepEqual(pointerTarget({ x: 0, y: 0 }, { ...rect, width: 0 }), { x: 0, y: 0 })
})

test('complete eyes stay within rocket safe field for every state and extreme gaze', () => {
  for (const state of [
    'idle',
    'thinking',
    'responding',
    'success',
    'error',
    'stopped',
  ] as AssistantState[]) {
    for (const x of [-9.6, 0, 9.6])
      for (const y of [-7.2, 0, 7.2]) {
        for (const t of [0, 0.4, 1.4, 1.49, 3, 20]) {
          const frame = sampleAvatar('rocket', state, t, true, { x, y })
          for (const e of frame.eyes) {
            assert.ok(e.x - e.width / 2 >= EYE_SAFE.left)
            assert.ok(e.x + e.width / 2 <= EYE_SAFE.right)
            assert.ok(e.y - e.height / 2 >= EYE_SAFE.top)
            assert.ok(e.y + e.height / 2 <= EYE_SAFE.bottom)
          }
          assert.equal(frame.eyes[1].x - frame.eyes[0].x, 40)
        }
      }
  }
})

test('Bloub eyes are centered and static mode does not depend on the animation clock', () => {
  const frame = sampleAvatar('bloub', 'idle', 0, false, { x: 0, y: 0 })
  assert.equal(frame.eyes[0].y, 120)
  assert.equal(frame.eyes[1].y, 120)
  assert.equal(frame.eyes[0].x + frame.eyes[1].x, 240)
  assert.deepEqual(frame, sampleAvatar('bloub', 'idle', 150, false, { x: 0, y: 0 }))
})

test('spring settles rapidly and reverses from its current value', () => {
  let p = { value: 0, velocity: 0 }
  for (let i = 0; i < 10; i++) p = springStep(p.value, p.velocity, 9.6, 1 / 60, 0.15)
  assert.ok(p.value > 9.4 && p.value <= 9.6)
  const current = p.value
  p = springStep(p.value, p.velocity, -9.6, 1 / 60, 0.15)
  assert.ok(p.value < current && p.value > -9.6)
  for (let i = 0; i < 20; i++) p = springStep(p.value, p.velocity, 0, 1 / 60, 0.25)
  assert.ok(Math.abs(p.value) < 0.1)
})

test('state mapping uses visible output and explicit completion only', () => {
  assert.equal(streamAvatarState('chart', { type: 'sql-result', content: 'reasoning' }), undefined)
  assert.equal(streamAvatarState('chart', { type: 'sql', content: 'SELECT 1' }), 'responding')
  assert.equal(streamAvatarState('analysis', { type: 'analysis-result', content: '' }), undefined)
  assert.equal(
    streamAvatarState('analysis', { type: 'analysis-result', content: 'result' }),
    'responding'
  )
  assert.equal(
    streamAvatarState('predict', { type: 'predict-result', content: 'result' }),
    'responding'
  )
  for (const type of ['permission_denied', 'cross_source_not_supported', 'error', 'predict-failed'])
    assert.equal(streamAvatarState('chart', { type }), 'error')
  assert.equal(streamAvatarState('chart', { code: 403 }), 'error')
  assert.equal(streamAvatarState('chart', { type: 'finish' }), 'success')
  assert.equal(streamAvatarState('analysis', { type: 'analysis_finish' }), 'success')
  assert.equal(streamAvatarState('predict', { type: 'predict_finish' }), undefined)
  assert.equal(streamAvatarState('predict', { type: 'predict-success' }), 'success')
})

test('request identity rejects late, foreign-chat and post-error completion events', () => {
  const first: AvatarEvent = { chatId: 1, recordIndex: 0, requestId: 1, state: 'thinking' }
  let current = acceptAvatarEvent(null, first, 1)
  current = acceptAvatarEvent(current, { ...first, state: 'error' }, 1)
  assert.equal(acceptAvatarEvent(current, { ...first, state: 'success' }, 1), current)
  const second: AvatarEvent = { ...first, recordIndex: 1, requestId: 2, state: 'thinking' }
  current = acceptAvatarEvent(current, second, 1)
  assert.equal(acceptAvatarEvent(current, { ...first, state: 'responding' }, 1), current)
  assert.equal(acceptAvatarEvent(current, { ...second, chatId: 2, state: 'success' }, 1), current)
  assert.equal(acceptAvatarEvent(null, { ...first, state: 'success' }, 1), null)
})

test('request lifecycle aborts immediately, isolates retries and stops unfinished streams', () => {
  const renderer = createRenderer({
    createElement: () => ({}),
    createText: () => ({}),
    createComment: () => ({}),
    insert() {},
    remove() {},
    setText() {},
    setElementText() {},
    patchProp() {},
    parentNode: () => null,
    nextSibling: () => null,
  })
  const events: AvatarEvent[] = []
  let requests!: ReturnType<typeof useAvatarRequest>
  const app = renderer.createApp({
    setup() {
      requests = useAvatarRequest((e) => events.push(e))
      return () => h('div')
    },
  })
  app.mount({})
  const first = requests.begin(1, 0, () => true)
  requests.cancel()
  assert.ok(first.controller.signal.aborted)
  first.update('success')
  assert.equal(events.at(-1)?.state, 'stopped')
  const second = requests.begin(1, 1, () => true)
  first.update('error')
  assert.equal(events.at(-1)?.requestId, second.requestId)
  second.end()
  assert.equal(events.at(-1)?.state, 'stopped')
  const third = requests.begin(1, 2, () => true)
  third.update('success')
  assert.equal(requests.cancel(), false)
  const fourth = requests.begin(1, 3, () => true)
  app.unmount()
  assert.ok(fourth.controller.signal.aborted)
})

const renderer = createRenderer({
  createElement: () => ({}),
  createText: () => ({}),
  createComment: () => ({}),
  insert() {},
  remove() {},
  setText() {},
  setElementText() {},
  patchProp() {},
  parentNode: () => null,
  nextSibling: () => null,
})
function mountAnswer(component: any) {
  const record = reactive({
    question: 'fixture only',
    analysis_record_id: 10,
    predict_record_id: 10,
  })
  const chat = ref({ id: 1, records: [record] })
  const events: AvatarEvent[] = []
  const child = ref<any>()
  const subject = { ...component, render: () => null }
  const app = renderer.createApp({
    setup: () => () =>
      h(subject, {
        ref: child,
        currentChat: chat.value,
        currentChatId: chat.value.id,
        message: { index: 0, record: chat.value.records[0] },
        reasoningName: 'sql_answer',
        onAvatarState: (event: AvatarEvent) => events.push(event),
      }),
  })
  app.mount({})
  return { app, child, events, chat, record }
}
function fixtureStream(messages: object[]) {
  return new Response(
    new ReadableStream({
      start(controller) {
        // Split within JSON to exercise the production answer component's stream buffering.
        const data = new TextEncoder().encode(
          messages.map((m) => `data:${JSON.stringify(m)}\n\n`).join('')
        )
        controller.enqueue(data.slice(0, 9))
        controller.enqueue(data.slice(9))
        controller.close()
      },
    })
  )
}
test('all three real answer components emit thinking/output/completion for stream fixtures', async () => {
  for (const [component, messages] of [
    [
      ChartAnswer,
      [
        { type: 'sql-result', reasoning_content: 'hidden' },
        { type: 'sql', content: 'SELECT 1' },
        { type: 'finish' },
      ],
    ],
    [
      AnalysisAnswer,
      [
        { type: 'analysis-result', content: 'analysis', reasoning_content: '' },
        { type: 'analysis_finish' },
      ],
    ],
    [
      PredictAnswer,
      [
        { type: 'predict-result', content: 'prediction', reasoning_content: '' },
        { type: 'predict-success' },
      ],
    ],
  ] as const) {
    setResponse(async () => fixtureStream([...messages]))
    const mounted = mountAnswer(component)
    await mounted.child.value.sendMessage()
    assert.deepEqual(
      mounted.events.map((e) => e.state),
      ['thinking', 'responding', 'success']
    )
    mounted.app.unmount()
  }
})

test('real chart component handles permission refusal and truncated streams without success', async () => {
  for (const type of ['permission_denied', 'cross_source_not_supported', 'error']) {
    setResponse(async () => fixtureStream([{ type, content: 'fixture' }, { type: 'finish' }]))
    const mounted = mountAnswer(ChartAnswer)
    await mounted.child.value.sendMessage()
    assert.deepEqual(
      mounted.events.map((e) => e.state),
      ['thinking', 'error']
    )
    mounted.app.unmount()
  }
  setResponse(async () => fixtureStream([{ type: 'sql', content: 'SELECT 1' }]))
  const mounted = mountAnswer(ChartAnswer)
  await mounted.child.value.sendMessage()
  assert.deepEqual(
    mounted.events.map((e) => e.state),
    ['thinking', 'responding', 'error']
  )
  mounted.app.unmount()
})

test('progress events do not imply an answer or bypass a permission refusal', async () => {
  setResponse(async () => fixtureStream([
    { type: 'progress', message: '正在生成查询语句' },
    { type: 'progress', message: '正在校验查询权限' },
    { type: 'permission_denied', reason: 'no_table_permission' },
    { type: 'finish' },
  ]))
  const mounted = mountAnswer(ChartAnswer)
  await mounted.child.value.sendMessage()
  assert.deepEqual(mounted.events.map((e) => e.state), ['thinking', 'error'])
  assert.match((mounted.record as any).error, /no_table_permission/)
  mounted.app.unmount()
})

test('transport errors stop the real answer component and emit error', async () => {
  setResponse(async () => {
    throw new Error('fixture connection lost')
  })
  const mounted = mountAnswer(ChartAnswer)
  const report = console.error
  console.error = () => {}
  try {
    await mounted.child.value.sendMessage()
    assert.deepEqual(
      mounted.events.map((e) => e.state),
      ['thinking', 'error']
    )
    assert.match((mounted.record as any).error, /fixture connection lost/)
  } finally {
    console.error = report
    mounted.app.unmount()
  }
})

test('real answer stop aborts a pending read; changing conversations rejects old output', async () => {
  let pending!: ReadableStreamDefaultController<Uint8Array>
  let abort!: AbortController
  setResponse(async (controller) => {
    abort = controller
    return new Response(
      new ReadableStream({
        start(c) {
          pending = c
          controller.signal.addEventListener('abort', () => c.close())
        },
      })
    )
  })
  const mounted = mountAnswer(ChartAnswer)
  const request = mounted.child.value.sendMessage()
  await nextTick()
  mounted.child.value.stop()
  await request
  assert.ok(abort.signal.aborted)
  assert.deepEqual(
    mounted.events.map((e) => e.state),
    ['thinking', 'stopped']
  )
  mounted.app.unmount()

  setResponse(
    async () =>
      new Response(
        new ReadableStream({
          start(c) {
            pending = c
          },
        })
      )
  )
  const other = mountAnswer(ChartAnswer)
  const older = other.child.value.sendMessage()
  await nextTick()
  other.chat.value = {
    id: 2,
    records: [{ question: 'new', analysis_record_id: 11, predict_record_id: 11 }],
  }
  await nextTick()
  pending.enqueue(new TextEncoder().encode('data:{"type":"sql","content":"OLD SQL"}\n\n'))
  pending.close()
  await older
  assert.ok(!('sql' in other.chat.value.records[0]))
  assert.deepEqual(
    other.events.map((e) => e.state),
    ['thinking']
  )
  other.app.unmount()
})

test('greeting does not stack and cancels on focus, busy state, appearance change and unmount', async () => {
  const timers = new Map<number, () => void>()
  let timerId = 0
  const originalSet = globalThis.setTimeout
  const originalClear = globalThis.clearTimeout
  globalThis.setTimeout = ((callback: () => void) => {
    timers.set(++timerId, callback)
    return timerId
  }) as any
  globalThis.clearTimeout = ((id: number) => timers.delete(id)) as any
  const props = reactive({
    variant: 'rocket',
    animated: true,
    idleMotion: 'auto',
    engaged: false,
    state: 'idle',
  })
  const child = ref<any>()
  const component = { ...(AssistantCompanion as any), render: () => null }
  const app = renderer.createApp({
    setup: () => () => h(component, { ...props, ref: child }),
  })
  try {
    app.mount({})
    const state = () => child.value.$.setupState
    state().greet()
    state().greet()
    assert.equal(timers.size, 1)
    assert.equal(state().replay, 1)
    props.engaged = true
    await nextTick()
    assert.equal(timers.size, 0)
    assert.equal(state().reaction, false)
    state().greet()
    assert.equal(timers.size, 0)
    props.engaged = false
    await nextTick()
    state().greet()
    props.state = 'thinking'
    await nextTick()
    assert.equal(timers.size, 0)
    state().greet()
    assert.equal(timers.size, 0)
    props.state = 'idle'
    await nextTick()
    state().greet()
    props.variant = 'bloub'
    await nextTick()
    assert.equal(timers.size, 0)
    props.animated = false
    await nextTick()
    state().greet()
    assert.equal(timers.size, 0)
    props.animated = true
    await nextTick()
    state().greet()
    assert.equal(timers.size, 1)
  } finally {
    app.unmount()
    globalThis.setTimeout = originalSet
    globalThis.clearTimeout = originalClear
  }
  assert.equal(timers.size, 0)
})

test('avatar animates by default despite reduced motion and cleans up on hide and unmount', async () => {
  class TrackedTarget extends EventTarget {
    listeners = new Map<string, Set<any>>()
    addEventListener(type: string, fn: any, options?: any) {
      const set = this.listeners.get(type) ?? new Set()
      set.add(fn)
      this.listeners.set(type, set)
      super.addEventListener(type, fn, options)
    }
    removeEventListener(type: string, fn: any, options?: any) {
      this.listeners.get(type)?.delete(fn)
      super.removeEventListener(type, fn, options)
    }
    count() {
      return [...this.listeners.values()].reduce((n, set) => n + set.size, 0)
    }
  }
  const page = Object.assign(new TrackedTarget(), { hidden: false })
  const media = Object.assign(new TrackedTarget(), { matches: true })
  const win = Object.assign(new TrackedTarget(), { matchMedia: () => media })
  const frames = new Map<number, FrameRequestCallback>()
  let frameId = 0
  let observers = 0
  const names = [
    'window',
    'document',
    'requestAnimationFrame',
    'cancelAnimationFrame',
    'IntersectionObserver',
    'ResizeObserver',
  ]
  const saved = names.map((name) => Object.getOwnPropertyDescriptor(globalThis, name))
  class Observer {
    constructor() {
      observers++
    }
    observe() {}
    disconnect() {
      observers--
    }
  }
  Object.assign(globalThis, {
    window: win,
    document: page,
    requestAnimationFrame: (callback: FrameRequestCallback) => {
      frames.set(++frameId, callback)
      return frameId
    },
    cancelAnimationFrame: (id: number) => frames.delete(id),
    IntersectionObserver: Observer,
    ResizeObserver: Observer,
  })
  try {
    for (let i = 0; i < 5; i++) {
      const app = renderer.createApp(
        { ...(AssistantAvatar as any), render: () => null },
        { followPointer: true }
      )
      app.mount({})
      await nextTick()
      assert.equal(frames.size, 1)
      page.hidden = true
      page.dispatchEvent(new Event('visibilitychange'))
      await nextTick()
      assert.equal(frames.size, 0)
      page.hidden = false
      page.dispatchEvent(new Event('visibilitychange'))
      await nextTick()
      assert.equal(frames.size, 1)
      media.matches = true
      media.dispatchEvent(new Event('change'))
      await nextTick()
      assert.equal(frames.size, 1)
      media.matches = false
      media.dispatchEvent(new Event('change'))
      await nextTick()
      assert.equal(frames.size, 1)
      app.unmount()
      await nextTick()
      assert.equal(frames.size, 0)
      assert.equal(win.count() + page.count() + media.count(), 0)
      assert.equal(observers, 0)
    }
    const still = renderer.createApp(
      { ...(AssistantAvatar as any), render: () => null },
      { animated: false, size: 28 }
    )
    still.mount({})
    await nextTick()
    assert.equal(frames.size, 0)
    still.unmount()
  } finally {
    names.forEach((name, i) => {
      if (saved[i]) Object.defineProperty(globalThis, name, saved[i]!)
      else delete (globalThis as any)[name]
    })
  }
})

import './chat-progress.test'
import './question-catalog.test'

test('real chart waits for local result loading before success and treats load rejection as failure', async () => {
  const original = chatApi.get_chart_data
  try {
    for (const fails of [false, true]) {
      let resolve!: (value: any) => void
      let reject!: (error: Error) => void
      chatApi.get_chart_data = () => new Promise((yes, no) => { resolve = yes; reject = no })
      setResponse(async () => fixtureStream([{ type: 'sql-data' }, { type: 'finish' }]))
      const mounted = mountAnswer(ChartAnswer)
      const sent = mounted.child.value.sendMessage()
      for (let n = 0; n < 10 && !resolve; n++) await new Promise(r => setTimeout(r, 0))
      assert.equal(mounted.events.at(-1)?.state, 'thinking')
      if (fails) reject(new Error('fixture load failure'))
      else resolve([])
      await sent
      assert.equal(mounted.events.at(-1)?.state, fails ? 'error' : 'success')
      mounted.app.unmount()
    }
  } finally { chatApi.get_chart_data = original }
})

test('late result fetch after switching chats cannot write to the new record', async () => {
  const original = chatApi.get_chart_data
  try {
    let resolve!: (value: any) => void
    chatApi.get_chart_data = () => new Promise(yes => { resolve = yes })
    setResponse(async () => fixtureStream([{ type: 'sql-data' }, { type: 'finish' }]))
    const mounted = mountAnswer(ChartAnswer)
    const sent = mounted.child.value.sendMessage()
    for (let n = 0; n < 10 && !resolve; n++) await new Promise(r => setTimeout(r, 0))
    mounted.chat.value = { id: 2, records: [{ question: 'new', analysis_record_id: 10, predict_record_id: 10 }] }
    await nextTick()
    resolve([{ private: 'old request fixture' }])
    await sent
    assert.equal((mounted.chat.value.records[0] as any).data, undefined)
    assert.notEqual(mounted.events.at(-1)?.state, 'success')
    mounted.app.unmount()
  } finally { chatApi.get_chart_data = original }
})
