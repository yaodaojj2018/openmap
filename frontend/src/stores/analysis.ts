/** 分析工作台状态（zustand）：中心点、类目选择、POI 结果。 */

import { create } from 'zustand'
import type { CategoryKey, PoiRecord } from '../types/analysis'

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
  setOrigin: (origin: Origin | null, address?: string) => void
  toggleCategory: (key: CategoryKey) => void
  setPoiResult: (pois: Partial<Record<CategoryKey, PoiRecord[]>>) => void
  setPoiLoading: (loading: boolean) => void
  setPoiError: (error: string | null) => void
  reset: () => void
}

const ALL: CategoryKey[] = ['medical', 'education', 'shopping', 'elderly']

export const useAnalysisStore = create<AnalysisState>((set) => ({
  origin: null,
  address: '',
  selected: ALL,
  pois: {},
  poiLoading: false,
  poiError: null,
  setOrigin: (origin, address) =>
    set((s) => ({ origin, address: address ?? s.address, pois: {} })),
  toggleCategory: (key) =>
    set((s) => ({
      selected: s.selected.includes(key)
        ? s.selected.filter((k) => k !== key)
        : [...s.selected, key],
    })),
  setPoiResult: (pois) => set({ pois, poiError: null }),
  setPoiLoading: (poiLoading) => set({ poiLoading }),
  setPoiError: (poiError) => set({ poiError }),
  reset: () => set({ origin: null, address: '', selected: ALL, pois: {}, poiError: null }),
}))
