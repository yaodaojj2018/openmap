/** 分析任务 API：创建（202）/ 状态轮询兜底 / 结果获取（docs/02 §5.3）。SSE 订阅见 useAnalysisTask。 */

import { api } from './client'
import type { AnalysisReport, AnalysisTask, CategoryKey } from '../types/analysis'

export async function createAnalysis(params: {
  lng: number
  lat: number
  crs?: string
  minutes?: number
  categories?: CategoryKey[]
}): Promise<AnalysisTask> {
  const { data } = await api.post<AnalysisTask>('/analyses', {
    origin: { lng: params.lng, lat: params.lat, crs: params.crs ?? 'bd09' },
    ...(params.minutes ? { minutes: params.minutes } : {}),
    ...(params.categories?.length ? { categories: params.categories } : {}),
  })
  return data
}

export async function fetchTaskStatus(taskId: string): Promise<AnalysisTask> {
  const { data } = await api.get<AnalysisTask>(`/analyses/${taskId}/status`)
  return data
}

export async function fetchTaskResult(taskId: string): Promise<AnalysisReport> {
  const { data } = await api.get<AnalysisReport>(`/analyses/${taskId}/result`)
  return data
}
