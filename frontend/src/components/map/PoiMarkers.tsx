/** POI 打点图层：按类目着色；origin 变化时清理重绘。 */

/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect } from 'react'
import { CATEGORY_MAP } from '../../constants/categories'
import { useAnalysisStore } from '../../stores/analysis'
import { useMap } from './MapCanvas'

export default function PoiMarkers() {
  const map = useMap()
  const pois = useAnalysisStore((s) => s.pois)

  useEffect(() => {
    if (!map) return
    const overlays: any[] = []
    for (const [key, records] of Object.entries(pois)) {
      const color = CATEGORY_MAP[key]?.color ?? '#595959'
      for (const poi of records ?? []) {
        const marker = new BMapGL.Marker(new BMapGL.Point(poi.lng, poi.lat))
        const label = new BMapGL.Label(poi.name, {
          offset: new BMapGL.Size(10, -8),
        })
        label.setStyle({ color, borderColor: color, fontSize: '11px' })
        marker.setLabel(label)
        map.addOverlay(marker)
        overlays.push(marker)
      }
    }
    return () => overlays.forEach((o) => map.removeOverlay(o))
  }, [map, pois])

  return null
}
