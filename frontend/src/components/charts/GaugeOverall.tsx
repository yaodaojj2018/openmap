/**
 * 总分仪表盘（docs/02 §4.2 charts/GaugeOverall）：综合评分 0-100。
 * score 为 null（旧缓存报告缺省）时指针归零并以"暂无评分"标注，不伪造数值。
 */

import { useMemo } from 'react'
import EChart from './EChart'

interface Props {
  score: number | null
}

const BANDS = [
  [0.6, '#ff4d4f'],
  [0.85, '#faad14'],
  [1, '#52c41a'],
]

export default function GaugeOverall({ score }: Props) {
  const option = useMemo(
    () => ({
      series: [
        {
          type: 'gauge',
          min: 0,
          max: 100,
          startAngle: 210,
          endAngle: -30,
          radius: '92%',
          axisLine: { lineStyle: { width: 14, color: BANDS } },
          pointer: { itemStyle: { color: '#1677ff' }, length: '60%' },
          axisTick: { distance: -14, length: 4, lineStyle: { color: '#fff' } },
          splitLine: { distance: -14, length: 14, lineStyle: { color: '#fff', width: 2 } },
          axisLabel: { distance: 18, color: '#8c8c8c', fontSize: 10 },
          detail: {
            valueAnimation: true,
            formatter: score == null ? '--' : '{value}',
            fontSize: 34,
            offsetCenter: [0, '62%'],
            color: '#262626',
          },
          title: { offsetCenter: [0, '92%'], fontSize: 12, color: '#8c8c8c' },
          data: [{ value: score ?? 0, name: score == null ? '暂无评分' : '综合评分' }],
        },
      ],
    }),
    [score],
  )
  return <EChart option={option} height={200} />
}
