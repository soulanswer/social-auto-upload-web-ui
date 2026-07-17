<template>
  <el-dialog
    :model-value="modelValue"
    width="480px"
    :close-on-click-modal="false"
    title="设置发布时间"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="dialog-body">
      <div class="field-row">
        <span class="field-label">任务名称</span>
        <span class="field-value">{{ task?.task_name || '-' }}</span>
      </div>
      <div class="field-row field-row--stack">
        <span class="field-label">发布时间</span>
        <el-date-picker
          v-model="scheduledAt"
          type="datetime"
          value-format="YYYY-MM-DD HH:mm:ss"
          format="YYYY-MM-DD HH:mm:ss"
          placeholder="请选择发布时间"
          :disabled-date="disabledDate"
          style="width: 100%;"
        />
      </div>
    </div>

    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="saving" @click="handleConfirm">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  task: { type: Object, default: null },
  saving: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue', 'confirm'])

const scheduledAt = ref('')

watch(
  () => props.modelValue,
  (visible) => {
    if (visible) {
      scheduledAt.value = props.task?.scheduled_at || ''
    }
  }
)

/** 禁止选择今天之前的日期 */
function disabledDate(date) {
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  return date.getTime() < today.getTime()
}

/** 确认保存发布时间 */
function handleConfirm() {
  if (!scheduledAt.value) {
    ElMessage.warning('请选择发布时间')
    return
  }
  emit('confirm', scheduledAt.value)
}
</script>

<style lang="scss" scoped>
@use '@/styles/variables.scss' as *;

.dialog-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.field-row {
  display: flex;
  align-items: center;
  gap: 12px;

  &--stack {
    align-items: flex-start;
    flex-direction: column;
  }
}

.field-label {
  flex-shrink: 0;
  font-size: 13px;
  color: $text-muted;
}

.field-value {
  font-size: 14px;
  color: $text-primary;
  font-weight: 500;
}
</style>
