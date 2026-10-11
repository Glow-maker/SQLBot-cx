import { ref, watch } from 'vue'
import type { AssistantVariant } from './model'
import { idleMotionLabels, type IdleMotion } from './idleMotion'

const KEY = 'sqlbot.assistant-appearance.v1'
export function useAvatarPreferences() {
  const variant = ref<AssistantVariant>('rocket')
  const animated = ref(true)
  const idleMotion = ref<IdleMotion>('auto')
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || 'null')
    if (saved?.variant === 'rocket' || saved?.variant === 'bloub') variant.value = saved.variant
    if (typeof saved?.animated === 'boolean') animated.value = saved.animated
    if (Object.prototype.hasOwnProperty.call(idleMotionLabels, saved?.idleMotion))
      idleMotion.value = saved.idleMotion
  } catch {
    /* Storage may be unavailable in an embedded/private browser. */
  }
  watch([variant, animated, idleMotion], () => {
    try {
      localStorage.setItem(
        KEY,
        JSON.stringify({
          variant: variant.value,
          animated: animated.value,
          idleMotion: idleMotion.value,
        })
      )
    } catch {
      /* Keep in-memory choice. */
    }
  })
  return { variant, animated, idleMotion }
}
