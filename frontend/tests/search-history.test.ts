/**
 * 搜索历史持久化回归测试：钉住「至少保留最近五个、去重、上限淘汰、损坏数据降级」契约。
 */

import { beforeEach, describe, expect, it } from 'vitest'

import {
  MAX_HISTORY,
  STORAGE_KEY,
  addSearchHistory,
  clearSearchHistory,
  readSearchHistory,
} from '../src/utils/searchHistory'

const entry = (n: number) => ({ address: `地址${n}`, lng: 116 + n, lat: 39 + n })

beforeEach(() => {
  localStorage.clear()
})

describe('搜索历史（localStorage）', () => {
  it('无历史时返回空数组', () => {
    expect(readSearchHistory()).toEqual([])
  })

  it('新条目插入最前，并按地址去重', () => {
    addSearchHistory(entry(1))
    addSearchHistory(entry(2))
    addSearchHistory(entry(1)) // 重复地址应被移到最前而非累积
    const h = readSearchHistory()
    expect(h.map((e) => e.address)).toEqual(['地址1', '地址2'])
  })

  it('超过 MAX_HISTORY 只保留最近若干条（满足「至少五个」）', () => {
    for (let i = 0; i < MAX_HISTORY + 3; i++) addSearchHistory(entry(i))
    const h = readSearchHistory()
    expect(h.length).toBe(MAX_HISTORY)
    expect(h[0].address).toBe(`地址${MAX_HISTORY + 2}`)
  })

  it('清除后为空', () => {
    addSearchHistory(entry(1))
    clearSearchHistory()
    expect(readSearchHistory()).toEqual([])
  })

  it('本地数据损坏（非法 JSON）降级为空', () => {
    localStorage.setItem(STORAGE_KEY, '{oops')
    expect(readSearchHistory()).toEqual([])
  })

  it('非法条目（非对象 / 空地址 / 缺坐标）被过滤', () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify([
        { address: '有效地址', lng: 116, lat: 39 },
        { address: '', lng: 116, lat: 39 },
        'not-an-object',
        { address: '缺坐标' },
      ]),
    )
    expect(readSearchHistory()).toEqual([{ address: '有效地址', lng: 116, lat: 39 }])
  })
})
