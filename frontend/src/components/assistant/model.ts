import { blinkScale, eyePoses, liveliness } from './bloub/face'

export type AssistantVariant = 'rocket' | 'bloub'
export type AssistantState = 'idle' | 'thinking' | 'responding' | 'success' | 'error' | 'stopped'
export const stateLabels: Record<AssistantState, string> = {
  idle: '准备就绪',
  thinking: '正在处理',
  responding: '正在生成回答',
  success: '已完成',
  error: '需要留意',
  stopped: '已停止',
}
export const VIEW_SIZE = 240
// Approved round-top Rocket. The face is deliberately independent of this path.
export const ROCKET_PATH =
  'M114 13C151.2 13 172.8 69 172.8 117C194.4 134 207.6 157 205.2 183Q204 195 192 189L163.2 172Q157 184 148.8 188Q114 200 79.2 188Q71 184 64.8 172L36 189Q24 195 22.8 183C20.4 157 33.6 134 55.2 117C55.2 69 76.8 13 114 13Z'
export const FLAME_PATH = 'M96 181Q103 213 114 229Q125 213 132 181Q123 190 114 190Q105 190 96 181Z'
export const EYE_SAFE = { left: 76, right: 152, top: 80, bottom: 139 }
export const clamp = (n: number, low: number, high: number) => Math.max(low, Math.min(high, n))
export type Point = { x: number; y: number }

export function pointerTarget(
  pointer: Point | null,
  box: { left: number; top: number; width: number; height: number }
): Point {
  if (!pointer || box.width <= 0 || box.height <= 0 || !Number.isFinite(pointer.x + pointer.y))
    return { x: 0, y: 0 }
  return {
    x: clamp((pointer.x - box.left - box.width / 2) / box.width, -1, 1) * VIEW_SIZE * 0.04,
    y: clamp((pointer.y - box.top - box.height / 2) / box.height, -1, 1) * VIEW_SIZE * 0.03,
  }
}

// Exact critically damped spring step; preserves velocity when the pointer reverses.
export function springStep(
  value: number,
  velocity: number,
  target: number,
  dt: number,
  response: number
) {
  const w = 6 / response
  const delta = value - target
  const c = velocity + w * delta
  const decay = Math.exp(-w * dt)
  return { value: target + (delta + c * dt) * decay, velocity: (velocity - w * c * dt) * decay }
}

export function sampleAvatar(
  variant: AssistantVariant,
  state: AssistantState,
  time: number,
  moving: boolean,
  gaze: Point
) {
  const live = liveliness(time % 900, { wander: 0, float: moving, blink: moving })
  const lid = moving ? blinkScale(live.lid) : 1
  const height = state === 'thinking' ? 24 : state === 'error' ? 34 : 32
  const happy = state === 'success'
  const eyeHeight = happy ? 12 : height * lid
  const eyes =
    variant === 'rocket'
      ? [-1, 1].map((side) => ({ x: 114 + side * 20, y: 109, a: 1, b: 0, c: 0, d: 1 }))
      : eyePoses({ yaw: 0, pitch: 0, roll: 0 }, 92).map((e) => ({
          ...e,
          x: 120 + e.x,
          y: 120 + e.y,
        }))
  const offset = { x: clamp(gaze.x, -9.6, 9.6), y: clamp(gaze.y, -7.2, 7.2) }
  const pulse =
    moving && (state === 'thinking' || state === 'responding')
      ? Math.sin(time * (state === 'thinking' ? 4 : 7))
      : 0
  return {
    eyes: eyes.map((e) => ({
      x: e.x + offset.x,
      y: e.y + offset.y,
      width: 14,
      height: eyeHeight,
      matrix: `matrix(${e.a} ${e.b} ${e.c} ${e.d} ${e.x + offset.x} ${e.y + offset.y})`,
    })),
    bodyScale: moving ? live.breath + pulse * 0.008 : 1,
    flameScale: moving ? (state === 'responding' ? 1.05 : 0.85) + pulse * 0.1 : 0.8,
  }
}

export interface AvatarEvent {
  chatId: number | undefined
  recordIndex: number
  requestId: number
  state: AssistantState
}
export const isActiveState = (state: AssistantState) =>
  state === 'thinking' || state === 'responding'
export function acceptAvatarEvent(
  current: AvatarEvent | null,
  event: AvatarEvent,
  chatId: number | undefined
): AvatarEvent | null {
  if (event.chatId !== chatId) return current
  if (event.state === 'thinking' && (!current || event.requestId > current.requestId)) return event
  if (
    !current ||
    current.requestId !== event.requestId ||
    current.recordIndex !== event.recordIndex
  )
    return current
  if (!isActiveState(current.state)) return current
  return event
}

export function streamAvatarState(
  kind: 'chart' | 'analysis' | 'predict',
  data: { type?: string; content?: unknown; code?: number }
): AssistantState | undefined {
  if (
    (data.code && data.code !== 200) ||
    ['error', 'permission_denied', 'cross_source_not_supported', 'predict-failed'].includes(
      data.type || ''
    )
  )
    return 'error'
  if (
    (kind === 'chart' && data.type === 'finish') ||
    (kind === 'analysis' && data.type === 'analysis_finish') ||
    (kind === 'predict' && data.type === 'predict-success')
  )
    return 'success'
  if (
    data.content &&
    ((kind === 'chart' && ['sql', 'chart'].includes(data.type || '')) ||
      (kind === 'analysis' && data.type === 'analysis-result') ||
      (kind === 'predict' && data.type === 'predict-result'))
  )
    return 'responding'
}
