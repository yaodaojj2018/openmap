/** 分析工作台状态（zustand）：中心点、类目选择、任务进度、POI 与等时圈结果。 */

import { create } from 'zustand'
import type {
  CategoryKey,
  IsochroneResult,
  PoiRecord,
  TaskStage,
  TaskStatus,
} from '../types/analysis'

export interface Origin {
  lng: number
  lat: number
}

interface AnalysisState {
  origin: Origin | null
  address: string
  selected: CategoryKey[]
  pois: Partial<Record<CategoryKey, PoiRecord[]>>
  poiLoading: boolean
  poiError: string | null
  isochrone: IsochroneResult | null
  isoLoading: boolean
  isoError: string | null
  /** 异步任务态（docs/02 §5.2）：由 useAnalysisTask 驱动 */
  taskId: string | null
  taskStatus: TaskStatus | null
  taskStage: TaskStage | null
  taskProgress: number
  taskError: string | null
  degradedFlags: string[]
  setOrigin: (origin: Origin | null, address?: string) => void
  toggleCategory: (key: CategoryKey) => void
  setPoiResult: (pois: Partial<Record<CategoryKey, PoiRecord[]>>) => void
  setPoiLoading: (loading: boolean) => void
  setPoiError: (error: string | null) => void
  setIsochrone: (result: IsochroneResult | null) => void
  setIsoLoading: (loading: boolean) => void
  setIsoError: (error: string | null) => void
  taskStarted: (taskId: string, status: TaskStatus, stage: TaskStage, progress: number) => void
  taskStageChanged: (stage: TaskStage, progress?: number) => void
  taskProgressed: (progress: number) => void
  taskFailed: (error: string) => void
  taskReset: () => void
  reset: () => void
}

const ALL: CategoryKey[] = ['medical', 'education', 'shopping', 'elderly']

const TASK_DEFAULTS = {
  taskId: null,
  taskStatus: null,
  taskStage: null,
  taskProgress: 0,
  taskError: null,
  degradedFlags: [] as string[],
}

export const useAnalysisStore = create<AnalysisState>((set) => ({
  origin: null,
  address: '',
  selected: ALL,
  pois: {},
  poiLoading: false,
  poiError: null,
  isochrone: null,
  isoLoading: false,
  isoError: null,
  ...TASK_DEFAULTS,
  // 中心点变化即作废旧任务/等时圈/POI（数据只对当前中心点有效）
  setOrigin: (origin, address) =>
    set((s) => ({ origin, address: address ?? s.address, pois: {}, isochrone: null, ...TASK_DEFAULTS })),
  toggleCategory: (key) =>
    set((s) => ({
      selected: s.selected.includes(key)
        ? s.selected.filter((k) => k !== key)
        : [...s.selected, key],
    })),
  setPoiResult: (pois) => set({ pois, poiError: null }),
  setPoiLoading: (poiLoading) => set({ poiLoading }),
  setPoiError: (poiError) => set({ poiError }),
  setIsochrone: (isochrone) => set({ isochrone, isoError: null }),
  setIsoLoading: (isoLoading) => set({ isoLoading }),
  setIsoError: (isoError) => set({ isoError }),
  taskStarted: (taskId, taskStatus, taskStage, taskProgress) =>
    set({ taskId, taskStatus, taskStage, taskProgress, taskError: null, degradedFlags: [] }),
  taskStageChanged: (stage, progress) =>
    set((s) => ({
      taskStage: stage,
      taskStatus: s.taskStatus === 'pending' ? 'running' : s.taskStatus,
      ...(progress != null && progress > s.taskProgress ? { taskProgress: progress } : {}),
    })),
  taskProgressed: (progress) =>
    set((s) => (progress > s.taskProgress ? { taskProgress: progress } : {})),
  taskFailed: (taskError) => set({ taskStatus: 'failed', taskError }),
  taskReset: () => set(TASK_DEFAULTS),
  reset: () =>
    set({
      origin: null,
      address: '',
      selected: ALL,
      pois: {},
      poiError: null,
      isochrone: null,
      isoError: null,
      ...TASK_DEFAULTS,
    }),
}))
