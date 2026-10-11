<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { idlePose, nearestAngle, type IdleMotion } from './idleMotion'
import {
  FLAME_PATH,
  ROCKET_PATH,
  pointerTarget,
  sampleAvatar,
  springStep,
  stateLabels,
  type AssistantState,
  type AssistantVariant,
  type Point,
} from './model'

const props = withDefaults(
  defineProps<{
    variant?: AssistantVariant
    state?: AssistantState
    size?: number
    animated?: boolean
    followPointer?: boolean
    idleMotion?: IdleMotion
    motionReplay?: number
    engaged?: boolean
    grounded?: boolean
  }>(),
  {
    variant: 'rocket',
    state: 'idle',
    size: 112,
    animated: true,
    followPointer: false,
    idleMotion: 'auto',
    motionReplay: 0,
  }
)
const root = ref<HTMLElement>()
const svg = ref<SVGSVGElement>()
const visible = ref(true)
const intersecting = ref(true)
const elapsed = ref(0)
const gaze = ref<Point>({ x: 0, y: 0 })
const running = ref(false)
const pose = ref({ y: 0, angle: 0 })
let poseVelocity = { y: 0, angle: 0 }
let motionStart = 0
const poseTransform = computed(() =>
  loop.value
    ? `translate(120 ${120 + pose.value.y}) rotate(${pose.value.angle}) translate(-120 -120)`
    : undefined
)
let pointer: Point | null = null
let target: Point = { x: 0, y: 0 }
let velocity: Point = { x: 0, y: 0 }
let raf = 0
let last = 0
let surface: Element | null = null
let intersection: IntersectionObserver | undefined
let resize: ResizeObserver | undefined
const canMove = computed(
  () => props.animated && visible.value && intersecting.value
)
const settledSuccess = computed(() => props.state === 'success' && elapsed.value >= 0.8)
const loop = computed(
  () =>
    canMove.value &&
    (['idle', 'thinking', 'responding'].includes(props.state) ||
      (props.state === 'success' && !settledSuccess.value))
)
const tracking = computed(
  () =>
    loop.value && props.followPointer && !props.engaged && props.state === 'idle' && props.size > 28
)
const frame = computed(() =>
  sampleAvatar(
    props.variant,
    settledSuccess.value ? 'idle' : props.state,
    elapsed.value,
    loop.value,
    gaze.value
  )
)

function aim() {
  if (!tracking.value || !svg.value || !surface) {
    target = { x: 0, y: 0 }
    return
  }
  const scope = surface.getBoundingClientRect()
  if (
    pointer &&
    (pointer.x < scope.left ||
      pointer.x > scope.right ||
      pointer.y < scope.top ||
      pointer.y > scope.bottom)
  )
    pointer = null
  target = pointerTarget(pointer, svg.value.getBoundingClientRect())
}
function move(event: Event) {
  const e = event as PointerEvent
  if (e.pointerType === 'touch') return
  pointer = { x: e.clientX, y: e.clientY }
  aim()
}
function reset() {
  pointer = null
  target = { x: 0, y: 0 }
}
function stopLoop() {
  cancelAnimationFrame(raf)
  raf = 0
  last = 0
  running.value = false
}
function tick(ms: number) {
  if (!loop.value) {
    stopLoop()
    return
  }
  const dt = last ? Math.min((ms - last) / 1000, 0.032) : 0
  last = ms
  elapsed.value += dt
  const desired =
    props.state === 'idle' && !props.engaged && props.size > 28
      ? idlePose(props.idleMotion, elapsed.value - motionStart)
      : props.state === 'thinking' || props.state === 'responding'
        ? { y: -10 * Math.abs(Math.sin(elapsed.value * 4.5)), angle: 4 * Math.sin(elapsed.value * 4.5) }
        : { y: 0, angle: 0 }
  const py = springStep(pose.value.y, poseVelocity.y, desired.y, dt, 0.12)
  const pa = springStep(
    pose.value.angle,
    poseVelocity.angle,
    nearestAngle(pose.value.angle, desired.angle),
    dt,
    0.12
  )
  pose.value = { y: py.value, angle: pa.value }
  poseVelocity = { y: py.velocity, angle: pa.velocity }
  const response = pointer && tracking.value ? 0.15 : 0.25
  const performing =
    Math.abs(pose.value.y) > 0.1 || Math.abs(nearestAngle(0, pose.value.angle)) > 0.1
  const x = springStep(gaze.value.x, velocity.x, performing ? 0 : target.x, dt, response)
  const y = springStep(
    gaze.value.y,
    velocity.y,
    performing ? 0 : props.engaged ? 2.4 : target.y,
    dt,
    response
  )
  gaze.value = { x: x.value, y: y.value }
  velocity = { x: x.velocity, y: y.velocity }
  raf = requestAnimationFrame(tick)
}
function sync() {
  reset()
  gaze.value = { x: 0, y: 0 }
  velocity = { x: 0, y: 0 }
  if (!loop.value) {
    stopLoop()
    pose.value = { y: 0, angle: 0 }
    poseVelocity = { y: 0, angle: 0 }
    motionStart = elapsed.value
  } else if (!raf) {
    running.value = true
    raf = requestAnimationFrame(tick)
  }
}
function visibilityChange() {
  visible.value = !document.hidden
  sync()
}
watch(
  () => [props.state, props.variant],
  () => {
    elapsed.value = 0
    motionStart = 0
    sync()
  },
  { flush: 'post' }
)
watch([loop, tracking], sync)
watch(
  () => [props.idleMotion, props.motionReplay],
  () => {
    motionStart = elapsed.value
  }
)
onMounted(() => {
  surface = root.value?.closest('[data-assistant-surface]') ?? root.value ?? null
  visible.value = !document.hidden
  if (props.followPointer && props.size > 28) {
    surface?.addEventListener('pointermove', move, { passive: true })
    surface?.addEventListener('pointerleave', reset)
    window.addEventListener('blur', reset)
    window.addEventListener('resize', aim, { passive: true })
    window.addEventListener('scroll', aim, { passive: true, capture: true })
  }
  document.addEventListener('visibilitychange', visibilityChange)
  intersection = new IntersectionObserver((entries) => {
    intersecting.value = entries[0]?.isIntersecting ?? false
  })
  resize = new ResizeObserver(aim)
  if (root.value) {
    intersection.observe(root.value)
    resize.observe(root.value)
  }
  if (surface) resize.observe(surface)
  sync()
})
onBeforeUnmount(() => {
  stopLoop()
  surface?.removeEventListener('pointermove', move)
  surface?.removeEventListener('pointerleave', reset)
  window.removeEventListener('blur', reset)
  window.removeEventListener('resize', aim)
  window.removeEventListener('scroll', aim, true)
  document.removeEventListener('visibilitychange', visibilityChange)
  intersection?.disconnect()
  resize?.disconnect()
})
</script>

<template>
  <span
    ref="root"
    class="assistant-avatar"
    :class="{ 'motion-off': !canMove }"
    :style="{ width: `${size}px`, height: `${size}px` }"
    :data-state="state"
    :data-running="running"
    :data-engaged="!!engaged"
  >
    <Transition name="avatar-swap" mode="out-in">
      <svg
        :key="variant"
        ref="svg"
        viewBox="0 0 240 240"
        role="img"
        :aria-label="`${variant === 'rocket' ? '原创火箭' : 'Bloub'}：${stateLabels[state]}`"
      >
        <ellipse
          v-if="grounded"
          class="avatar-ground"
          cx="120"
          cy="235"
          rx="38"
          ry="4"
          fill="currentColor"
          :opacity="loop ? 0.12 + pose.y * 0.002 : 0.12"
          :transform="`translate(120 235) scale(${loop ? 1 + pose.y * 0.009 : 1} 1) translate(-120 -235)`"
        />
        <g class="avatar-motion" :transform="poseTransform">
          <g :transform="variant === 'rocket' ? 'translate(6 0)' : undefined">
            <path
              v-if="variant === 'rocket'"
              :d="FLAME_PATH"
              fill="#ff8d87"
              :transform="`translate(114 181) scale(1 ${frame.flameScale}) translate(-114 -181)`"
            />
            <g
              :transform="`translate(120 120) scale(1 ${frame.bodyScale}) translate(-120 -120)`"
              fill="currentColor"
            >
              <path v-if="variant === 'rocket'" :d="ROCKET_PATH" />
              <circle v-else cx="120" cy="120" r="94" />
            </g>
            <g class="avatar-eyes" fill="#fffaf4">
              <rect
                v-for="(eye, i) in frame.eyes"
                :key="i"
                :x="-eye.width / 2"
                :y="-eye.height / 2"
                :width="eye.width"
                :height="eye.height"
                :rx="eye.width / 2"
                :transform="eye.matrix"
              />
            </g>
          </g>
        </g>
      </svg>
    </Transition>
  </span>
</template>

<style scoped>
.assistant-avatar {
  display: inline-flex;
  position: relative;
  flex: none;
  color: var(--assistant-character, var(--ed-color-primary, var(--el-color-primary, #3370ff)));
  vertical-align: middle;
}
svg {
  display: block;
  width: 100%;
  height: 100%;
  overflow: visible;
}
.avatar-swap-enter-active,
.avatar-swap-leave-active {
  transition: opacity 100ms ease;
}
.avatar-swap-enter-from,
.avatar-swap-leave-to {
  opacity: 0;
}
.motion-off .avatar-swap-enter-active,
.motion-off .avatar-swap-leave-active {
  transition: none;
}
</style>
