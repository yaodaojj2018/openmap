/**
 * 百度地图 GL（bmapgl）无官方类型包，M1 以 any 壳声明渐进收紧；
 * M2 引入等时圈图层时在此补充最小接口（Map/Point/Marker/Polygon...）。
 */
declare global {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const BMapGL: any

  interface Window {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    BMapGL?: any
  }
}

export {}
