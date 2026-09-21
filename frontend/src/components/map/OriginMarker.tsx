/** 中心点标记图层：随 store 中 origin 增删 marker。 */

import { useEffect } from 'react'
import { useAnalysisStore } from '../../stores/analysis'
import { useMap } from './MapCanvas'

export default function OriginMarker() {
  const map = useMap()
  const origin = useAnalysisStore((s) => s.origin)

  useEffect(() => {
    if (!map || !origin) return
    const marker = new BMapGL.Marker(new BMapGL.Point(origin.lng, origin.lat))
    const label = new BMapGL.Label('中心点', { offset: new BMapGL.Size(12, -10) })
    marker.setLabel(label)
    map.addOverlay(marker)
    map.panTo(new BMapGL.Point(origin.lng, origin.lat))
    return () => map.removeOverlay(marker)
  }, [map, origin])

  return null
}
