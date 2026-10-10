/**
 * 回归测试（BF-011）：分析任务在收到 SSE completed 事件后必须进入终态。
 *
 * 历史缺陷：`useAnalysisTask` 的 completed 分支只取报告落 store，未把任务态
 * 置为 completed；进度条（taskStage）与按钮 loading（taskStatus）遂永久停留在
 * 最后一个阶段（blindspot）与 running。本用例复现「事件 → 终态」链路，钉住
 * 「报告落档即任务终态」这一契约（docs/regression-ci.md §6）。
 */

import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as api from '../src/api/analysis'
import { useAnalysisTask } from '../src/hooks/useAnalysisTask'
import { useAnalysisStore } from '../src/stores/analysis'
import type { AnalysisReport, AnalysisTask } from '../src/types/analysis'

vi.mock('../src/api/analysis', () => ({
  createAnalysis: vi.fn(),
  fetchTaskResult: vi.fn(),
  fetchTaskStatus: vi.fn(),
}))

/** 可控的 EventSource 替身：记录实例、可按事件类型手动派发。 */
class FakeEventSource {
  static instances: FakeEventSource[] = []
  static CLOSED = 2
  readyState = 0
  listeners: Record<string, ((e: { data: string }) => void)[]> = {}
  constructor(public url: string) {
    FakeEventSource.instances.push(this)
  }
  addEventListener(type: string, cb: (e: { data: string }) => void) {
    ;(this.listeners[type] ??= []).push(cb)
  }
  removeEventListener() {}
  close() {
    this.readyState = FakeEventSource.CLOSED
  }
  emit(type: string, payload: unknown) {
    for (const cb of this.listeners[type] ?? []) cb({ data: JSON.stringify(payload) })
  }
}

const origin = { lng: 120.110885, lat: 30.342059, crs: 'bd09' as const }

const runningTask: AnalysisTask = {
  task_id: 'task-bf011',
  params: { origin, minutes: 15, categories: ['medical'] },
  status: 'running',
  stage: 'pending',
  progress: 0,
  degraded_flags: [],
  api_call_stats: {},
  created_at: '2026-10-10T00:00:00Z',
  error: null,
}

const report: AnalysisReport = {
  task_id: 'task-bf011',
  origin,
  minutes: 15,
  isochrone: { origin, levels: [], probe_count: 0, matrix_batches: 0, method: 'test' },
  poi: { origin, radius_m: 1500, categories: { medical: [] } },
  coverage: null,
  blindspot: null,
  overall_score: 88.5,
  degraded_flags: [],
  api_call_stats: {},
  generated_at: '2026-10-10T00:00:00Z',
}

beforeEach(() => {
  globalThis.EventSource = FakeEventSource as unknown as typeof EventSource
  FakeEventSource.instances.length = 0
  useAnalysisStore.getState().reset()
  vi.clearAllMocks()
})

describe('useAnalysisTask 任务状态机', () => {
  it('BF-011：SSE completed 事件后任务进入终态，不停留在 running/blindspot', async () => {
    vi.mocked(api.createAnalysis).mockResolvedValue(runningTask)
    vi.mocked(api.fetchTaskResult).mockResolvedValue(report)

    const { result } = renderHook(() => useAnalysisTask())
    await act(async () => {
      await result.current.run({ lng: origin.lng, lat: origin.lat, categories: ['medical'] })
    })

    const es = FakeEventSource.instances.at(-1)
    expect(es).toBeDefined()

    // 推进到用户报障时的卡住点：最后一个阶段 blindspot / running
    act(() => {
      es!.emit('stage', {
        event: 'stage',
        task_id: 'task-bf011',
        stage: 'blindspot',
        progress: 0.92,
        status: 'running',
      })
    })
    expect(useAnalysisStore.getState().taskStage).toBe('blindspot')
    expect(useAnalysisStore.getState().taskStatus).toBe('running')

    // 终态事件：修复前此后无任何状态迁移（按钮永久 loading、进度条停在 blindspot）
    act(() => {
      es!.emit('completed', {
        event: 'completed',
        task_id: 'task-bf011',
        stage: 'completed',
        progress: 1,
        status: 'completed',
        degraded_flags: [],
      })
    })

    await waitFor(() => expect(useAnalysisStore.getState().taskStatus).toBe('completed'))
    expect(useAnalysisStore.getState().taskStage).toBe('completed')
    expect(useAnalysisStore.getState().taskProgress).toBe(1)
    expect(useAnalysisStore.getState().report).not.toBeNull()
  })
})
