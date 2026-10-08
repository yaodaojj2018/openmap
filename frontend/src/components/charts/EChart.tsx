/**
 * ECharts 通用封装（docs/02 §4.2 components/charts）：按需注册图表/组件，
 * 实例只初始化一次；option 变化增量更新，容器尺寸变化联动 resize，卸载销毁。
 * 图表组件（RadarScore/BarCount/GaugeOverall）以 useMemo 后的 option 传入，
 * 避免无关渲染触发重绘（docs/02 §6 图层/组件 memo 化要求）。
 */

import { useEffect, useRef, type CSSProperties } from 'react'
import * as echarts from 'echarts/core'
import { BarChart, GaugeChart, RadarChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([
  BarChart,
  GaugeChart,
  RadarChart,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  CanvasRenderer,
])

export type EChartOption = echarts.EChartsCoreOption

interface Props {
  option: EChartOption
  /** 画布高度（px）；宽度自适应容器 */
  height?: number
  style?: CSSProperties
}

export default function EChart({ option, height = 260, style }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<ReturnType<typeof echarts.init> | null>(null)

  useEffect(() => {
    if (!containerRef.current) return
    const chart = echarts.init(containerRef.current)
    chartRef.current = chart
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(containerRef.current)
    return () => {
      observer.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    chartRef.current?.setOption(option, { notMerge: true })
  }, [option])

  return <div ref={containerRef} style={{ height, width: '100%', ...style }} />
}
