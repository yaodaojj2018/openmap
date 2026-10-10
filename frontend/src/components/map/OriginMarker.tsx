/** 中心点/出入口标记图层：单源画「中心点」，多源画 1~3 个「出入口」。 */

import { useEffect } from 'react'
import { useAnalysisStore } from '../../stores/analysis'
import { useMap } from './MapCanvas'

export default function OriginMarker() {
  const map = useMap()
  const origin = useAnalysisStore((s) => s.origin)
  const entryPoints = useAnalysisStore((s) => s.entryPoints)

  useEffect(() => {
    if (!map || !origin) return
    const points = entryPoints.length ? entryPoints : [origin]
    const multi = points.length > 1
    const overlays = points.map((p, i) => {
      const marker = new BMapGL.Marker(new BMapGL.Point(p.lng, p.lat))
      const label = new BMapGL.Label(multi ? `出入口 ${i + 1}` : '中心点', {
        offset: new BMapGL.Size(12, -10),
      })
      marker.setLabel(label)
      map.addOverlay(marker)
      return marker
    })
    map.panTo(new BMapGL.Point(points[0].lng, points[0].lat))
    return () => overlays.forEach((o) => map.removeOverlay(o))
  }, [map, origin, entryPoints])

  return null
}
