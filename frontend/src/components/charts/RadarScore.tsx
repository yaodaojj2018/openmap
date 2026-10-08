/** 雷达图（docs/01 §7 图表端）：四大类覆盖评分一览，面积填充便于看短板。 */

import { useMemo } from 'react'
import EChart from './EChart'
import type { CategoryCoverage } from '../../types/analysis'

interface Props {
  categories: CategoryCoverage[]
}

export default function RadarScore({ categories }: Props) {
  const option = useMemo(
    () => ({
      tooltip: {},
      radar: {
        indicator: categories.map((c) => ({ name: c.label, max: 100 })),
        radius: '65%',
      },
      series: [
        {
          type: 'radar',
          data: [
            {
              value: categories.map((c) => c.score),
              name: '覆盖评分',
              areaStyle: { opacity: 0.25 },
              itemStyle: { color: '#1677ff' },
              lineStyle: { width: 2 },
            },
          ],
        },
      ],
    }),
    [categories],
  )
  return <EChart option={option} height={240} />
}
