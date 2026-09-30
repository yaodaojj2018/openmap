/**
 * 地图画布：加载 bmapgl、初始化 Map 实例并通过 Context 分发给图层组件。
 * 图层（OriginMarker/PoiMarkers/M2 的 IsochroneLayer）通过 useMap() 组合，本组件不感知业务。
 */

/* eslint-disable @typescript-eslint/no-explicit-any */
import { createContext, useContext, useEffect, useRef, useState } from 'react'
import { Alert, Spin } from 'antd'
import { loadBaiduMap } from '../../hooks/useBaiduMap'

const MapContext = createContext<any>(null)

export function useMap(): any {
  return useContext(MapContext)
}

interface Props {
  ak: string
  center: { lng: number; lat: number }
  zoom?: number
  onPick?: (point: { lng: number; lat: number }) => void
  children?: React.ReactNode
}

export default function MapCanvas({ ak, center, zoom = 14, onPick, children }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const pickRef = useRef(onPick)
  pickRef.current = onPick
  const [map, setMap] = useState<any>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let disposed = false
    loadBaiduMap(ak)
      .then((BMapGL) => {
        if (disposed || !containerRef.current) return
        const instance = new BMapGL.Map(containerRef.current)
        instance.centerAndZoom(new BMapGL.Point(center.lng, center.lat), zoom)
        instance.enableScrollWheelZoom(true)
        // 回调走 ref：上层重新渲染不重建地图实例
        instance.addEventListener('click', (e: { latlng: { lng: number; lat: number } }) => {
          pickRef.current?.(e.latlng)
        })
        setMap(instance)
      })
      .catch((err: Error) => setError(err.message))
    return () => {
      disposed = true
    }
    // 初始化仅一次：center/zoom 变化由外部调用 map 实例方法控制
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (error) {
    return (
      <Alert
        type="warning"
        showIcon
        message="地图无法加载"
        description={`${error}。可在仓库根目录 .env 中配置 VITE_BMAP_AK（需 Referer 白名单包含 localhost），或使用后端 DEMO_MODE 查看数据面板。`}
      />
    )
  }

  return (
    <div style={{ position: 'relative', height: '100%', minHeight: 320 }}>
      <div ref={containerRef} style={{ height: '100%' }} />
      {!map && (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Spin tip="地图加载中..." />
        </div>
      )}
      <MapContext.Provider value={map}>{map ? children : null}</MapContext.Provider>
    </div>
  )
}
