/** 与后端 models 对齐的领域类型（唯一 schema 源为 backend/app/models）。 */

export type Crs = 'bd09' | 'gcj02' | 'wgs84'

export type CategoryKey = 'medical' | 'education' | 'shopping' | 'elderly'

export interface GeocodeCandidate {
  address: string
  lng: number
  lat: number
  level: string
  city: string
  precise: number | null
  confidence: number | null
}

export interface PoiRecord {
  uid: string
  name: string
  lng: number
  lat: number
  address: string
  tag: string
  category: CategoryKey | null
  distance_m: number | null
}

export interface PoiSearchResult {
  origin: { lng: number; lat: number; crs: Crs }
  radius_m: number
  categories: Record<string, PoiRecord[]>
}

export interface DemoHint {
  address: string
  origin: { lng: number; lat: number; crs: Crs }
  categories: CategoryKey[]
}

export interface IsochroneLevel {
  level_min: number
  /** GeoJSON Polygon rings：[ [ [lng,lat], ... ] ]（bd09） */
  coordinates: number[][][]
  area_km2: number
  confidence: number
}

export interface IsochroneResult {
  origin: { lng: number; lat: number; crs: Crs }
  levels: IsochroneLevel[]
  probe_count: number
  matrix_batches: number
  method: string
}
