<script setup lang="ts">
import BaseAnswer from './BaseAnswer.vue'
import ChatProgressCard from './ChatProgress.vue'
import { createProgress, receiveProgress, endProgress, completeProgress, progressError, type ChatProgress } from './chatProgress'
import { useAvatarRequest } from '@/components/assistant/useAvatarRequest'
import { Chat, chatApi, ChatInfo, type ChatMessage, ChatRecord, questionApi } from '@/api/chat.ts'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import ChartBlock from '@/views/chat/chat-block/ChartBlock.vue'
import JSONBig from 'json-bigint'

const props = withDefaults(
  defineProps<{
    recordId?: number
    chatList?: Array<ChatInfo>
    currentChatId?: number
    currentChat?: ChatInfo
    message?: ChatMessage
    loading?: boolean
    reasoningName: 'sql_answer' | 'chart_answer' | Array<'sql_answer' | 'chart_answer'>
  }>(),
  {
    recordId: undefined,
    chatList: () => [],
    currentChatId: undefined,
    currentChat: () => new ChatInfo(),
    message: undefined,
    loading: false,
  }
)

const emits = defineEmits([
  'avatarState',
  'finish',
  'error',
  'stop',
  'scrollBottom',
  'update:loading',
  'update:chatList',
  'update:currentChat',
  'update:currentChatId',
])

const index = computed(() => {
  if (props.message?.index) {
    return props.message.index
  }
  if (props.message?.index === 0) {
    return 0
  }
  return -1
})

const _currentChatId = computed({
  get() {
    return props.currentChatId
  },
  set(v) {
    emits('update:currentChatId', v)
  },
})

const _currentChat = computed({
  get() {
    return props.currentChat
  },
  set(v) {
    emits('update:currentChat', v)
  },
})

const _chatList = computed({
  get() {
    return props.chatList
  },
  set(v) {
    emits('update:chatList', v)
  },
})

const _loading = computed({
  get() {
    return props.loading
  },
  set(v) {
    emits('update:loading', v)
  },
})

const avatarRequest = useAvatarRequest(event => emits('avatarState', event))
watch([() => props.currentChat, () => props.message?.record], () => {
  stop()
  progress.value = undefined
  loadHistoricalData()
})
const stopFlag = ref(false)
const progress = ref<ChatProgress>()
let receivedResult: ChatRecord | undefined
let disposed = false

const sendMessage = async () => {
  stopFlag.value = false
  loadingData.value = false
  receivedResult = undefined
  progress.value = createProgress()
  const currentProgress = progress.value
  _loading.value = true

  if (index.value < 0) {
    _loading.value = false
    return
  }

  const currentRecord: ChatRecord = _currentChat.value.records[index.value]
  currentRecord.chart_warning = undefined

  let error: boolean = false
  if (_currentChatId.value === undefined) {
    error = true
  }
  if (error) { _loading.value = false; return }
  const requestChat = _currentChat.value
  const requestIndex = index.value
  const run = avatarRequest.begin(_currentChatId.value, requestIndex,
    () => _currentChat.value === requestChat && index.value === requestIndex &&
      props.message?.record === currentRecord && requestChat.records[requestIndex] === currentRecord)

  let resultLoad: Promise<unknown> | undefined
  let resultLoadFailed = false
  let resultLoaded = false
  let emptyResult = false
  function keepReceivedResult() {
    currentRecord.chart_warning ||= '连接已中断，已保留查询结果；图表更新可能未完成'
    endProgress(currentProgress, 'stopped', '查询结果已返回，连接已中断')
    run.update('stopped')
    emits('stop')
  }

  try {
    const controller = run.controller
    const param = {
      question: currentRecord.question,
      chat_id: _currentChatId.value,
    }
    const response = await questionApi.add(param, controller)
    if (!run.current()) return
    if (!response.ok) {
      const error = { code: response.status, message: response.status === 401 ? '登录状态已失效，请重新登录后重试' : '问数服务请求失败' }
      currentRecord.error = JSON.stringify(error)
      throw error
    }
    const reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8')

    let sql_answer = ''
    let chart_answer = ''

    let tempResult = ''

    while (true) {
      if (stopFlag.value) {
        controller.abort()
        break
      }

      const { done, value } = await reader.read()
      if (!run.current()) return
      if (done) {
        if (resultLoad) await resultLoad
        if (!run.current()) return
        if (resultLoaded) {
          keepReceivedResult()
          break
        }
        endProgress(currentProgress, 'failed', '连接已中断，未收到完成结果')
        currentRecord.error = '连接已中断，未收到完成结果，请重试'
        run.update('error')
        emits('error', currentRecord.id)
        break
      }

      let chunk = decoder.decode(value, { stream: true })
      tempResult += chunk
      const split = tempResult.match(/data:.*}\n\n/g)
      if (split) {
        chunk = split.join('')
        tempResult = tempResult.replace(chunk, '')
      } else {
        continue
      }
      if (chunk && chunk.startsWith('data:{')) {
        if (split) {
          for (const str of split) {
            let data
            try {
              data = JSONBig.parse(str.replace('data:{', '{'))
            } catch (err) {
              console.error('JSON string:', str)
              throw err
            }

            if (!run.current()) return
            receiveProgress(currentProgress, data)
            if (data.type !== 'finish') run.event('chart', data)
            if (data.code && data.code !== 200) {
              endProgress(currentProgress, 'failed', progressError(data))
              currentRecord.error = JSON.stringify(data)
              run.update('error')
              emits('error', currentRecord.id)
              ElMessage({
                message: data.msg,
                type: 'error',
                showClose: true,
              })
              _loading.value = false
              return
            }

            switch (data.type) {
              case 'interaction':
                currentRecord.interaction_id = data.interaction_id
                break
              case 'progress':
                // Structured events are handled by receiveProgress above.
                break
              case 'id':
                currentRecord.id = data.id
                break
              case 'regenerate_record_id':
                currentRecord.regenerate_record_id = data.regenerate_record_id
                break
              case 'question':
                currentRecord.question = data.question
                break
              case 'guidance':
                currentRecord.datasource_select_answer = data.content
                break
              case 'info':
                console.info(data.msg)
                break
              case 'brief':
                _currentChat.value.brief = data.brief
                _chatList.value.forEach((c: Chat) => {
                  if (c.id === _currentChat.value.id) {
                    c.brief = _currentChat.value.brief
                  }
                })
                break
              case 'error':
                endProgress(currentProgress, 'failed', progressError(data.content))
                currentRecord.error = data.content
                emits('error', currentRecord.id)
                return
              case 'permission_denied':
                endProgress(currentProgress, 'failed', progressError(data))
                currentRecord.error = JSON.stringify(data)
                emits('error', currentRecord.id)
                _loading.value = false
                return
              case 'cross_source_not_supported':
                endProgress(currentProgress, 'failed', progressError(data))
                currentRecord.error = JSON.stringify(data)
                emits('error', currentRecord.id)
                _loading.value = false
                return
              case 'sql-result':
                if (!currentProgress.structured) currentProgress.message = '正在生成查询语句'
                sql_answer += data.reasoning_content
                currentRecord.sql_answer = sql_answer
                break
              case 'sql':
                currentRecord.sql = data.content
                break
              case 'sql-data':
                if (!currentProgress.structured) currentProgress.message = '正在整理查询结果'
                if (!resultLoad) {
                  resultLoad = getChatData(currentRecord.id, run.current).then(response => {
                    if (!run.current()) return
                    const parsed = typeof response === 'string' ? JSON.parse(response) : response
                    const rows = Array.isArray(parsed) ? parsed : parsed?.data
                    emptyResult = Array.isArray(rows) && rows.length === 0
                    resultLoaded = true
                    receivedResult = currentRecord
                  }).catch(() => {
                    if (!run.current()) return
                    resultLoadFailed = true
                    endProgress(currentProgress, 'failed', '查询结果加载失败，请重试', Date.now(), 'result')
                  })
                }
                break
              case 'chart-result':
                if (!currentProgress.structured) currentProgress.message = '正在生成图表'
                chart_answer += data.reasoning_content
                currentRecord.chart_answer = chart_answer
                break
              case 'chart':
                currentRecord.chart = data.content
                break
              case 'chart-warning':
                currentRecord.chart_warning = typeof data.message === 'string' ? data.message : '图表生成失败，已保留数据明细'
                break
              case 'datasource':
                if (!_currentChat.value.datasource) {
                  _currentChat.value.datasource = data.id
                }
                break
              case 'finish':
                if (resultLoad) {
                  if (currentProgress.outcome === 'running') currentProgress.message = '正在加载查询结果'
                  await resultLoad
                }
                if (!run.current()) return
                if (resultLoadFailed) currentRecord.error = '查询结果加载失败，请重试'
                completeProgress(currentProgress, emptyResult)
                if (currentProgress.outcome === 'succeeded') {
                  run.update('success')
                  emits('finish', currentRecord.id)
                } else {
                  currentRecord.error ||= currentProgress.message
                  run.update('error')
                  emits('error', currentRecord.id)
                }
                return
            }
            await nextTick()
          }
        }
      }
    }
  } catch (error) {
    if (!run.current()) return
    if (resultLoad) await resultLoad
    if (!run.current()) return
    if (resultLoaded && !currentRecord.error) {
      keepReceivedResult()
      return
    }
    endProgress(currentProgress, 'failed', progressError(error))
    run.update('error')
    if (!currentRecord.error) {
      currentRecord.error = 'Error:' + error
    }
    console.error('Error:', error)
    emits('error', currentRecord.id)
  } finally {
    if (run.current()) {
      run.end()
      _loading.value = false
    }
    run.controller.abort()
  }
}

const loadingData = ref(false)

function getChatData(recordId?: number, current?: () => boolean) {
  const targetChat = _currentChat.value
  const targetRecord = props.message?.record
  const isCurrent = current ?? (() => !disposed && _currentChat.value === targetChat && props.message?.record === targetRecord)
  loadingData.value = true
  return chatApi.get_chart_data(recordId).then(response => {
    if (isCurrent()) {
      if (targetRecord && targetRecord.id === recordId) targetRecord.data = response
    }
    return response
  }).finally(() => {
    if (isCurrent()) {
      loadingData.value = false
      emits('scrollBottom')
    }
  })
}

function stop() {
  if (progress.value?.outcome === 'running') {
    endProgress(progress.value, 'stopped', '已停止接收')
    if (receivedResult) receivedResult.chart_warning ||= '已停止接收，已保留查询结果；图表更新可能未完成'
  }
  loadingData.value = false
  if (!avatarRequest.cancel()) return
  stopFlag.value = true
  _loading.value = false
  emits('stop')
}

onBeforeUnmount(() => {
  disposed = true
  stop()
})

function loadHistoricalData() {
  if (props.message?.record?.id && props.message?.record?.finish) {
    getChatData(props.message.record.id).catch(() => { /* Historical results keep their existing error UI. */ })
  }
}
onMounted(loadHistoricalData)

defineExpose({ sendMessage, index: () => index.value, stop })
</script>

<template>
  <BaseAnswer v-if="message" :message="message" :reasoning-name="reasoningName" :loading="_loading" :show-waiting="false">
    <template #progress>
      <ChatProgressCard v-if="progress" :progress="progress" @toggle="progress.expanded = !progress.expanded" @stop="stop" />
    </template>
    <ChartBlock
      v-if="!message.record?.datasource_select_answer || message.record?.sql || message.record?.error"
      style="margin-top: 6px"
      :message="message"
      :record-id="recordId"
      :loading-data="loadingData"
    />
    <p v-else role="status" style="padding: 16px; line-height: 1.8">{{ message.record.datasource_select_answer }}</p>
    <slot></slot>
    <template #tool>
      <slot name="tool"></slot>
    </template>
    <template #footer>
      <slot name="footer"></slot>
    </template>
  </BaseAnswer>
</template>

<style scoped lang="less"></style>
