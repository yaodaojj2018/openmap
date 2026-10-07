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

/** ---- 分析任务（docs/02 §5.2/§5.3，对齐 backend/app/models/task.py）---- */

export type TaskStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'

export type TaskStage =
  | 'pending'
  | 'resolving'
  | 'sampling'
  | 'fitting'
  | 'poi'
  | 'coverage'
  | 'blindspot'
  | 'completed'

export interface AnalysisParams {
  origin: { lng: number; lat: number; crs: Crs }
  minutes: number
  categories: CategoryKey[]
}

export interface AnalysisTask {
  task_id: string
  params: AnalysisParams
  status: TaskStatus
  stage: TaskStage
  progress: number
  degraded_flags: string[]
  api_call_stats: Record<string, number>
  created_at: string
  error: string | null
}

/** SSE 事件统一载荷（event 字段区分类型；cancelled 为单活跃取消的扩展事件） */
export interface TaskEvent {
  event: 'stage' | 'progress' | 'completed' | 'failed' | 'cancelled'
  task_id: string
  stage?: TaskStage
  progress?: number
  status?: TaskStatus
  error?: string | null
  degraded_flags?: string[]
}

/** 完整分析报告（对齐 backend/app/models/report.py；坐标 bd09） */
export interface AnalysisReport {
  task_id: string
  origin: { lng: number; lat: number; crs: Crs }
  minutes: number
  isochrone: IsochroneResult
  poi: PoiSearchResult
  degraded_flags: string[]
  api_call_stats: Record<string, number>
  generated_at: string
}
