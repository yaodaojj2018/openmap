/**
 * 盲区图层（docs/02 §4.2 map/BlindspotLayer）：各类缺失栅格聚合多边形以灰色
 * 半透明叠加在等时圈之上（叠加变深即复合盲区），每片配"缺 XX"标签标于环质心。
 * 报告变化时清理重绘；missing_cells 为 0 的类型不绘制。
 */

/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect } from 'react'
import { useAnalysisStore } from '../../stores/analysis'
import { useMap } from './MapCanvas'

export default function BlindspotLayer() {
  const map = useMap()
  const blindspot = useAnalysisStore((s) => s.report?.blindspot ?? null)

  useEffect(() => {
    if (!map || !blindspot) return
    const overlays: any[] = []
    for (const type of blindspot.types) {
      if (type.missing_cells === 0) continue
      for (const rings of type.polygons) {
        const ring = rings[0] // 外环即盲区轮廓；内环（洞）不单独描边
        const polygon = new BMapGL.Polygon(
          ring.map(([lng, lat]) => new BMapGL.Point(lng, lat)),
          {
            strokeColor: '#595959',
            strokeWeight: 1.5,
            strokeOpacity: 0.8,
            strokeStyle: 'dashed',
            fillColor: '#595959',
            fillOpacity: 0.22,
          },
        )
        map.addOverlay(polygon)
        overlays.push(polygon)

        const cx = ring.reduce((sum, [lng]) => sum + lng, 0) / ring.length
        const cy = ring.reduce((sum, [, lat]) => sum + lat, 0) / ring.length
        const label = new BMapGL.Label(`缺 ${type.label}`, {
          position: new BMapGL.Point(cx, cy),
        })
        label.setStyle({
          color: '#595959',
          borderColor: '#8c8c8c',
          backgroundColor: 'rgba(255,255,255,0.82)',
          fontSize: '11px',
        })
        map.addOverlay(label)
        overlays.push(label)
      }
    }
    return () => overlays.forEach((o) => map.removeOverlay(o))
  }, [map, blindspot])

  return null
}
