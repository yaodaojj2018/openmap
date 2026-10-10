/**
 * 主工作台：左侧地图（点选中心点 / POI 打点 / 等时圈多边形 / 盲区叠加），右侧控制面板
 * （地址搜索定位、类目选择、一键体检分析 + 进度、结果列表）；体检完成后下方展开
 * 完整报告（三件套图表 + 覆盖明细 + 盲区摘要 + 导出）。
 */

import { lazy, Suspense, useCallback, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Empty,
  Input,
  List,
  message,
  Progress,
  Row,
  Col,
  Space,
  Spin,
  Switch,
  Tabs,
  Tag,
  Typography,
} from 'antd'
import { SearchOutlined, ReloadOutlined, PlayCircleOutlined } from '@ant-design/icons'
import MapCanvas from '../components/map/MapCanvas'
import OriginMarker from '../components/map/OriginMarker'
import PoiMarkers from '../components/map/PoiMarkers'
import IsochroneLayer from '../components/map/IsochroneLayer'
import BlindspotLayer from '../components/map/BlindspotLayer'
// 报告面板懒加载（docs/02 §6 首屏 ≤2s）：echarts/html2canvas/jspdf 只随首份报告进场
const ReportPanel = lazy(() => import('../components/report/ReportPanel'))
import { CATEGORY_LIST } from '../constants/categories'
import { formatDegradedFlags } from '../constants/degraded'
import { fetchDemoHint, geocode } from '../api/geo'
import { STAGE_LABELS, useAnalysisTask } from '../hooks/useAnalysisTask'
import { useAnalysisStore } from '../stores/analysis'
import type { GeocodeCandidate } from '../types/analysis'

const { Text, Paragraph } = Typography

export default function AnalysisPage() {
  const ak = import.meta.env.VITE_BMAP_AK ?? ''
  const {
    origin,
    entryPoints,
    address,
    selected,
    pois,
    poiLoading,
    poiError,
    isochrone,
    isoLoading,
    isoError,
    report,
    taskStatus,
    taskStage,
    taskProgress,
    taskError,
    degradedFlags,
  } = useAnalysisStore()
  const store = useAnalysisStore
  const { run: runTask } = useAnalysisTask()
  const [query, setQuery] = useState('')
  const [candidates, setCandidates] = useState<GeocodeCandidate[]>([])
  const [searching, setSearching] = useState(false)
  // 方案 A：地图默认只画阈值圈，多级内圈由开关展开（数据/报告口径不变）
  const [showAllLevels, setShowAllLevels] = useState(false)
  // 多源并集：添加入口模式下，地图点选追加第 2/3 个出入口（而非重置单中心点）
  const [addingEntry, setAddingEntry] = useState(false)
  const taskRunning = taskStatus === 'pending' || taskStatus === 'running'

  const applyCandidate = useCallback(
    (cand: GeocodeCandidate) => {
      store.getState().setOrigin({ lng: cand.lng, lat: cand.lat }, cand.address)
      setCandidates([])
    },
    [store],
  )

  const onSearch = useCallback(async () => {
    if (query.trim().length < 2) return
    setSearching(true)
    try {
      const result = await geocode(query.trim())
      setCandidates(result)
      if (result.length === 0) message.warning('未匹配到坐标，可尝试更完整的地址或直接点选地图')
    } catch (err) {
      message.error((err as Error).message)
    } finally {
      setSearching(false)
    }
  }, [query])

  const onPick = useCallback(
    (point: { lng: number; lat: number }) => {
      if (!addingEntry) {
        store.getState().setOrigin(point, '')
        return
      }
      store.getState().addEntryPoint(point)
      if (store.getState().entryPoints.length >= 3) setAddingEntry(false)
    },
    [store, addingEntry],
  )

  /** 一键体检分析：等时圈 → POI → 覆盖漏斗 → 盲区全流水线，SSE 进度，报告落 store */
  const runAnalysis = useCallback(async () => {
    const state = store.getState()
    if (!state.origin) {
      message.warning('请先通过地址搜索或点击地图选择中心点')
      return
    }
    if (state.selected.length === 0) {
      message.warning('请至少选择一个设施类目')
      return
    }
    await runTask({
      lng: state.origin.lng,
      lat: state.origin.lat,
      categories: state.selected,
      ...(state.entryPoints.length > 1 ? { entryPoints: state.entryPoints } : {}),
    })
  }, [runTask, store])

  const loadDemo = useCallback(async () => {
    try {
      const hint = await fetchDemoHint()
      const pts = (hint.entry_points ?? [{ lng: hint.origin.lng, lat: hint.origin.lat }]).map(
        (p) => ({ lng: p.lng, lat: p.lat }),
      )
      store.getState().setOrigin({ lng: hint.origin.lng, lat: hint.origin.lat }, hint.address)
      if (pts.length > 1) store.getState().setEntryPoints(pts)
      setQuery(hint.address)
      await runTask({
        lng: hint.origin.lng,
        lat: hint.origin.lat,
        categories: hint.categories,
        ...(pts.length > 1 ? { entryPoints: pts } : {}),
      })
    } catch (err) {
      message.error((err as Error).message)
    }
  }, [runTask, store])

  const resultTabs = CATEGORY_LIST.filter((c) => pois[c.key]?.length)
  // 阈值圈 = 最大级别（resolve_levels 保证），其余为可按需展开的内圈
  const isoInnerLabels =
    isochrone == null
      ? []
      : isochrone.levels
          .filter((lv) => lv.level_min < Math.max(...isochrone.levels.map((l) => l.level_min)))
          .map((lv) => `${lv.level_min} 分钟`)

  return (
    <>
      <Row gutter={16}>
      <Col xs={24} lg={16}>
        <Card
          title="社区地图（点击地图选择出入口）"
          extra={
            <Text type="secondary">
              {origin
                ? entryPoints.length > 1
                  ? `出入口 ${entryPoints.length} 个（并集等时圈）`
                  : `中心点: ${origin.lng.toFixed(6)}, ${origin.lat.toFixed(6)} (bd09)`
                : '尚未选择中心点'}
            </Text>
          }
          styles={{ body: { height: '68vh' } }}
        >
          <MapCanvas ak={ak} center={{ lng: 116.316628, lat: 39.981909 }} onPick={onPick}>
            <IsochroneLayer showAll={showAllLevels} />
            <BlindspotLayer />
            <OriginMarker />
            <PoiMarkers />
          </MapCanvas>
        </Card>
      </Col>

      <Col xs={24} lg={8}>
        <Space direction="vertical" style={{ width: '100%' }} size={16}>
          <Card title="① 定位社区">
            {!ak && (
              <Alert
                type="info"
                showIcon
                style={{ marginBottom: 12 }}
                message="未配置前端地图 AK，地图不可用；数据链路仍可正常演示"
              />
            )}
            <Space.Compact style={{ width: '100%' }}>
              <Input
                placeholder="输入地址，如：北京市海淀区中关村大街1号"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onPressEnter={onSearch}
              />
              <Button type="primary" icon={<SearchOutlined />} loading={searching} onClick={onSearch}>
                搜索
              </Button>
            </Space.Compact>
            {candidates.length > 0 && (
              <List
                size="small"
                style={{ marginTop: 8 }}
                dataSource={candidates}
                renderItem={(cand) => (
                  <List.Item
                    style={{ cursor: 'pointer' }}
                    onClick={() => applyCandidate(cand)}
                  >
                    <List.Item.Meta
                      title={cand.address}
                      description={`${cand.level || '地标'} · ${cand.lng.toFixed(5)}, ${cand.lat.toFixed(5)}`}
                    />
                  </List.Item>
                )}
              />
            )}
            {address && (
              <Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
                当前地址：{address}
              </Paragraph>
            )}
            {origin && (
              <div style={{ marginTop: 8 }}>
                <Button
                  size="small"
                  type={addingEntry ? 'primary' : 'default'}
                  disabled={entryPoints.length >= 3}
                  onClick={() => setAddingEntry((v) => !v)}
                >
                  {addingEntry ? '点击地图添加入口…' : `＋ 添加入口（${entryPoints.length}/3）`}
                </Button>
                {entryPoints.length > 0 && (
                  <Space wrap style={{ marginTop: 8 }}>
                    {entryPoints.map((p, i) => (
                      <Tag
                        key={`${p.lng}-${p.lat}-${i}`}
                        closable={entryPoints.length > 1}
                        onClose={() => store.getState().removeEntryPoint(i)}
                      >
                        {entryPoints.length > 1 ? `出入口${i + 1}` : '中心点'} {p.lng.toFixed(5)},{' '}
                        {p.lat.toFixed(5)}
                      </Tag>
                    ))}
                  </Space>
                )}
                {addingEntry && (
                  <Text type="secondary" style={{ display: 'block', marginTop: 4 }}>
                    点击地图选择下一个出入口（并集等时圈最多 3 个）
                  </Text>
                )}
              </div>
            )}
          </Card>

          <Card title="② 选择设施类目并开始体检">
            <Space wrap>
              {CATEGORY_LIST.map((cat) => (
                <Checkbox
                  key={cat.key}
                  checked={selected.includes(cat.key)}
                  disabled={taskRunning}
                  onChange={() => store.getState().toggleCategory(cat.key)}
                >
                  <span style={{ color: cat.color }}>{cat.label}</span>
                </Checkbox>
              ))}
            </Space>
            <Space style={{ marginTop: 12 }}>
              <Button
                type="primary"
                icon={<PlayCircleOutlined />}
                loading={taskRunning}
                disabled={!origin}
                onClick={runAnalysis}
              >
                开始体检分析
              </Button>
              <Button icon={<ReloadOutlined />} disabled={taskRunning} onClick={loadDemo}>
                加载示例社区
              </Button>
            </Space>
            {taskRunning && taskStage && (
              <div style={{ marginTop: 12 }}>
                <Progress
                  percent={Math.round(taskProgress * 100)}
                  size="small"
                  status="active"
                  format={() => STAGE_LABELS[taskStage]}
                />
              </div>
            )}
            {taskError && (
              <Alert type="error" showIcon style={{ marginTop: 12 }} message={taskError} />
            )}
            {degradedFlags.length > 0 && (
              <Alert
                type="warning"
                showIcon
                style={{ marginTop: 12 }}
                message={`部分数据降级：${formatDegradedFlags(degradedFlags)}`}
              />
            )}
          </Card>

          <Card title="③ 步行等时圈">
            {isoError && <Alert type="error" showIcon message={isoError} />}
            {isoLoading && <Spin style={{ display: 'block', margin: '24px auto' }} />}
            {!isoLoading && isochrone && (
              <div>
                {isoInnerLabels.length > 0 && (
                  <Space style={{ marginBottom: 8 }}>
                    <Switch size="small" checked={showAllLevels} onChange={setShowAllLevels} />
                    <Text type="secondary">
                      地图显示内圈（{isoInnerLabels.join('、')}），默认仅画阈值圈
                    </Text>
                  </Space>
                )}
                {isochrone.levels.map((lv) => (
                  <div key={lv.level_min}>
                    <Text strong>{lv.level_min} 分钟</Text>
                    <Text type="secondary">
                      {' '}
                      · {lv.area_km2.toFixed(2)} km² · 置信度 {(lv.confidence * 100).toFixed(0)}%
                    </Text>
                  </div>
                ))}
                <Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
                  探测 {isochrone.probe_count} 点 · 矩阵调用 {isochrone.matrix_batches} 次 ·
                  {isochrone.method}
                </Paragraph>
              </div>
            )}
            {!isochrone && !isoLoading && (
              <Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>
                基于批量步行测时的多级可达边界；河流/围墙方向会形成真实凹陷。
                点击"开始体检分析"一键生成。
              </Paragraph>
            )}
          </Card>

          <Card title="④ 检索结果">
            {poiError && <Alert type="error" showIcon message={poiError} />}
            {poiLoading && <Spin style={{ display: 'block', margin: '24px auto' }} />}
            {!poiLoading && resultTabs.length === 0 && (
              <Empty description="暂无数据，请选择中心点后检索" />
            )}
            {!poiLoading && resultTabs.length > 0 && (
              <Tabs
                items={resultTabs.map((cat) => ({
                  key: cat.key,
                  label: `${cat.label} (${pois[cat.key]?.length ?? 0})`,
                  children: (
                    <List
                      size="small"
                      dataSource={pois[cat.key] ?? []}
                      renderItem={(poi) => (
                        <List.Item>
                          <Text style={{ color: cat.color }}>●</Text> {poi.name}
                          {poi.distance_m != null && (
                            <Text type="secondary"> （直线 {(poi.distance_m / 1000).toFixed(2)} km）</Text>
                          )}
                        </List.Item>
                      )}
                    />
                  ),
                }))}
              />
            )}
          </Card>
        </Space>
      </Col>
      </Row>

      {report && (
        <div style={{ marginTop: 16 }}>
          <Suspense fallback={null}>
            <ReportPanel report={report} />
          </Suspense>
        </div>
      )}
    </>
  )
}
