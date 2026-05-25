<script setup lang="ts">
import { ref, nextTick, onMounted } from 'vue'

interface Message {
  role: 'user' | 'assistant' | 'system'
  content: string
  isStreaming?: boolean
  timestamp: Date
}

const messages = ref<Message[]>([])
const inputText = ref('')
const sessionId = ref('customer-' + Date.now())
const isWaitingApproval = ref(false)
const chatContainer = ref<HTMLElement>()

onMounted(() => {
  messages.value.push({
    role: 'assistant',
    content: '您好！我是企业客服助手。请问有什么可以帮您的？',
    timestamp: new Date()
  })
})

async function processSSEStream(response: Response) {
  const reader = response.body?.getReader()
  if (!reader) throw new Error('No response body')

  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue

      const payload = line.slice(6)

      if (payload === '[DONE]') {
        const streamingMsg = messages.value[messages.value.length - 1]
        if (streamingMsg && streamingMsg.role === 'assistant') {
          streamingMsg.isStreaming = false
        }
        continue
      }

      try {
        const event = JSON.parse(payload)

        switch (event.type) {
          case 'node_start':
            console.log(`[Agent] 节点开始: ${event.node}`)
            break

          case 'node_end':
            console.log(`[Agent] 节点结束: ${event.node}`)
            break

          case 'token': {
            const streamingMsg = messages.value[messages.value.length - 1]
            if (streamingMsg && streamingMsg.role === 'assistant' && streamingMsg.isStreaming) {
              streamingMsg.content += event.content
              scrollToBottom()
            }
            break
          }

          case 'message': {
            const lastMsg = messages.value[messages.value.length - 1]
            if (lastMsg && lastMsg.role === 'assistant' && lastMsg.isStreaming) {
              lastMsg.content = event.content
              lastMsg.isStreaming = false
              scrollToBottom()
            }
            break
          }

          case 'review_required': {
            isWaitingApproval.value = true
            const streamingMsg = messages.value[messages.value.length - 1]
            if (streamingMsg && streamingMsg.role === 'assistant' && streamingMsg.isStreaming) {
              streamingMsg.isStreaming = false
            }
            messages.value.push({
              role: 'system',
              content: '⏳ 您的请求需要人工审核，请稍候...',
              timestamp: new Date()
            })
            break
          }

          case 'error': {
            const streamingMsg = messages.value[messages.value.length - 1]
            if (streamingMsg && streamingMsg.isStreaming) {
              streamingMsg.content = `❌ 错误: ${event.message}`
              streamingMsg.isStreaming = false
            }
            break
          }
        }
      } catch {
        // skip unparseable events
      }
    }
  }
}

async function continueAfterApproval() {
  messages.value.push({
    role: 'assistant',
    content: '',
    isStreaming: true,
    timestamp: new Date()
  })

  try {
    const response = await fetch('http://localhost:8000/chat/stream/continue', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId.value })
    })

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    await processSSEStream(response)
  } catch (err) {
    console.error('继续请求失败:', err)
    const streamingMsg = messages.value[messages.value.length - 1]
    if (streamingMsg && streamingMsg.role === 'assistant' && streamingMsg.isStreaming) {
      streamingMsg.content = '抱歉，请求失败，请重试。'
      streamingMsg.isStreaming = false
    }
  } finally {
    isWaitingApproval.value = false
  }
}

async function sendMessage() {
  if (!inputText.value.trim() || isWaitingApproval.value) return

  const userMessage = inputText.value

  messages.value.push({
    role: 'user',
    content: userMessage,
    timestamp: new Date()
  })

  messages.value.push({
    role: 'assistant',
    content: '',
    isStreaming: true,
    timestamp: new Date()
  })

  inputText.value = ''

  try {
    const response = await fetch('http://localhost:8000/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: userMessage,
        user_id: 'test-user-001',
        session_id: sessionId.value
      })
    })

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    await processSSEStream(response)

    // 如果触发了人工审核，自动连接 continue 端点等待审批结果
    if (isWaitingApproval.value) {
      await continueAfterApproval()
    }
  } catch (err) {
    console.error('请求失败:', err)
    const streamingMsg = messages.value[messages.value.length - 1]
    if (streamingMsg && streamingMsg.role === 'assistant' && streamingMsg.isStreaming) {
      streamingMsg.content = '抱歉，请求失败，请重试。'
      streamingMsg.isStreaming = false
    }
  }
}

function scrollToBottom() {
  nextTick(() => {
    if (chatContainer.value) {
      chatContainer.value.scrollTop = chatContainer.value.scrollHeight
    }
  })
}
</script>

<template>
  <div class="customer-chat">
    <header>
      <h2>企业客服助手</h2>
      <span class="session-badge">会话: {{ sessionId }}</span>
    </header>

    <div class="messages" ref="chatContainer">
      <div v-for="(msg, idx) in messages" :key="idx"
           :class="['message', msg.role, { streaming: msg.isStreaming }]">
        <div class="role-icon">
          {{ msg.role === 'user' ? '👤' : msg.role === 'assistant' ? '🤖' : 'ℹ️' }}
        </div>
        <div class="content">
          {{ msg.content }}
          <span v-if="msg.isStreaming && !msg.content" class="typing-indicator">
            <span></span><span></span><span></span>
          </span>
        </div>
        <div class="time">{{ msg.timestamp.toLocaleTimeString() }}</div>
      </div>
    </div>

    <div class="input-area" v-if="!isWaitingApproval">
      <input
        v-model="inputText"
        @keyup.enter="sendMessage"
        placeholder="输入您的问题..."
        :disabled="isWaitingApproval"
      />
      <button @click="sendMessage" :disabled="isWaitingApproval">发送</button>
    </div>
    <div class="waiting-banner" v-else>
      ⏳ 等待管理员审批中，请稍候...
    </div>
  </div>
</template>

<style scoped>
.customer-chat {
  margin: 0 auto;
  height: 100vh;
  display: flex;
  flex-direction: column;
  font-family: -apple-system, BlinkMacSystemFont, sans-serif;
}

header {
  padding: 16px;
  background: #1a73e8;
  color: white;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.session-badge {
  font-size: 12px;
  background: rgba(255,255,255,0.2);
  padding: 4px 8px;
  border-radius: 4px;
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  background: #f5f7fa;
}

.message {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
  animation: fadeIn 0.3s;
}

.message.user {
  flex-direction: row-reverse;
}

.message.user .content {
  background: #1a73e8;
  color: white;
}

.message.assistant .content {
  background: white;
  border: 1px solid #e0e0e0;
}

.message.system .content {
  background: #fff3cd;
  border: 1px solid #ffc107;
}

.content {
  padding: 10px 14px;
  border-radius: 12px;
  max-width: 70%;
  line-height: 1.5;
  word-break: break-word;
}

.role-icon {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
}

.time {
  font-size: 11px;
  color: #999;
  align-self: flex-end;
}

.input-area {
  display: flex;
  padding: 12px;
  background: white;
  border-top: 1px solid #e0e0e0;
  gap: 8px;
}

.input-area input {
  flex: 1;
  padding: 10px 14px;
  border: 1px solid #ddd;
  border-radius: 24px;
  outline: none;
  font-size: 14px;
}

.input-area input:focus {
  border-color: #1a73e8;
}

.input-area button {
  padding: 10px 20px;
  background: #1a73e8;
  color: white;
  border: none;
  border-radius: 24px;
  cursor: pointer;
  font-size: 14px;
}

.waiting-banner {
  padding: 16px;
  background: #fff3cd;
  text-align: center;
  color: #856404;
  font-weight: 500;
}

.typing-indicator {
  display: inline-flex;
  gap: 3px;
  padding: 4px 0;
}

.typing-indicator span {
  width: 6px;
  height: 6px;
  background: #999;
  border-radius: 50%;
  animation: bounce 1.4s infinite;
}

.typing-indicator span:nth-child(2) { animation-delay: 0.2s; }
.typing-indicator span:nth-child(3) { animation-delay: 0.4s; }

@keyframes bounce {
  0%, 60%, 100% { transform: translateY(0); }
  30% { transform: translateY(-6px); }
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>
