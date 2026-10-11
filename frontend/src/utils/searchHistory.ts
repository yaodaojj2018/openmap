/** 地址搜索历史：地址 + 解析坐标，持久化到 localStorage（纯函数，便于单测）。 */

export interface SearchHistoryEntry {
  address: string
  lng: number
  lat: number
}

export const STORAGE_KEY = 'openmap.search_history'
/** 保留条数上限（需求要求「至少五个」，取 8 留有余量，超出自动淘汰最旧） */
export const MAX_HISTORY = 8

/** 校验单条历史：地址非空、坐标为有限数值，抵御本地数据被手改/损坏 */
function isEntry(value: unknown): value is SearchHistoryEntry {
  if (typeof value !== 'object' || value === null) return false
  const e = value as Record<string, unknown>
  return (
    typeof e.address === 'string' &&
    e.address.trim().length > 0 &&
    typeof e.lng === 'number' &&
    Number.isFinite(e.lng) &&
    typeof e.lat === 'number' &&
    Number.isFinite(e.lat)
  )
}

export function readSearchHistory(): SearchHistoryEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.filter(isEntry) : []
  } catch {
    // 本地存储被禁用或数据损坏：降级为空历史，不影响搜索定位
    return []
  }
}

export function addSearchHistory(entry: SearchHistoryEntry): SearchHistoryEntry[] {
  const address = entry.address.trim()
  if (!address) return readSearchHistory()
  const next = [
    { address, lng: entry.lng, lat: entry.lat },
    ...readSearchHistory().filter((e) => e.address !== address),
  ].slice(0, MAX_HISTORY)
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
  } catch {
    // 写入失败（隐私模式/配额）静默降级：本次仍返回内存结果
  }
  return next
}

export function clearSearchHistory(): void {
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch {
    // 忽略
  }
}
