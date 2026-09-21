/** axios 实例：统一错误归一化（后端错误体 {code, message} → Error.message）。 */

import axios from 'axios'

export const api = axios.create({ baseURL: '/api/v1', timeout: 30_000 })

api.interceptors.response.use(
  (resp) => resp,
  (error: {
    response?: { data?: { message?: string; detail?: { message?: string } } }
    message: string
  }) => {
    const detail = error.response?.data?.detail?.message ?? error.response?.data?.message
    return Promise.reject(new Error(detail ?? `请求失败: ${error.message}`))
  },
)
