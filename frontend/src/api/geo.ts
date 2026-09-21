/** 地图能力 API 封装：geocode / pois / demo hint。 */

import { api } from './client'
import type { CategoryKey, DemoHint, GeocodeCandidate, PoiSearchResult } from '../types/analysis'

export async function geocode(q: string, city?: string): Promise<GeocodeCandidate[]> {
  const { data } = await api.get<{ candidates: GeocodeCandidate[] }>('/geocode', {
    params: { q, city },
  })
  return data.candidates
}

export async function searchPois(params: {
  lng: number
  lat: number
  crs?: string
  categories: CategoryKey[]
}): Promise<PoiSearchResult> {
  const { data } = await api.get<PoiSearchResult>('/pois', {
    params: { ...params, categories: params.categories.join(',') },
  })
  return data
}

export async function fetchDemoHint(): Promise<DemoHint> {
  const { data } = await api.get<DemoHint>('/demo/hint')
  return data
}
