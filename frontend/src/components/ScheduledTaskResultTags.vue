<template>
  <div class="result-tags">
    <el-tooltip
      v-for="item in items"
      :key="`${item.display_name}-${item.status}`"
      :content="item.error_message || item.display_name"
      effect="dark"
      placement="top"
      popper-class="scheduled-task-status-tooltip"
      :disabled="!item.error_message"
    >
      <el-tag :type="tagType(item.status)" size="small" effect="dark">
        {{ item.display_name }}
      </el-tag>
    </el-tooltip>
  </div>
</template>

<script setup>
const props = defineProps({
  items: { type: Array, default: () => [] },
})

/** 根据账号级执行状态映射标签颜色 */
function tagType(status) {
  if (status === 'success') return 'success'
  if (status === 'failed' || status === 'invalid') return 'danger'
  if (status === 'running' || status === 'queued' || status === 'dispatching') return 'primary'
  if (status === 'partial') return 'warning'
  return 'info'
}
</script>

<style lang="scss" scoped>
.result-tags {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
}
</style>

<style lang="scss">
.scheduled-task-status-tooltip {
  max-width: 420px;
  padding: 10px 12px;
  border: 1px solid rgba(248, 113, 113, 0.28);
  border-radius: 10px;
  background: linear-gradient(135deg, rgba(63, 20, 24, 0.97), rgba(36, 18, 24, 0.97)) !important;
  color: #fca5a5 !important;
  box-shadow:
    0 10px 28px rgba(0, 0, 0, 0.35),
    0 0 0 1px rgba(248, 113, 113, 0.08) inset;
  font-size: 12px;
  line-height: 1.6;
  word-break: break-all;
}
</style>
