/**
 * 降级标记（degraded_flags）→ 用户可读文案。
 * 不同 flag 的语义不同，不能套同一句后缀：
 * - poi:<key>:unavailable：类目检索失败，该类按 0 处计（且不计入综合评分与盲区判定）；
 * - coverage:matrix:unavailable：边缘带实测不可用，保留时间场插值判定——数据未清零。
 * 新增 flag 时在后端 pipeline 与此处同步登记，未登记的 key 原样展示以便发现遗漏。
 */

import { CATEGORY_MAP } from './categories'

export function formatDegradedFlag(flag: string): string {
  const poi = flag.match(/^poi:(\w+):unavailable$/)
  if (poi) {
    const label = CATEGORY_MAP[poi[1]]?.label ?? poi[1]
    return `「${label}」类目检索失败，按 0 处计（不计入综合评分与盲区判定）`
  }
  if (flag === 'coverage:matrix:unavailable') {
    return '边缘带实测不可用，保留时间场插值判定（数据未清零）'
  }
  return flag
}

export const formatDegradedFlags = (flags: string[]): string =>
  flags.map(formatDegradedFlag).join('；')
