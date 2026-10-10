/**
 * 体检报告面板（docs/02 §4.2 components/report）：三件套图表（总分仪表盘 / 覆盖雷达 /
 * 设施柱状）+ 覆盖明细表（docs/01 §8-147：数量、覆盖率、平均耗时、评分）+ 盲区摘要，
 * 附图片 / PDF / JSON 导出（docs/01 §8 交付清单）。导出捕获本组件根节点（含标题），
 * 工具栏置于捕获区外，成品不包含操作按钮。
 */

import { useRef, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  List,
  message,
  Row,
  Space,
  Table,
  Typography,
} from 'antd'
import { FileImageOutlined, FilePdfOutlined, FileTextOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import GaugeOverall from '../charts/GaugeOverall'
import RadarScore from '../charts/RadarScore'
import BarCount from '../charts/BarCount'
import { exportJson, exportPdf, exportPng } from './exporters'
import { formatDegradedFlags } from '../../constants/degraded'
import type { AnalysisReport, CategoryCoverage } from '../../types/analysis'

const { Title, Paragraph, Text } = Typography

const TABLE_COLUMNS: ColumnsType<CategoryCoverage> = [
  { title: '类目', dataIndex: 'label' },
  { title: '数量', dataIndex: 'total', align: 'right' },
  { title: '圈内可达', dataIndex: 'reachable', align: 'right' },
  {
    title: '覆盖率',
    key: 'ratio',
    align: 'right',
    render: (_, r) => `${(r.coverage_ratio * 100).toFixed(0)}%`,
  },
  {
    title: '平均步行',
    key: 'avg',
    align: 'right',
    render: (_, r) => (r.avg_walk_time_min == null ? '--' : `${r.avg_walk_time_min.toFixed(1)} 分`),
  },
  {
    title: '评分',
    dataIndex: 'score',
    align: 'right',
    render: (v: number) => <Text strong>{v.toFixed(0)}</Text>,
  },
]

export default function ReportPanel({ report }: { report: AnalysisReport }) {
  const panelRef = useRef<HTMLDivElement>(null)
  const [pngBusy, setPngBusy] = useState(false)
  const [pdfBusy, setPdfBusy] = useState(false)

  const coverage = report.coverage ?? null
  const blindspot = report.blindspot ?? null
  const categories = coverage?.categories ?? []
  // 评分口径收口在后端（检索失败类目不计入平均，见 pipeline）；前端补算会成为
  // 第二份公式副本（JS/Python 舍入语义还不一致），旧缓存缺省时仪表盘显示空态即可
  const overall = report.overall_score ?? null

  const missingTypes = blindspot?.types.filter((t) => t.missing_cells > 0) ?? []
  const apiStats = Object.entries(report.api_call_stats)
    .map(([key, count]) => `${key} × ${count}`)
    .join(' · ')

  const runExport = async (kind: 'png' | 'pdf') => {
    if (!panelRef.current) return
    const setBusy = kind === 'png' ? setPngBusy : setPdfBusy
    setBusy(true)
    try {
      if (kind === 'png') await exportPng(panelRef.current, report.task_id)
      else await exportPdf(panelRef.current, report.task_id)
    } catch (err) {
      message.error(`导出失败：${(err as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <Space style={{ marginBottom: 12 }}>
        <Button icon={<FileImageOutlined />} loading={pngBusy} onClick={() => runExport('png')}>
          导出图片
        </Button>
        <Button icon={<FilePdfOutlined />} loading={pdfBusy} onClick={() => runExport('pdf')}>
          导出 PDF
        </Button>
        <Button icon={<FileTextOutlined />} onClick={() => exportJson(report)}>
          导出 JSON
        </Button>
      </Space>

      <div ref={panelRef}>
        <Card
          title={`⑤ 体检报告 · ${report.minutes} 分钟生活圈`}
          extra={<Text type="secondary">{report.origin.lng.toFixed(6)}, {report.origin.lat.toFixed(6)} (bd09)</Text>}
        >
          {!coverage && (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
              message="本报告来自旧版本缓存，无覆盖判定 / 盲区 / 评分数据；重新发起体检可获取完整报告"
            />
          )}
          <Row gutter={16}>
            <Col xs={24} sm={8} lg={6}>
              <Title level={5}>综合评分</Title>
              <GaugeOverall score={overall} />
            </Col>
            <Col xs={24} sm={16} lg={9}>
              <Title level={5}>各类覆盖评分</Title>
              {categories.length >= 3 ? (
                <RadarScore categories={categories} />
              ) : (
                <Empty description="类目不足 3 个，雷达图从略" style={{ padding: 48 }} />
              )}
            </Col>
            <Col xs={24} lg={9}>
              <Title level={5}>分类设施与圈内可达</Title>
              {categories.length > 0 ? (
                <BarCount categories={categories} />
              ) : (
                <Empty description="暂无覆盖数据" style={{ padding: 48 }} />
              )}
            </Col>
          </Row>

          {categories.length > 0 && (
            <Row gutter={16} style={{ marginTop: 8 }}>
              <Col xs={24} lg={14}>
                <Title level={5}>覆盖明细（{report.minutes} 分钟圈内口径）</Title>
                <Table
                  size="small"
                  rowKey="category"
                  columns={TABLE_COLUMNS}
                  dataSource={categories}
                  pagination={false}
                />
              </Col>
              <Col xs={24} lg={10}>
                <Title level={5}>
                  盲区识别（{blindspot ? `${blindspot.threshold_m / 1000} km` : '1 km'} 内无设施即缺失）
                </Title>
                <Paragraph type="secondary" style={{ marginTop: -4, marginBottom: 8 }}>
                  灰色区域为「周边 1 公里内无菜市场/药店/小学」的独立口径判定（直线距离），
                  独立于 15 分钟等时圈，可能落在等时圈之外。
                </Paragraph>
                {blindspot == null ? (
                  <Empty description="暂无盲区数据" style={{ padding: 48 }} />
                ) : blindspot.types.length === 0 ? (
                  <Paragraph>关键设施类目未参与本次分析（未选择或检索降级），未进行盲区判定。</Paragraph>
                ) : missingTypes.length === 0 ? (
                  <Paragraph>各类关键设施 1 km 内均有覆盖，未识别出盲区。</Paragraph>
                ) : (
                  <List
                    size="small"
                    dataSource={missingTypes}
                    renderItem={(t) => (
                      <List.Item>
                        <Text>缺 {t.label}</Text>
                        <Text type="secondary">
                          {t.missing_cells} 格
                          {t.worst_distance_m == null
                            ? ' · 全域缺失'
                            : ` · 最远 ${(t.worst_distance_m / 1000).toFixed(2)} km`}
                        </Text>
                      </List.Item>
                    )}
                  />
                )}
                {blindspot != null && blindspot.severe_cells > 0 && (
                  <Alert
                    type="warning"
                    showIcon
                    style={{ marginTop: 8 }}
                    message={`复合盲区 ${blindspot.severe_cells} 格（≥2 类设施同时缺失），建议优先补足`}
                  />
                )}
              </Col>
            </Row>
          )}

          <Paragraph type="secondary" style={{ marginTop: 16, marginBottom: 0 }}>
            生成于 {new Date(report.generated_at).toLocaleString()} · API 调用 {apiStats || '0 次'} ·
            边缘精判 {coverage?.verified_count ?? 0} 处 · 栅格{' '}
            {blindspot ? `${blindspot.grid_side}×${blindspot.grid_side}@${blindspot.cell_size_m}m` : '--'}
            {report.degraded_flags.length > 0 && ` · 降级项：${formatDegradedFlags(report.degraded_flags)}`}
          </Paragraph>
        </Card>
      </div>
    </div>
  )
}
