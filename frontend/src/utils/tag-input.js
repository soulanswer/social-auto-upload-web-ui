/**
 * 标签输入工具。
 *
 * 支持场景：
 * 1. 单个标签：`旅行`
 * 2. 带 # 前缀：`#旅行`
 * 3. 逗号批量输入：`#旅行,#美食,#打卡`
 * 4. 中文逗号 / 换行混输：`旅行，美食\n打卡`
 */

/**
 * 规范化单个标签值。
 *
 * - 去掉首尾空白
 * - 去掉前导 `#`
 * - 连续多个 `#` 只保留内容部分
 */
export function normalizeTagValue(raw) {
  if (!raw) return ''
  return String(raw).trim().replace(/^#+/, '').trim()
}

/**
 * 把一段输入拆成标签数组。
 *
 * 规则：
 * - 按英文逗号、中文逗号、换行拆分
 * - 自动去重，保留首次出现顺序
 * - 自动去掉前导 `#`
 */
export function splitTagInput(raw) {
  if (!raw) return []

  const seen = new Set()
  const result = []
  const parts = String(raw).split(/[,\n\r，]+/)

  for (const part of parts) {
    const tag = normalizeTagValue(part)
    if (!tag || seen.has(tag)) continue
    seen.add(tag)
    result.push(tag)
  }

  return result
}
