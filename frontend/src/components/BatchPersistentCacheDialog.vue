<template>
  <el-dialog
    :model-value="modelValue"
    title="持久化缓存"
    width="900px"
    :close-on-click-modal="false"
    class="batch-cache-dialog"
    @update:model-value="$emit('update:modelValue', $event)"
    @open="onOpen"
  >
    <div class="batch-dialog-body">
      <div class="batch-section batch-accounts">
        <div class="batch-section-header">
          <span class="batch-section-title">选择账号</span>
          <span class="batch-section-count">已选 {{ selectedAccountIds.size }} / {{ accounts.length }}</span>
          <el-button size="small" link type="primary" @click="toggleSelectAll">
            {{ isAllSelected ? '取消全选' : '一键全选' }}
          </el-button>
        </div>

        <el-scrollbar class="batch-account-scrollbar">
          <div class="batch-account-grid">
            <div
              v-for="account in accounts"
              :key="account.id"
              :class="['batch-account-card', { selected: selectedAccountIds.has(account.id), disabled: account.status !== '正常' }]"
              @click="account.status === '正常' && toggleAccount(account.id)"
            >
              <div class="batch-account-avatar">
                <img v-if="account.avatar" :src="proxyAvatar(account.avatar)" :alt="account.name">
                <img v-else :src="getDefaultAvatar(account.name)" :alt="account.name">
              </div>
              <div class="batch-account-info">
                <div class="batch-account-name" :title="account.name">{{ account.name }}</div>
                <div class="batch-account-platform">{{ account.platform }}</div>
              </div>
              <div v-if="selectedAccountIds.has(account.id)" class="batch-account-check">
                <el-icon><Check /></el-icon>
              </div>
            </div>

            <div v-if="accounts.length === 0" class="batch-empty">暂无可选账号</div>
          </div>
        </el-scrollbar>
      </div>

      <div class="batch-section batch-cache">
        <div class="batch-section-header">
          <span class="batch-section-title">缓存地址</span>
          <span class="batch-section-count">{{ selectedAccounts.length }} 条</span>
        </div>

        <el-scrollbar class="cache-scrollbar">
          <div class="cache-panel">
            <el-empty
              v-if="selectedAccounts.length === 0"
              class="cache-empty"
              description="选择左侧账号后，在这里预览缓存地址"
              :image-size="72"
            />

            <div v-else class="cache-account-list">
              <el-card
                v-for="account in selectedAccounts"
                :key="account.id"
                shadow="never"
                class="cache-account-item"
              >
                <div class="cache-account-name">{{ account.name }}</div>
                <div class="cache-account-platform">{{ account.platform }}</div>
                <div class="cache-account-path">{{ buildCachePath(account) }}</div>
              </el-card>
            </div>
          </div>
        </el-scrollbar>
      </div>
    </div>

    <template #footer>
      <div class="batch-footer">
        <span class="batch-footer-hint">仅 UI 预览，后端暂未接入</span>
        <div class="batch-footer-actions">
          <el-button @click="$emit('update:modelValue', false)">取消</el-button>
          <el-button
            type="primary"
            :disabled="selectedAccountIds.size === 0"
            @click="handleApply"
          >
            持久化 {{ selectedAccountIds.size }} 个账号
          </el-button>
        </div>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Check } from '@element-plus/icons-vue'
import { useAccountStore } from '@/stores/account'
import { platformNameToKey } from '@/config/platforms'
import { getDefaultAvatar, proxyAvatar } from '@/utils/avatar'

const props = defineProps({
  modelValue: { type: Boolean, required: true }
})

defineEmits(['update:modelValue'])

const accountStore = useAccountStore()
const accounts = computed(() => accountStore.accounts)
const selectedAccountIds = ref(new Set())
const selectedAccounts = computed(() =>
  accounts.value.filter(account => selectedAccountIds.value.has(account.id))
)

const isAllSelected = computed(() => {
  const validIds = accounts.value.filter(account => account.status === '正常').map(account => account.id)
  if (validIds.length === 0) return false
  return validIds.every(id => selectedAccountIds.value.has(id))
})

function toggleAccount(id) {
  const next = new Set(selectedAccountIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selectedAccountIds.value = next
}

function toggleSelectAll() {
  if (isAllSelected.value) {
    selectedAccountIds.value = new Set()
    return
  }
  const validIds = accounts.value.filter(account => account.status === '正常').map(account => account.id)
  selectedAccountIds.value = new Set(validIds)
}

function onOpen() {
  selectedAccountIds.value = new Set()
}

function buildCachePath(account) {
  const key = platformNameToKey[account.platform] || `platform_${account.type}`
  return `data/browser_profiles/${key}_${account.id}`
}

function handleApply() {
  ElMessage.info('持久化缓存后端能力暂未接入，当前仅完成 UI 预览')
}

watch(() => props.modelValue, (visible) => {
  if (!visible) selectedAccountIds.value = new Set()
})
</script>

<style lang="scss" scoped>
@use '@/styles/variables.scss' as *;

.batch-cache-dialog {
  :deep(.el-dialog__body) {
    padding: 16px 20px;
  }

  .batch-dialog-body {
    display: flex;
    gap: 16px;
    min-height: 480px;
  }

  .batch-section {
    display: flex;
    flex-direction: column;
    background: rgba(0, 0, 0, 0.25);
    border: 1px solid $border;
    border-radius: $radius-card;
    overflow: hidden;
    min-height: 0;
  }

  .batch-accounts {
    flex: 1.4;
  }

  .batch-cache {
    flex: 1;
  }

  .batch-section-header {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px 14px;
    border-bottom: 1px solid $border-light;
    background: rgba(255, 255, 255, 0.02);

    .batch-section-title {
      font-size: 13px;
      font-weight: 600;
      color: $text-primary;
    }

    .batch-section-count {
      font-size: 12px;
      color: $brand-start;
      font-weight: 500;
      padding: 2px 8px;
      background: rgba($brand-start, 0.12);
      border-radius: 10px;
    }

    .el-button {
      margin-left: auto;
      font-size: 12px;
    }
  }

  .batch-account-scrollbar,
  .cache-scrollbar {
    flex: 1;
    min-height: 0;
  }

  .cache-scrollbar {
    max-height: 440px;
  }

  :deep(.batch-account-scrollbar .el-scrollbar__wrap),
  :deep(.cache-scrollbar .el-scrollbar__wrap) {
    overflow-x: hidden;
  }

  .batch-account-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
    gap: 8px;
    padding: 12px;
    align-content: start;
  }

  .batch-account-card {
    position: relative;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 10px;
    background: $bg-surface;
    border: 1px solid $border;
    border-radius: $radius-sm;
    cursor: pointer;
    transition: all $transition-fast;

    &:hover:not(.disabled) {
      background: rgba($brand-start, 0.06);
      border-color: $border-active;
    }

    &.selected {
      background: rgba($brand-start, 0.12);
      border-color: $brand-start;
      box-shadow: 0 0 0 1px rgba($brand-start, 0.25);

      .batch-account-name {
        color: #fff;
        font-weight: 600;
      }
    }

    &.disabled {
      opacity: 0.45;
      cursor: not-allowed;
    }
  }

  .batch-account-avatar {
    width: 28px;
    height: 28px;
    border-radius: 50%;
    background: rgba($brand-start, 0.12);
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    overflow: hidden;
    color: #c4b5fd;
    font-size: 12px;
    font-weight: 700;

    img {
      width: 100%;
      height: 100%;
      object-fit: cover;
    }
  }

  .batch-account-info {
    flex: 1;
    min-width: 0;
  }

  .batch-account-name {
    font-size: 12px;
    font-weight: 500;
    color: $text-primary;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    transition: color $transition-fast;
  }

  .batch-account-platform {
    font-size: 10px;
    color: $text-muted;
    margin-top: 2px;
  }

  .batch-account-check {
    position: absolute;
    top: 4px;
    right: 4px;
    width: 16px;
    height: 16px;
    border-radius: 50%;
    background: $brand-start;
    color: #fff;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 10px;
  }

  .cache-panel {
    padding: 14px;
    min-height: auto;
  }

  .cache-empty {
    min-height: 240px;
    border: 1px dashed rgba($brand-start, 0.22);
    border-radius: 12px;
    background: rgba($brand-start, 0.05);

    :deep(.el-empty__description p) {
      color: $text-muted;
      font-size: 12px;
    }
  }

  .cache-account-list {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .cache-account-item {
    border: 1px solid $border;
    border-radius: 12px;
    background: rgba($brand-start, 0.06);

    :deep(.el-card__body) {
      padding: 12px;
    }
  }

  .cache-account-name {
    font-size: 13px;
    font-weight: 600;
    color: $text-primary;
  }

  .cache-account-platform {
    margin-top: 2px;
    margin-bottom: 10px;
    font-size: 11px;
    color: $text-muted;
  }

  .cache-account-path {
    padding: 10px 12px;
    border-radius: 10px;
    background: rgba(0, 0, 0, 0.18);
    border: 1px solid rgba($brand-start, 0.18);
    color: #d8ccff;
    font-size: 12px;
    line-height: 1.5;
    font-family: Consolas, 'Courier New', monospace;
    word-break: break-all;
  }

  .batch-empty {
    grid-column: 1 / -1;
    text-align: center;
    padding: 24px 0;
    color: $text-muted;
    font-size: 13px;
  }

  .batch-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;

    .batch-footer-hint {
      font-size: 12px;
      color: $text-muted;
    }

    .batch-footer-actions {
      display: flex;
      gap: 8px;
    }
  }

  @media (max-width: 960px) {
    .batch-dialog-body {
      flex-direction: column;
      min-height: auto;
    }
  }
}
</style>
