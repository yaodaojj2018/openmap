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
  entry_points?: { lng: number; lat: number; crs: Crs }[]
  categories: CategoryKey[]
}

export interface IsochroneLevel {
  level_min: number
  /** GeoJSON MultiPolygon：[ [ [ [lng,lat], ... ] ] ]（bd09）；单源 = 1 元素 */
  coordinates: number[][][][]
  area_km2: number
  confidence: number
}

export interface IsochroneResult {
  origin: { lng: number; lat: number; crs: Crs }
  /** 实际参与并集的源点（1~3 个小区出入口） */
  origins?: { lng: number; lat: number; crs: Crs }[]
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
  entry_points?: { lng: number; lat: number; crs: Crs }[]
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

/** ---- 覆盖判定（docs/02 §3.2 三级漏斗，对齐 backend/app/models/coverage.py）---- */

export type CoverageMethod = 'polygon' | 'field' | 'matrix'

export interface FacilityCoverage {
  uid: string
  name: string
  category: string
  /** 估算/实测步行分钟；圈外、实测不可达或阻挡方向未估算为 null */
  est_walk_time_min: number | null
  in_circle: boolean
  method: CoverageMethod
  confidence: number
}

export interface CategoryCoverage {
  category: string
  label: string
  total: number
  reachable: number
  coverage_ratio: number
  avg_walk_time_min: number | null
  score: number
}

export interface CoverageResult {
  threshold_min: number
  facilities: FacilityCoverage[]
  categories: CategoryCoverage[]
  verified_count: number
}

/** ---- 盲区识别（docs/02 §3.3，对齐 backend/app/models/blindspot.py）---- */

export interface BlindspotTypeResult {
  type_key: string
  label: string
  missing_cells: number
  worst_distance_m: number | null
  /** GeoJSON Polygon 环组：[外环, 内环...]（bd09），buffer 平滑后 */
  polygons: number[][][][]
}

export interface BlindspotResult {
  origin: { lng: number; lat: number; crs: Crs }
  extent_m: number
  cell_size_m: number
  grid_side: number
  threshold_m: number
  types: BlindspotTypeResult[]
  max_severity: number
  severe_cells: number
}

/** 完整分析报告（对齐 backend/app/models/report.py；坐标 bd09） */
export interface AnalysisReport {
  task_id: string
  origin: { lng: number; lat: number; crs: Crs }
  /** 等时圈源点（1~3 个小区出入口，前端重连后据此重绘标记） */
  entry_points?: { lng: number; lat: number; crs: Crs }[]
  minutes: number
  isochrone: IsochroneResult
  poi: PoiSearchResult
  /** M3 接入；升级前的旧缓存报告可能缺省 */
  coverage?: CoverageResult | null
  blindspot?: BlindspotResult | null
  /** 综合评分（0-100）= 类目覆盖评分等权平均（检索失败类目不计入）；旧缓存报告可能缺省 */
  overall_score?: number | null
  degraded_flags: string[]
  api_call_stats: Record<string, number>
  generated_at: string
}
