import { onBeforeUnmount } from 'vue'
import { isActiveState, streamAvatarState, type AvatarEvent, type AssistantState } from './model'

let nextRequestId = 0
interface AvatarRun {
  requestId: number
  controller: AbortController
  current: () => boolean
  busy: () => boolean
  update: (state: AssistantState) => void
  event: (
    kind: 'chart' | 'analysis' | 'predict',
    data: Parameters<typeof streamAvatarState>[1]
  ) => void
  end: () => void
}
export function useAvatarRequest(notify: (event: AvatarEvent) => void) {
  let active: AvatarRun | undefined
  function begin(chatId: number | undefined, recordIndex: number, valid: () => boolean): AvatarRun {
    cancel()
    const requestId = ++nextRequestId
    const controller = new AbortController()
    let state: AssistantState = 'thinking'
    const current = () => active?.requestId === requestId && valid() && !controller.signal.aborted
    const update = (next: AssistantState) => {
      if (!current() || !isActiveState(state)) return
      state = next
      notify({ chatId, recordIndex, requestId, state })
    }
    const run = {
      requestId,
      controller,
      current,
      update,
      busy: () => current() && isActiveState(state),
      event: (
        kind: 'chart' | 'analysis' | 'predict',
        data: Parameters<typeof streamAvatarState>[1]
      ) => {
        const next = streamAvatarState(kind, data)
        if (next) update(next)
      },
      end: () => {
        if (isActiveState(state)) update('stopped')
      },
    }
    active = run
    notify({ chatId, recordIndex, requestId, state })
    return run
  }
  function cancel() {
    if (!active) return false
    const wasBusy = active.busy()
    active.end()
    active.controller.abort()
    active = undefined
    return wasBusy
  }
  onBeforeUnmount(cancel)
  return { begin, cancel }
}
