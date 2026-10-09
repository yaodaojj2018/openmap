/**
 * 等时圈图层：多级多边形蓝色渐变（内圈最深）。
 * 默认仅绘制阈值圈（最外级）保持主视图干净，多级内圈由 showAll 开关展开——
 * 数据照常全量返回（docs/01 §4.2 多级圈嵌套），此处只收敛呈现。
 * 大圈先 addOverlay、小圈后加，保证内圈叠在外圈之上；结果变化时清理重绘。
 */

import { useEffect } from 'react'
import { useAnalysisStore } from '../../stores/analysis'
import { useMap } from './MapCanvas'

/** 按级别升序（内→外）：由深到浅 */
const LEVEL_STYLES = [
  { color: '#0958d9', fillOpacity: 0.30 },
  { color: '#1677ff', fillOpacity: 0.18 },
  { color: '#69b1ff', fillOpacity: 0.10 },
]

interface IsochroneLayerProps {
  /** 展开多级内圈；默认只画阈值圈（最外级），单圈时取最深样式保证醒目 */
  showAll?: boolean
}

export default function IsochroneLayer({ showAll = false }: IsochroneLayerProps) {
  const map = useMap()
  const isochrone = useAnalysisStore((s) => s.isochrone)

  useEffect(() => {
    if (!map || !isochrone) return
    const asc = [...isochrone.levels].sort((a, b) => a.level_min - b.level_min)
    const visible = showAll ? asc : asc.slice(-1)
    // 先加外圈（降序遍历），内圈后加叠在上
    const overlays = [...visible]
      .reverse()
      .map((level, descIdx) => {
        const style = LEVEL_STYLES[visible.length - 1 - descIdx] ?? LEVEL_STYLES[LEVEL_STYLES.length - 1]
        const ring = level.coordinates[0]
        const polygon = new BMapGL.Polygon(
          ring.map(([lng, lat]) => new BMapGL.Point(lng, lat)),
          {
            strokeColor: style.color,
            strokeWeight: 2,
            strokeOpacity: 0.9,
            fillColor: style.color,
            fillOpacity: style.fillOpacity,
          },
        )
        map.addOverlay(polygon)
        return polygon
      })
    return () => overlays.forEach((o) => map.removeOverlay(o))
  }, [map, isochrone, showAll])

  return null
}
