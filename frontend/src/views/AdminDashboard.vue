<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue'

interface PendingApproval {
  session_id: string
  category: string
  order_id: string
  amount: number
  reason: string
  risk_level: string
  timestamp: string
}

interface GraphNode {
  id: string
  label: string
  status: 'idle' | 'active' | 'completed' | 'error'
  x: number
  y: number
}

const pendingApprovals = ref<PendingApproval[]>([])
const activeSessions = ref<Map<string, { currentStep: string; category: string }>>(new Map())
const totalTokens = ref(0)
const totalCost = ref(0)
const ws = ref<WebSocket | null>(null)

// 图节点定义（用于可视化）
const graphNodes = ref<GraphNode[]>([
  { id: 'authenticate', label: '认证', status: 'idle', x: 50, y: 20 },
  { id: 'classify', label: '意图分类', status: 'idle', x: 50, y: 100 },
  { id: 'route', label: '路由', status: 'idle', x: 50, y: 180 },
  { id: 'query_order', label: '查订单', status: 'idle', x: 200, y: 120 },
  { id: 'auto_refund', label: '自动退款', status: 'idle', x: 200, y: 180 },
  { id: 'human_review', label: '人工审批', status: 'idle', x: 350, y: 180 },
  { id: 'execute', label: '执行', status: 'idle', x: 500, y: 180 },
  { id: 'respond', label: '响应', status: 'idle', x: 500, y: 260 },
])

const activeNodeId = ref<string | null>(null)

function connectAdminWS() {
  ws.value = new WebSocket('ws://localhost:8000/ws/admin-dashboard')
  
  ws.value.onmessage = (event) => {
    const data = JSON.parse(event.data)
    
    switch (data.type) {
      case 'node_start':
        // 高亮当前节点
        activeNodeId.value = data.node
        updateNodeStatus(data.node, 'active')
        break
      
      case 'node_complete':
        updateNodeStatus(data.current_step || data.node, 'completed')
        if (activeNodeId.value === data.node) {
          activeNodeId.value = null
        }
        break
      
      case 'human_review_required':
        pendingApprovals.value.push({
          session_id: data.data.order_id,
          category: data.data.category,
          order_id: data.data.order_id,
          amount: data.data.amount,
          reason: data.data.reason,
          risk_level: data.data.risk_level,
          timestamp: data.data.timestamp
        })
        break
      
      case 'token_usage':
        totalTokens.value += data.tokens || 0
        totalCost.value += data.cost || 0
        break
      
      case 'heartbeat':
        if (data.data.active_sessions) {
          activeSessions.value = new Map(Object.entries(data.data.active_sessions))
        }
    }
  }
}

function updateNodeStatus(nodeId: string, status: 'idle' | 'active' | 'completed' | 'error') {
  const node = graphNodes.value.find(n => n.id === nodeId)
  if (node) node.status = status
}

async function handleApproval(sessionId: string, decision: 'approved' | 'rejected') {
  const comment = prompt('请输入审批意见（可选）:') || ''
  
  ws.value?.send(JSON.stringify({
    type: 'approval_decision',
    session_id: sessionId,
    decision: decision,
    comment: comment
  }))
  
  // 从待审批列表移除
  pendingApprovals.value = pendingApprovals.value.filter(a => a.session_id !== sessionId)
}

const statsCards = computed(() => [
  { title: '待审批', value: pendingApprovals.value.length, color: '#ff9800' },
  { title: '活跃会话', value: activeSessions.value.size, color: '#4caf50' },
  { title: '总Token', value: totalTokens.value.toLocaleString(), color: '#2196f3' },
  { title: '预估成本', value: `$${totalCost.value.toFixed(4)}`, color: '#9c27b0' },
])

onMounted(() => connectAdminWS())
onUnmounted(() => ws.value?.close())
</script>

<template>
  <div class="admin-dashboard">
    <!-- 顶部统计卡片 -->
    <div class="stats-row">
      <div v-for="card in statsCards" :key="card.title" class="stat-card"
           :style="{ borderTopColor: card.color }">
        <div class="stat-value">{{ card.value }}</div>
        <div class="stat-title">{{ card.title }}</div>
      </div>
    </div>
    
    <div class="main-content">
      <!-- 左侧：图状态监控 -->
      <div class="panel graph-panel">
        <h3>Agent 工作流监控</h3>
        <div class="graph-container">
          <svg viewBox="0 0 600 320" class="graph-svg">
            <!-- 连线 -->
            <line x1="80" y1="60" x2="80" y2="100" stroke="#ccc" stroke-width="2"/>
            <line x1="80" y1="140" x2="80" y2="180" stroke="#ccc" stroke-width="2"/>
            <!-- 更多连线... -->
            
            <!-- 节点 -->
            <g v-for="node in graphNodes" :key="node.id"
               :transform="`translate(${node.x}, ${node.y})`"
               class="graph-node">
              <rect x="-45" y="-18" width="90" height="36" rx="18"
                    :class="['node-rect', node.status]"
                    :stroke="activeNodeId === node.id ? '#1a73e8' : '#ccc'"
                    :stroke-width="activeNodeId === node.id ? 3 : 1"/>
              <text text-anchor="middle" dy="5"
                    :fill="node.status === 'active' ? '#1a73e8' : '#333'"
                    font-size="12">{{ node.label }}</text>
            </g>
          </svg>
        </div>
        <div class="legend">
          <span><span class="dot idle"></span> 空闲</span>
          <span><span class="dot active"></span> 执行中</span>
          <span><span class="dot completed"></span> 已完成</span>
        </div>
      </div>
      
      <!-- 右侧：待审批列表 -->
      <div class="panel approval-panel">
        <h3>待审批工单</h3>
        <div v-if="pendingApprovals.length === 0" class="empty-state">
          暂无待审批工单
        </div>
        <div v-for="approval in pendingApprovals" :key="approval.session_id"
             class="approval-card"
             :class="approval.risk_level">
          <div class="card-header">
            <span class="order-id">{{ approval.order_id }}</span>
            <span class="risk-badge">{{ approval.risk_level.toUpperCase() }}</span>
          </div>
          <div class="card-body">
            <div class="info-row">
              <span>类型:</span> <strong>{{ approval.category }}</strong>
            </div>
            <div class="info-row">
              <span>金额:</span> <strong>¥{{ approval.amount }}</strong>
            </div>
            <div class="info-row">
              <span>原因:</span> <strong>{{ approval.reason }}</strong>
            </div>
            <div class="info-row">
              <span>时间:</span> <strong>{{ approval.timestamp }}</strong>
            </div>
          </div>
          <div class="card-actions">
            <button class="btn-approve" @click="handleApproval(approval.session_id, 'approved')">
              ✅ 通过
            </button>
            <button class="btn-reject" @click="handleApproval(approval.session_id, 'rejected')">
              ❌ 拒绝
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.admin-dashboard {
  padding: 20px;
  font-family: -apple-system, BlinkMacSystemFont, sans-serif;
  background: #f0f2f5;
  min-height: 100vh;
}

.stats-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 20px;
}

.stat-card {
  background: white;
  padding: 20px;
  border-radius: 8px;
  border-top: 3px solid;
  box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}

.stat-value {
  font-size: 28px;
  font-weight: bold;
  color: #333;
}

.stat-title {
  color: #666;
  font-size: 14px;
  margin-top: 4px;
}

.main-content {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}

.panel {
  background: white;
  border-radius: 8px;
  padding: 20px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}

.panel h3 {
  margin: 0 0 16px;
  color: #333;
}

.graph-svg {
  width: 100%;
  height: auto;
}

.node-rect {
  transition: all 0.3s;
}

.node-rect.idle { fill: #f5f5f5; }
.node-rect.active { fill: #e3f2fd; }
.node-rect.completed { fill: #e8f5e9; }
.node-rect.error { fill: #ffebee; }

.legend {
  display: flex;
  gap: 16px;
  padding: 12px 0;
  font-size: 12px;
  color: #666;
}

.dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  margin-right: 4px;
}

.dot.idle { background: #ccc; }
.dot.active { background: #2196f3; animation: pulse 1.5s infinite; }
.dot.completed { background: #4caf50; }

.approval-card {
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  margin-bottom: 12px;
  overflow: hidden;
}

.approval-card.high { border-left: 4px solid #f44336; }
.approval-card.medium { border-left: 4px solid #ff9800; }

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  background: #fafafa;
}

.risk-badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 12px;
  background: #ffebee;
  color: #c62828;
  font-weight: 600;
}

.card-body {
  padding: 12px 16px;
}

.info-row {
  display: flex;
  justify-content: space-between;
  padding: 4px 0;
  font-size: 13px;
  color: #666;
}

.card-actions {
  display: flex;
  gap: 8px;
  padding: 12px 16px;
  background: #fafafa;
}

.btn-approve, .btn-reject {
  flex: 1;
  padding: 8px;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
}

.btn-approve { background: #e8f5e9; color: #2e7d32; }
.btn-approve:hover { background: #c8e6c9; }
.btn-reject { background: #ffebee; color: #c62828; }
.btn-reject:hover { background: #ffcdd2; }

.empty-state {
  text-align: center;
  color: #999;
  padding: 40px;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.approval-card {
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  margin-bottom: 12px;
  overflow: hidden;
  transition: box-shadow 0.2s;
}

.approval-card:hover {
  box-shadow: 0 2px 8px rgba(0,0,0,0.1);
}

/* 左侧色条 - 风险等级 */
.approval-card.high {
  border-left: 4px solid #f44336;
}

.approval-card.medium {
  border-left: 4px solid #ff9800;
}

.approval-card.low {
  border-left: 4px solid #4caf50;
}

/* 审批卡片内的子元素样式 */
.order-id {
  font-weight: 600;
  color: #333;
}

.risk-badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 12px;
  font-weight: 600;
}

.risk-badge.high {
  background: #ffebee;
  color: #c62828;
}

.risk-badge.medium {
  background: #fff3e0;
  color: #e65100;
}

.risk-badge.low {
  background: #e8f5e9;
  color: #2e7d32;
}

/* 恢复后更新 Dashboard 数据的过渡动画 */
.stat-value {
  transition: all 0.3s ease;
}

/* 过期的待审批项 */
.approval-card.expired {
  opacity: 0.6;
  border-left-color: #999;
}
</style>