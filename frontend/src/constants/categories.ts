/** 分类体系元数据：与后端 data/config/poi_taxonomy.json 保持一致；color 用于地图标记。 */

import type { CategoryKey } from '../types/analysis'

export interface CategoryMeta {
  key: CategoryKey
  label: string
  color: string
}

export const CATEGORY_LIST: CategoryMeta[] = [
  { key: 'medical', label: '医疗（药店）', color: '#f5222d' },
  { key: 'education', label: '教育（小学）', color: '#1677ff' },
  { key: 'shopping', label: '购物（菜市场）', color: '#52c41a' },
  { key: 'elderly', label: '养老', color: '#722ed1' },
]

export const CATEGORY_MAP: Record<string, CategoryMeta> = Object.fromEntries(
  CATEGORY_LIST.map((c) => [c.key, c]),
)
