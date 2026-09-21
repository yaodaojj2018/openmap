/** 百度地图 GL 脚本异步加载（单例 Promise）。 */

/* eslint-disable @typescript-eslint/no-explicit-any */
let loading: Promise<any> | null = null

export function loadBaiduMap(ak: string): Promise<any> {
  if (window.BMapGL) return Promise.resolve(window.BMapGL)
  if (!ak) return Promise.reject(new Error('未配置 VITE_BMAP_AK（前端 JS API 密钥）'))
  if (loading) return loading

  loading = new Promise((resolve, reject) => {
    const callback = '__openmap_bmapgl_ready__'
    ;(window as any)[callback] = () => resolve(window.BMapGL)
    const script = document.createElement('script')
    script.src = `https://api.map.baidu.com/api?v=1.0&type=webgl&ak=${encodeURIComponent(ak)}&callback=${callback}`
    script.onerror = () => {
      loading = null
      reject(new Error('百度地图脚本加载失败，请检查 AK 配置与网络'))
    }
    document.head.appendChild(script)
  })
  return loading
}
