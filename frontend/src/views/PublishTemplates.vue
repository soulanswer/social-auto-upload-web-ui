<template>
  <div class="publish-templates-page" v-loading="loading">
    <div class="page-header">
      <div>
        <h1 class="page-title">渠道默认值</h1>
        <p class="page-subtitle">统一维护各视频渠道的默认表单值，进入发布页时会优先加载这里的配置</p>
      </div>
      <div class="page-actions">
        <el-button @click="handleResetCurrent">重置当前渠道</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </div>
    </div>

    <div class="templates-layout" v-if="templatePlatforms.length > 0">
      <aside class="platform-sidebar">
        <button
          v-for="platform in templatePlatforms"
          :key="platform.key"
          type="button"
          :class="['platform-item', { active: activePlatformKey === platform.key }]"
          @click="activePlatformKey = platform.key"
        >
          <img :src="platform.logo" :alt="platform.name" class="platform-logo" />
          <div class="platform-copy">
            <div class="platform-name">{{ platform.name }}</div>
            <div class="platform-subtitle">{{ platform.shortName }}</div>
          </div>
        </button>
      </aside>

      <section v-if="currentPlatform" class="template-panel">
        <div class="template-card">
          <div class="panel-header">
            <div class="panel-title-wrap">
              <img :src="currentPlatform.logo" :alt="currentPlatform.name" class="panel-logo" />
              <div>
                <div class="panel-title">{{ currentPlatform.name }}</div>
                <div class="panel-hint">
                  这里只保存渠道默认值；合集、位置、热点等账号专属字段，仍在发布页里按账号设置
                </div>
              </div>
            </div>
          </div>

          <div class="section-card">
            <div class="section-header">
              <div class="section-title">基础信息</div>
              <div class="section-subtitle">这部分会作为进入视频发布页后的基础默认值</div>
            </div>

            <div class="basic-grid">
              <div class="field-card field-card--full">
                <div class="field-label">标题</div>
                <el-input
                  v-model="currentTemplate.title"
                  placeholder="请输入默认标题"
                  maxlength="100"
                  show-word-limit
                />
              </div>

              <div class="field-card field-card--full">
                <div class="field-label">描述</div>
                <el-input
                  v-model="currentTemplate.description"
                  type="textarea"
                  :rows="5"
                  placeholder="请输入默认描述"
                  maxlength="2000"
                  show-word-limit
                />
              </div>

              <div class="field-card field-card--full">
                <div class="field-label">标签</div>
                <div class="field-desc">输入标签内容，按回车确认，支持逗号分割，例如 `#旅行,#美食,#打卡`</div>
                <el-input
                  v-model="templateTagInput"
                  placeholder="输入标签内容，按回车添加，支持逗号分割"
                  @keyup.enter="addTemplateTags"
                  clearable
                />
                <div v-if="currentTemplate.tags && currentTemplate.tags.length > 0" class="tags-list">
                  <el-tag
                    v-for="(tag, index) in currentTemplate.tags"
                    :key="`${currentPlatform.key}-${tag}-${index}`"
                    closable
                    size="small"
                    :disable-transitions="false"
                    @close="removeTemplateTag(index)"
                  >
                    #{{ tag }}
                  </el-tag>
                </div>
              </div>
            </div>
          </div>

          <div class="section-card">
            <div class="section-header">
              <div class="section-title">平台设置</div>
              <div class="section-subtitle">只保留平台级、长期稳定的默认项，账号专属能力仍在发布页里配置</div>
            </div>

            <div class="settings-grid">
              <template v-for="field in currentTemplateFields" :key="field.key">
                <div
                  v-if="!field.visibleWhen || currentTemplate[field.visibleWhen.key] === field.visibleWhen.value"
                  class="field-card"
                >
                  <div class="field-label">
                    <span v-if="field.required" class="field-required">*</span>
                    {{ field.label }}
                  </div>
                  <div v-if="field.description" class="field-desc">{{ field.description }}</div>

                  <el-input
                    v-if="field.type === 'input'"
                    v-model="currentTemplate[field.key]"
                    :placeholder="field.placeholder"
                  />

                  <el-switch
                    v-else-if="field.type === 'switch'"
                    v-model="currentTemplate[field.key]"
                  />

                  <div
                    v-else-if="field.type === 'radio'"
                    class="radio-row"
                    :class="{ 'is-disabled': field.disabledWhen && currentTemplate[field.disabledWhen.key] === field.disabledWhen.value }"
                  >
                    <label
                      v-for="opt in field.options"
                      :key="String(opt.value)"
                      :class="['radio-item', { 'is-disabled': field.disabledWhen && currentTemplate[field.disabledWhen.key] === field.disabledWhen.value }]"
                    >
                      <input
                        v-model="currentTemplate[field.key]"
                        type="radio"
                        :name="`${currentPlatform.key}-${field.key}`"
                        :value="opt.value"
                        :disabled="field.disabledWhen && currentTemplate[field.disabledWhen.key] === field.disabledWhen.value"
                      />
                      <span class="radio-text">{{ opt.label }}</span>
                    </label>
                  </div>

                  <el-select
                    v-else-if="field.type === 'select'"
                    v-model="currentTemplate[field.key]"
                    :placeholder="field.placeholder"
                    clearable
                    style="width: 100%"
                  >
                    <el-option
                      v-for="opt in (field.options || [])"
                      :key="opt.value"
                      :label="opt.label"
                      :value="opt.value"
                    />
                  </el-select>

                  <el-select
                    v-else-if="field.type === 'multiSelect'"
                    v-model="currentTemplate[field.key]"
                    :placeholder="field.placeholder"
                    multiple
                    collapse-tags
                    collapse-tags-tooltip
                    clearable
                    style="width: 100%"
                  >
                    <el-option
                      v-for="opt in (field.options || [])"
                      :key="opt.value"
                      :label="opt.label"
                      :value="opt.value"
                    />
                  </el-select>

                  <el-date-picker
                    v-else-if="field.type === 'datetime'"
                    v-model="currentTemplate[field.key]"
                    type="datetime"
                    :placeholder="field.placeholder"
                    :disabled-date="field.disabledDate"
                    :disabled-hours="field.disabledHours"
                    :disabled-minutes="field.disabledMinutes"
                    value-format="YYYY-MM-DD HH:mm:ss"
                    style="width: 100%"
                  />

                  <el-date-picker
                    v-else-if="field.type === 'date'"
                    v-model="currentTemplate[field.key]"
                    type="date"
                    :placeholder="field.placeholder"
                    :disabled-date="(date) => date > new Date()"
                    value-format="YYYY-MM-DD"
                    style="width: 100%"
                  />

                  <el-cascader
                    v-else-if="field.type === 'cascader'"
                    v-model="currentTemplate[field.key]"
                    :options="field.options || []"
                    :placeholder="field.placeholder"
                    :props="field.props || { expandTrigger: 'hover' }"
                    clearable
                    filterable
                    style="width: 100%"
                  />
                </div>
              </template>
            </div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { settingsApi } from '@/api/v2'
import { platformList } from '@/config/platforms'
import { splitTagInput } from '@/utils/tag-input'

const TEMPLATE_SETTINGS_KEY = 'videoPublishDefaults'
const TEMPLATE_UNSUPPORTED_TYPES = new Set(['poiSelect', 'compilationSelect'])
const TEMPLATE_EXCLUDED_KEYS = new Set(['title', 'description', 'videoFormat'])

function cloneTemplateValue(value) {
  if (Array.isArray(value)) return [...value]
  if (value && typeof value === 'object') return { ...value }
  return value
}

function getTemplateFieldInitialValue(field, platform) {
  const defaults = platform.defaultSettings || {}
  if (Object.prototype.hasOwnProperty.call(defaults, field.key)) {
    return cloneTemplateValue(defaults[field.key])
  }
  if (field.type === 'multiSelect' || field.type === 'cascader') return []
  if (field.type === 'switch') return false
  return ''
}

function getTemplateFields(platform) {
  return (platform.settingsFields || []).filter((field) => {
    if (TEMPLATE_EXCLUDED_KEYS.has(field.key)) return false
    if (TEMPLATE_UNSUPPORTED_TYPES.has(field.type)) return false
    return true
  })
}

function createTemplateConfig(platform) {
  const defaults = platform.defaultSettings || {}
  const config = {
    title: defaults.title || '',
    description: defaults.description || '',
    tags: Array.isArray(defaults.tags) ? [...defaults.tags] : [],
  }

  for (const field of getTemplateFields(platform)) {
    config[field.key] = getTemplateFieldInitialValue(field, platform)
  }

  return config
}

function normalizeTemplateSettings(rawSettings) {
  if (!rawSettings) return {}
  if (typeof rawSettings === 'string') {
    try {
      return JSON.parse(rawSettings)
    } catch (e) {
      console.warn('解析渠道默认值失败:', e)
      return {}
    }
  }
  return rawSettings
}

const loading = ref(false)
const saving = ref(false)
const templatePlatforms = platformList
const activePlatformKey = ref(templatePlatforms[0]?.key || '')
const templateTagInput = ref('')
const templateConfigs = reactive(
  Object.fromEntries(templatePlatforms.map((platform) => [platform.key, createTemplateConfig(platform)])),
)

const currentPlatform = computed(() =>
  templatePlatforms.find((platform) => platform.key === activePlatformKey.value) || null,
)

const currentTemplate = computed(() => {
  if (!currentPlatform.value) return {}
  return templateConfigs[currentPlatform.value.key]
})

const currentTemplateFields = computed(() => {
  if (!currentPlatform.value) return []
  return getTemplateFields(currentPlatform.value)
})

function addTemplateTags() {
  const inputTags = splitTagInput(templateTagInput.value)
  if (inputTags.length === 0) return
  if (!Array.isArray(currentTemplate.value.tags)) {
    currentTemplate.value.tags = []
  }

  let hasInserted = false
  let hasDuplicate = false
  for (const tag of inputTags) {
    if (currentTemplate.value.tags.includes(tag)) {
      hasDuplicate = true
      continue
    }
    currentTemplate.value.tags.push(tag)
    hasInserted = true
  }

  if (!hasInserted && hasDuplicate) {
    ElMessage.warning('标签已存在')
  }
  templateTagInput.value = ''
}

function removeTemplateTag(index) {
  currentTemplate.value.tags.splice(index, 1)
}

function applySavedTemplateSettings(rawSettings) {
  const settings = normalizeTemplateSettings(rawSettings)
  for (const [platformKey, savedConfig] of Object.entries(settings)) {
    if (!templateConfigs[platformKey] || !savedConfig || typeof savedConfig !== 'object') continue
    for (const key of Object.keys(templateConfigs[platformKey])) {
      if (Object.prototype.hasOwnProperty.call(savedConfig, key)) {
        templateConfigs[platformKey][key] = cloneTemplateValue(savedConfig[key])
      }
    }
  }
}

async function fetchTemplateSettings() {
  loading.value = true
  try {
    const res = await settingsApi.getSettings()
    if (res.code === 200 && res.data?.[TEMPLATE_SETTINGS_KEY]) {
      applySavedTemplateSettings(res.data[TEMPLATE_SETTINGS_KEY])
    }
  } catch (e) {
    console.error('加载渠道默认值失败:', e)
    ElMessage.error('加载渠道默认值失败')
  } finally {
    loading.value = false
  }
}

async function handleSave() {
  saving.value = true
  try {
    const payload = JSON.parse(JSON.stringify(templateConfigs))
    const res = await settingsApi.updateSettings({
      [TEMPLATE_SETTINGS_KEY]: payload,
    })
    if (res.code === 200) {
      ElMessage.success('渠道默认值已保存')
    }
  } catch (e) {
    console.error('保存渠道默认值失败:', e)
    ElMessage.error('保存渠道默认值失败')
  } finally {
    saving.value = false
  }
}

function handleResetCurrent() {
  if (!currentPlatform.value) return
  templateConfigs[currentPlatform.value.key] = createTemplateConfig(currentPlatform.value)
  ElMessage.success(`已重置 ${currentPlatform.value.name} 默认值`)
}

watch(activePlatformKey, () => {
  templateTagInput.value = ''
})

onMounted(() => {
  fetchTemplateSettings()
})
</script>

<style lang="scss" scoped>
@use '@/styles/variables.scss' as *;

.publish-templates-page {
  min-height: 100%;
  padding: 24px;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}

.page-title {
  margin: 0;
  font-size: 28px;
  font-weight: 700;
  color: $text-primary;
}

.page-subtitle {
  margin: 8px 0 0;
  font-size: 14px;
  color: $text-secondary;
}

.page-actions {
  display: flex;
  gap: 12px;
  flex-shrink: 0;
}

.templates-layout {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr);
  gap: 20px;
  align-items: stretch;
}

.platform-sidebar,
.template-card {
  background: $bg-elevated;
  border: 1px solid $border;
  border-radius: $radius-lg;
  max-height: calc(100vh - 190px);
  overflow-y: auto;
}

.platform-sidebar {
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  overscroll-behavior: contain;
}

.template-panel {
  min-width: 0;
}

.platform-item {
  width: 100%;
  border: 1px solid transparent;
  border-radius: $radius-base;
  background: transparent;
  color: inherit;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
  cursor: pointer;
  transition: $transition-base;

  &:hover {
    border-color: rgba(255, 255, 255, 0.08);
    background: rgba(255, 255, 255, 0.03);
  }

  &.active {
    border-color: $border-active;
    background: rgba(139, 92, 246, 0.08);
  }
}

.platform-logo,
.panel-logo {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  object-fit: cover;
  flex-shrink: 0;
}

.platform-copy {
  min-width: 0;
  text-align: left;
}

.platform-name,
.panel-title {
  font-size: 15px;
  font-weight: 600;
  color: $text-primary;
}

.platform-subtitle {
  margin-top: 4px;
  font-size: 12px;
  color: $text-secondary;
}

.panel-header {
  margin-bottom: 16px;
  padding-bottom: 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.panel-title-wrap {
  display: flex;
  align-items: center;
  gap: 12px;
}

.panel-hint {
  margin-top: 4px;
  font-size: 13px;
  color: $text-secondary;
}

.template-card {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 20px;
  box-shadow: 0 18px 40px rgba(0, 0, 0, 0.18);
}

.section-card {
  padding: 18px;
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: $radius-base;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.035) 0%, rgba(255, 255, 255, 0.015) 100%);
}

.section-header {
  margin-bottom: 14px;
}

.section-title {
  font-size: 15px;
  font-weight: 700;
  color: $text-primary;
}

.section-subtitle {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.6;
  color: $text-secondary;
}

.basic-grid,
.settings-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

.basic-grid {
  grid-template-columns: 1fr;
}

.settings-grid {
  margin-top: 16px;
}

.field-card {
  padding: 16px;
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: $radius-base;
  background: rgba(255, 255, 255, 0.025);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.02);
}

.field-card--full {
  grid-column: 1 / -1;
}

.field-label {
  margin-bottom: 10px;
  font-size: 14px;
  font-weight: 600;
  color: $text-primary;
}

.field-required {
  color: #f56c6c;
  margin-right: 4px;
}

.field-desc {
  margin: -4px 0 10px;
  font-size: 12px;
  line-height: 1.5;
  color: $text-secondary;
}

.tags-list {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.radio-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;

  &.is-disabled {
    opacity: 0.55;
  }
}

.radio-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  border: 1px solid $border;
  border-radius: $radius-base;

  &.is-disabled {
    cursor: not-allowed;
  }
}

.radio-text {
  font-size: 13px;
  color: $text-secondary;
}

@media (max-width: 1100px) {
  .templates-layout {
    grid-template-columns: 1fr;
  }

  .platform-sidebar {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 768px) {
  .publish-templates-page {
    padding: 16px;
  }

  .page-header {
    flex-direction: column;
    align-items: stretch;
  }

  .page-actions {
    justify-content: flex-end;
  }

  .platform-sidebar,
  .basic-grid,
  .settings-grid {
    grid-template-columns: 1fr;
  }

  .platform-sidebar {
    max-height: 320px;
  }
}
</style>
