export const idleMotionLabels = {
  auto: '自动换动作',
  calm: '安静呼吸',
  bounce: '弹一弹',
  sway: '摇一摇',
  spin: '转一圈',
  float: '轻轻飘',
} as const
export type IdleMotion = keyof typeof idleMotionLabels
export type MotionPose = { y: number; angle: number }
const sequence: IdleMotion[] = ['bounce', 'sway', 'float', 'spin']

// One short gesture, then a long quiet interval. No scaling of the face or body.
export function idlePose(mode: IdleMotion, seconds: number): MotionPose {
  const neutral = { y: 0, angle: 0 }
  const time = seconds - (mode === 'auto' ? 2 : 0)
  if (time < 0 || mode === 'calm') return neutral
  const phase = time % 9
  const action = mode === 'auto' ? sequence[Math.floor(time / 9) % sequence.length] : mode
  const duration = action === 'spin' ? 1.6 : 2
  if (phase >= duration) return neutral
  const p = phase / duration
  const envelope = Math.sin(Math.PI * p) ** 2
  if (action === 'bounce') return { y: -24 * Math.sin(2 * Math.PI * p) ** 2 * (1 - p), angle: 0 }
  if (action === 'sway') return { y: 0, angle: 14 * Math.sin(4 * Math.PI * p) * envelope }
  if (action === 'float')
    return { y: -18 * envelope, angle: -7 * Math.sin(2 * Math.PI * p) * envelope }
  return { y: -8 * envelope, angle: 360 * (p * p * (3 - 2 * p)) }
}

export function nearestAngle(current: number, target: number) {
  return current + ((((target - current) % 360) + 540) % 360) - 180
}
