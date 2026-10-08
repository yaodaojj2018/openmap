/** 柱状图（docs/01 §7 图表端）：分类设施"总数 vs 圈内可达"对比，落差即缺口。 */

import { useMemo } from 'react'
import EChart from './EChart'
import type { CategoryCoverage } from '../../types/analysis'

interface Props {
  categories: CategoryCoverage[]
}

export default function BarCount({ categories }: Props) {
  const option = useMemo(
    () => ({
      tooltip: { trigger: 'axis' as const },
      legend: { bottom: 0 },
      grid: { left: 40, right: 16, top: 24, bottom: 32 },
      xAxis: {
        type: 'category' as const,
        data: categories.map((c) => c.label),
        axisLabel: { interval: 0 },
      },
      yAxis: { type: 'value' as const, minInterval: 1 },
      series: [
        {
          name: '检索总数',
          type: 'bar' as const,
          data: categories.map((c) => c.total),
          itemStyle: { color: '#bfbfbf' },
          barMaxWidth: 28,
        },
        {
          name: '圈内可达',
          type: 'bar' as const,
          data: categories.map((c) => c.reachable),
          itemStyle: { color: '#1677ff' },
          barMaxWidth: 28,
        },
      ],
    }),
    [categories],
  )
  return <EChart option={option} height={240} />
}
