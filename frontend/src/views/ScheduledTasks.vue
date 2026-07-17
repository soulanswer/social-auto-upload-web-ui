<template>
  <div class="scheduled-tasks-page">
    <div class="page-header">
      <div>
        <h1>定时任务</h1>
        <p class="page-subtitle">管理已导入的多渠道定时发布任务</p>
      </div>
      <el-button @click="fetchTasks" :loading="loading">
        刷新
      </el-button>
    </div>

    <div class="toolbar">
      <el-input
        v-model="keyword"
        placeholder="搜索任务名称、标题或标签"
        clearable
        style="width: 260px"
        @keyup.enter="handleSearch"
      />
      <el-select v-model="status" placeholder="全部状态" clearable style="width: 180px" @change="handleSearch">
        <el-option label="草稿" value="draft" />
        <el-option label="已排期" value="scheduled" />
        <el-option label="派发中" value="dispatching" />
        <el-option label="排队中" value="queued" />
        <el-option label="发布中" value="running" />
        <el-option label="成功" value="success" />
        <el-option label="部分成功" value="partial" />
        <el-option label="失败" value="failed" />
        <el-option label="数据失效" value="invalid" />
      </el-select>
      <el-button type="primary" @click="handleSearch">查询</el-button>
    </div>

    <div class="table-card">
      <el-table v-loading="loading" :data="tasks" style="width: 100%" >
        <el-table-column prop="task_name" label="任务名称" min-width="180" />
        <el-table-column prop="title" label="发布标题" min-width="200" />
        <el-table-column prop="status" label="任务状态" min-width="100">
          <template #default="scope">
            <el-tag :type="statusTagType(scope.row.status)" size="small" effect="dark">
              {{ scope.row.status_label || statusLabel(scope.row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="tag_summary" label="发布标签" min-width="180">
          <template #default="scope">
            {{ scope.row.tag_summary || '-' }}
          </template>
        </el-table-column>
        <el-table-column label="渠道名称" min-width="180">
          <template #default="scope">
            {{ (scope.row.channel_names || []).join('、') || '-' }}
          </template>
        </el-table-column>
        <el-table-column label="账号状态" min-width="200">
          <template #default="scope">
            <ScheduledTaskResultTags :items="scope.row.channel_statuses || []" />
          </template>
        </el-table-column>
        <el-table-column prop="scheduled_at" label="发布时间" width="180">
          <template #default="scope">
            {{ scope.row.scheduled_at || '未设置' }}
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="180" />
        <el-table-column prop="updated_at" label="更新时间" width="180" />
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="scope">
            <div class="action-group">
              <el-button link type="primary" @click="openSchedule(scope.row)">
                {{ scope.row.scheduled_at ? '修改时间' : '设置时间' }}
              </el-button>
              <el-button
                link
                type="warning"
                :disabled="!['draft', 'scheduled','success','partial','failed'].includes(scope.row.status)"
                @click="handleRunNow(scope.row)"
              >
                立即执行
              </el-button>
              <el-button link type="info" @click="openDetail(scope.row)">详情</el-button>
              <el-button
                v-if="scope.row.publish_batch_id"
                link
                type="success"
                @click="goHistory(scope.row.publish_batch_id)"
              >
                历史
              </el-button>
              <el-popconfirm title="确认删除该任务？" @confirm="handleDelete(scope.row)">
                <template #reference>
                  <el-button
                    link
                    type="danger"
                    :disabled="['dispatching', 'queued', 'running'].includes(scope.row.status)"
                  >
                    删除
                  </el-button>
                </template>
              </el-popconfirm>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <div v-if="tasks.length === 0 && !loading" class="empty-wrap">
        <el-empty description="暂无定时任务" />
      </div>

      <div class="pagination-wrap">
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="total"
          layout="prev, pager, next"
          background
          @current-change="fetchTasks"
        />
      </div>
    </div>

    <ScheduleTimeDialog
      v-model="scheduleDialogVisible"
      :task="selectedTask"
      :saving="scheduleSaving"
      @confirm="handleScheduleConfirm"
    />

    <ScheduledTaskDetailDialog
      v-model="detailDialogVisible"
      :task-id="selectedTask?.id || ''"
    />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import { scheduledTasksApi } from '@/api/scheduledTasks'
import ScheduleTimeDialog from '@/components/ScheduleTimeDialog.vue'
import ScheduledTaskDetailDialog from '@/components/ScheduledTaskDetailDialog.vue'
import ScheduledTaskResultTags from '@/components/ScheduledTaskResultTags.vue'

const loading = ref(false)
const scheduleSaving = ref(false)
const tasks = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const keyword = ref('')
const status = ref('')
const selectedTaskId = ref('')
const scheduleDialogVisible = ref(false)
const detailDialogVisible = ref(false)
const router = useRouter()
const selectedTask = computed(() => tasks.value.find(task => task.id === selectedTaskId.value) || null)
const statusLabelMap = {
  draft: '草稿',
  scheduled: '已排期',
  dispatching: '派发中',
  queued: '排队中',
  running: '发布中',
  success: '成功',
  partial: '部分成功',
  failed: '失败',
  cancelled: '已取消',
  invalid: '数据失效',
}

let eventSource = null
let reconnectTimer = null
let pendingRefreshTimer = null
let allowReconnect = true
let fetchSequence = 0

function buildStreamUrl(path) {
  const baseUrl = (import.meta.env.VITE_API_BASE_URL || '').trim()
  if (!baseUrl) return path
  return `${baseUrl.replace(/\/$/, '')}${path}`
}

function closeEventSource() {
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
}

function clearReconnectTimer() {
  if (reconnectTimer) {
    window.clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
}

function clearPendingRefresh() {
  if (pendingRefreshTimer) {
    window.clearTimeout(pendingRefreshTimer)
    pendingRefreshTimer = null
  }
}

function scheduleSilentRefresh(delay = 150) {
  clearPendingRefresh()
  pendingRefreshTimer = window.setTimeout(() => {
    pendingRefreshTimer = null
    fetchTasks({ silent: true })
  }, delay)
}

function scheduleReconnect(delay = 3000) {
  if (!allowReconnect || reconnectTimer) return
  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = null
    if (!allowReconnect || document.hidden) return
    connectSSE()
  }, delay)
}

function connectSSE() {
  if (!allowReconnect || eventSource || typeof EventSource === 'undefined') return
  eventSource = new EventSource(buildStreamUrl('/api/v2/scheduled-tasks/stream'))
  eventSource.onmessage = (event) => {
    if (!event.data) return
    scheduleSilentRefresh()
  }
  eventSource.onerror = () => {
    closeEventSource()
    scheduleReconnect()
  }
}

function handleVisibilityChange() {
  if (!document.hidden) {
    scheduleSilentRefresh(0)
    connectSSE()
  }
}

onMounted(() => {
  fetchTasks()
  connectSSE()
  document.addEventListener('visibilitychange', handleVisibilityChange)
})

/** 拉取定时任务列表 */
async function fetchTasks(options = {}) {
  const normalizedOptions = typeof options === 'object' && options !== null ? options : {}
  const silent = normalizedOptions.silent === true
  const requestId = ++fetchSequence
  if (!silent) {
    loading.value = true
  }
  try {
    const res = await scheduledTasksApi.getTasks({
      page: page.value,
      pageSize,
      keyword: keyword.value.trim(),
      status: status.value,
    })
    if (requestId !== fetchSequence) return
    tasks.value = res.data?.items || []
    total.value = res.data?.total || 0
  } finally {
    if (!silent) {
      loading.value = false
    }
  }
}

/** 手动触发查询 */
function handleSearch() {
  page.value = 1
  fetchTasks()
}

/** 打开设置时间弹窗 */
function openSchedule(task) {
  selectedTaskId.value = task.id
  scheduleDialogVisible.value = true
}

/** 打开详情弹窗 */
function openDetail(task) {
  selectedTaskId.value = task.id
  detailDialogVisible.value = true
}

/** 保存发布时间 */
async function handleScheduleConfirm(scheduledAt) {
  if (!selectedTaskId.value) return
  scheduleSaving.value = true
  try {
    await scheduledTasksApi.scheduleTask(selectedTaskId.value, scheduledAt)
    ElMessage.success('发布时间已保存')
    scheduleDialogVisible.value = false
    fetchTasks({ silent: true })
  } finally {
    scheduleSaving.value = false
  }
}

/** 立即执行任务 */
async function handleRunNow(task) {
  try {
    await ElMessageBox.confirm(
      '执行后将立即开始这条多渠道发布任务，无法撤回。',
      '确认立即执行该任务？',
      {
        type: 'warning',
        confirmButtonText: '立即执行',
        cancelButtonText: '取消',
      }
    )
    await scheduledTasksApi.runNow(task.id)
    ElMessage.success('任务已开始执行')
    fetchTasks({ silent: true })
  } catch (error) {
    if (error !== 'cancel') {
      // 统一由请求拦截器提示，这里不重复报错
    }
  }
}

/** 删除任务 */
async function handleDelete(task) {
  await scheduledTasksApi.deleteTask(task.id)
  ElMessage.success('任务已删除')
  fetchTasks({ silent: true })
}

/** 跳转到现有发布历史详情 */
function goHistory(batchId) {
  router.push(`/publish-history/${batchId}`)
}

function statusLabel(taskStatus) {
  return statusLabelMap[taskStatus] || taskStatus || '-'
}

function statusTagType(taskStatus) {
  if (taskStatus === 'success') return 'success'
  if (taskStatus === 'failed' || taskStatus === 'invalid') return 'danger'
  if (taskStatus === 'partial') return 'warning'
  if (taskStatus === 'dispatching' || taskStatus === 'queued' || taskStatus === 'running') return 'primary'
  return 'info'
}

onBeforeUnmount(() => {
  allowReconnect = false
  closeEventSource()
  clearReconnectTimer()
  clearPendingRefresh()
  document.removeEventListener('visibilitychange', handleVisibilityChange)
})
</script>

<style lang="scss" scoped>
@use '@/styles/variables.scss' as *;

.scheduled-tasks-page {
  padding: 24px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 20px;
}

.page-header h1 {
  margin: 0;
  font-size: 26px;
  color: $text-primary;
}

.page-subtitle {
  margin: 6px 0 0;
  font-size: 14px;
  color: $text-muted;
}

.toolbar {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.table-card {
  padding: 20px;
  border: 1px solid $border;
  border-radius: $radius-card;
  background: $bg-elevated;
}

.action-group {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
}

.action-group :deep(.el-button) {
  margin-left: 0;
  min-height: 20px;
  padding: 0;
  line-height: 1.2;
}

.pagination-wrap {
  display: flex;
  justify-content: center;
  margin-top: 20px;
}

.empty-wrap {
  padding: 40px 0 10px;
}
</style>
