<template>
  <el-dialog
    :model-value="modelValue"
    width="1180px"
    :close-on-click-modal="false"
    title="任务详情"
    top="5vh"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div v-loading="loading" class="detail-dialog">
      <div v-if="detail" class="summary-card">
        <div class="summary-title">{{ detail.task_name || '-' }}</div>
        <div class="summary-meta">
          <span>
            状态：
            <span :class="['status-badge', statusClass(detail.status)]">
              {{ detail.status_label || detail.status || '-' }}
            </span>
          </span>
          <span>发布时间：{{ detail.scheduled_at || '未设置' }}</span>
          <span>批次ID：{{ detail.publish_batch_id || '-' }}</span>
        </div>
      </div>

      <el-collapse v-if="detail" v-model="activeNames" class="account-collapse">
        <el-collapse-item
          v-for="account in detail.accounts || []"
          :key="account.display_name"
          :name="account.display_name"
        >
          <template #title>
            <div class="collapse-head">
              <span class="head-name">{{ account.display_name }}</span>
              <span :class="['status-badge', statusClass(account.status)]">
                {{ account.status_label || account.status }}
              </span>
              <span class="head-time">开始：{{ formatTime(account.started_at) }}</span>
              <span class="head-time">结束：{{ formatTime(account.finished_at) }}</span>
            </div>
          </template>

          <div class="account-panel">
            <el-descriptions :column="2" border size="small">
              <el-descriptions-item label="渠道">{{ account.platform || '-' }}</el-descriptions-item>
              <el-descriptions-item label="账号">{{ account.account_name || '-' }}</el-descriptions-item>
              <el-descriptions-item label="状态">
                <span :class="['status-badge', statusClass(account.status)]">
                  {{ account.status_label || account.status || '-' }}
                </span>
              </el-descriptions-item>
              <el-descriptions-item label="失败原因">
                <span class="error-text">{{ account.error_message || '-' }}</span>
              </el-descriptions-item>
              <el-descriptions-item label="开始时间">{{ formatTime(account.started_at) }}</el-descriptions-item>
              <el-descriptions-item label="结束时间">{{ formatTime(account.finished_at) }}</el-descriptions-item>
            </el-descriptions>

            <div class="timeline-wrap">
              <div class="timeline-title">操作记录</div>
              <el-timeline v-if="(account.operation_logs || []).length > 0">
                <el-timeline-item
                  v-for="log in account.operation_logs"
                  :key="`${log.step_order}-${log.action}`"
                  :type="timelineType(log.result)"
                  :timestamp="formatTime(log.created_at)"
                >
                  <div class="timeline-item">
                    <div class="timeline-action">{{ log.action }}</div>
                    <div class="timeline-message">{{ log.message }}</div>
                    <div v-if="log.expected_value || log.actual_value" class="timeline-values">
                      <div>期望值：{{ log.expected_value || '-' }}</div>
                      <div>实际值：{{ log.actual_value || '-' }}</div>
                    </div>
                  </div>
                </el-timeline-item>
              </el-timeline>
              <el-empty v-else description="暂无操作记录" />
            </div>
          </div>
        </el-collapse-item>
      </el-collapse>
    </div>
  </el-dialog>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { scheduledTasksApi } from '@/api/scheduledTasks'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  taskId: { type: String, default: '' },
})

const emit = defineEmits(['update:modelValue'])

const loading = ref(false)
const detail = ref(null)
const activeNames = ref([])
let pollTimer = null

const shouldPoll = computed(() =>
  ['dispatching', 'queued', 'running'].includes(detail.value?.status || '')
)

watch(
  () => [props.modelValue, props.taskId],
  async ([visible, taskId]) => {
    stopPolling()
    if (!visible || !taskId) return
    await fetchDetail()
    if (shouldPoll.value) startPolling()
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  stopPolling()
})

/** 拉取详情弹窗数据 */
async function fetchDetail() {
  if (!props.taskId) return
  loading.value = true
  try {
    const res = await scheduledTasksApi.getTaskDetail(props.taskId)
    detail.value = res.data
    // 详情弹窗默认全部收起，由用户按需展开。
    activeNames.value = []
    if (!shouldPoll.value) stopPolling()
  } finally {
    loading.value = false
  }
}

/** 运行中任务每 3 秒刷新一次详情 */
function startPolling() {
  stopPolling()
  pollTimer = window.setInterval(() => {
    fetchDetail()
  }, 3000)
}

/** 停止详情轮询 */
function stopPolling() {
  if (pollTimer) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

function tagType(status) {
  if (status === 'success') return 'success'
  if (status === 'failed' || status === 'invalid') return 'danger'
  if (status === 'running' || status === 'queued' || status === 'dispatching') return 'primary'
  if (status === 'partial') return 'warning'
  return 'info'
}

function timelineType(result) {
  if (result === 'success') return 'success'
  if (result === 'failed') return 'danger'
  if (result === 'warning') return 'warning'
  return 'primary'
}

function statusClass(status) {
  if (status === 'success') return 'is-success'
  if (status === 'failed' || status === 'invalid') return 'is-danger'
  if (status === 'partial') return 'is-warning'
  if (status === 'running' || status === 'queued' || status === 'dispatching') return 'is-primary'
  return 'is-info'
}

function formatTime(value) {
  if (!value) return '-'
  return String(value)
}
</script>

<style lang="scss" scoped>
@use '@/styles/variables.scss' as *;

.detail-dialog {
  min-height: 240px;
}

.summary-card {
  padding: 16px 18px;
  margin-bottom: 16px;
  border: 1px solid $border;
  border-radius: $radius-card;
  background: rgba(255, 255, 255, 0.03);
}

.summary-title {
  font-size: 16px;
  font-weight: 700;
  color: $text-primary;
}

.summary-meta {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  margin-top: 8px;
  font-size: 12px;
  color: $text-secondary;
}


.collapse-head {
  display: grid;
  grid-template-columns: minmax(220px, 320px) 72px 1fr 1fr;
  align-items: center;
  column-gap: 12px;
  width: 100%;
  padding: 0 24px 0 0;
}

.head-name {
  font-weight: 600;
  color: $text-primary;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.head-time {
  font-size: 12px;
  color: $text-muted;
}

.account-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.timeline-wrap {
  padding: 12px 14px;
  border: 1px solid $border;
  border-radius: $radius-card;
  background: rgba(255, 255, 255, 0.02);
}

.timeline-title {
  margin-bottom: 12px;
  font-size: 14px;
  font-weight: 600;
  color: $text-primary;
}

.timeline-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.timeline-action {
  font-size: 13px;
  font-weight: 600;
  color: $text-primary;
}

.timeline-message {
  font-size: 13px;
  color: $text-primary;
  line-height: 1.6;
}

.timeline-values {
  font-size: 12px;
  color: $text-muted;
  line-height: 1.6;
}

.error-text {
  color: #fca5a5;
  font-size: 13px;
  line-height: 1.6;
  word-break: break-all;
}

.status-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 52px;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  line-height: 1.4;

  &.is-success {
    color: #86efac;
    background: rgba(34, 197, 94, 0.16);
  }

  &.is-danger {
    color: #fca5a5;
    background: rgba(239, 68, 68, 0.16);
  }

  &.is-warning {
    color: #fcd34d;
    background: rgba(245, 158, 11, 0.16);
  }

  &.is-primary {
    color: #93c5fd;
    background: rgba(59, 130, 246, 0.16);
  }

  &.is-info {
    color: $text-secondary;
    background: rgba(255, 255, 255, 0.08);
  }
}

:deep(.el-collapse) {
  border-top: none;
  border-bottom: none;
}

:deep(.el-collapse-item) {
  margin-bottom: 10px;
  border: 1px solid $border;
  border-radius: 10px;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.02);
}

:deep(.el-collapse-item__header) {
  min-height: 48px;
  padding: 0 14px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  background: rgba(255, 255, 255, 0.03);
}

:deep(.el-collapse-item__wrap) {
  border-bottom: none;
  background: transparent;
}

:deep(.el-collapse-item__content) {
  padding: 14px;
}

:deep(.el-descriptions__label) {
  color: $text-secondary;
  background: rgba(255, 255, 255, 0.03);
}

:deep(.el-descriptions__content) {
  color: $text-primary;
  background: rgba(255, 255, 255, 0.02);
}
</style>
