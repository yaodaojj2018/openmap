/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 百度地图 JS API AK（浏览器端，域名白名单校验；区别于服务端 AK） */
  readonly VITE_BMAP_AK: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
