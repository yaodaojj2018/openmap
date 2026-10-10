/**
 * 分析任务编排 hook（docs/02 §5.1/§5.3）：创建任务 → SSE 进度 → 报告落 store。
 *
 * 竞态控制（docs/02 §5.2）：服务端单活跃策略自动取消旧任务；本 hook 以 task_id
 * 比对丢弃过期事件，发起新任务即关闭旧 EventSource。SSE 断连时优先利用
 * EventSource 原生重连（自动携带 Last-Event-ID，服务端回放事件缓冲）；
 * 连接被彻底关闭（CLOSED）才降级为 /status 轮询兜底。
 */

import { useCallback, useEffect, useRef } from 'react'
import { createAnalysis, fetchTaskResult, fetchTaskStatus } from '../api/analysis'
import { useAnalysisStore } from '../stores/analysis'
import type {
  AnalysisReport,
  CategoryKey,
  PoiRecord,
  TaskEvent,
  TaskStage,
} from '../types/analysis'

/** 阶段中文名（进度条旁展示，docs/01 §5.4"采样 12/32 方向"级别的细度） */
export const STAGE_LABELS: Record<TaskStage, string> = {
  pending: '准备中',
  resolving: '解析中心点',
  sampling: '路网测时采样',
  fitting: '等时圈拟合',
  poi: '设施检索',
  coverage: '覆盖判定',
  blindspot: '盲区识别',
  completed: '完成',
}

export interface RunAnalysisOptions {
  lng: number
  lat: number
  categories: CategoryKey[]
  minutes?: number
}

export function useAnalysisTask() {
  const esRef = useRef<EventSource | null>(null)
  const pollRef = useRef<number | null>(null)
  const currentIdRef = useRef<string | null>(null)

  const cleanup = useCallback(() => {
    esRef.current?.close()
    esRef.current = null
    if (pollRef.current != null) {
      window.clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [])

  // 组件卸载即断开订阅（任务在服务端继续，重新进入页面可凭 task_id 恢复）
  useEffect(() => cleanup, [cleanup])

  const applyReport = useCallback((report: AnalysisReport) => {
    const s = useAnalysisStore.getState()
    s.setIsochrone(report.isochrone)
    s.setPoiResult(report.poi.categories as Partial<Record<CategoryKey, PoiRecord[]>>)
    s.setReport(report)
    useAnalysisStore.setState({ degradedFlags: report.degraded_flags })
    s.setIsoLoading(false)
    s.setPoiLoading(false)
    s.taskCompleted() // 终态化任务：两条完成路径（SSE 事件/参数复用）统一在此收口
  }, [])

  /** 终态统一处理：completed 拉报告，failed 落错误，cancelled 静默（被新任务取代） */
  const handleEvent = useCallback(
    (ev: TaskEvent) => {
      if (ev.task_id !== currentIdRef.current) return // 过期任务事件，丢弃
      const s = useAnalysisStore.getState()
      switch (ev.event) {
        case 'stage':
          if (ev.stage) s.taskStageChanged(ev.stage, ev.progress)
          break
        case 'progress':
          if (ev.progress != null) s.taskProgressed(ev.progress)
          break
        case 'completed':
          cleanup()
          fetchTaskResult(ev.task_id)
            .then(applyReport)
            .catch((err: Error) => {
              s.taskFailed(err.message)
              s.setIsoLoading(false)
              s.setPoiLoading(false)
            })
          break
        case 'failed':
          cleanup()
          s.taskFailed(ev.error ?? '分析失败，请稍后重试')
          s.setIsoLoading(false)
          s.setPoiLoading(false)
          break
        case 'cancelled':
          cleanup() // 静默放弃：单活跃取消不是错误
          break
      }
    },
    [applyReport, cleanup],
  )

  /** /status 轮询兜底（SSE 彻底不可用时） */
  const startPolling = useCallback(
    (taskId: string) => {
      pollRef.current = window.setInterval(async () => {
        if (currentIdRef.current !== taskId) {
          cleanup()
          return
        }
        try {
          const task = await fetchTaskStatus(taskId)
          handleEvent({ event: 'stage', task_id: taskId, stage: task.stage, progress: task.progress })
          if (task.status === 'completed' || task.status === 'failed' || task.status === 'cancelled') {
            handleEvent({ event: task.status, task_id: taskId, error: task.error })
          }
        } catch {
          // 轮询失败继续下一轮；连续失败最终由任务超时语义兜底
        }
      }, 1000)
    },
    [cleanup, handleEvent],
  )

  const run = useCallback(
    async (opts: RunAnalysisOptions) => {
      cleanup() // 服务端会自动取消旧任务；本地同步弃订
      currentIdRef.current = null
      const s = useAnalysisStore.getState()
      s.taskReset()
      s.setIsoLoading(true)
      s.setPoiLoading(true)
      s.setIsoError(null)
      s.setPoiError(null)
      try {
        const task = await createAnalysis({
          lng: opts.lng,
          lat: opts.lat,
          categories: opts.categories,
          ...(opts.minutes ? { minutes: opts.minutes } : {}),
        })
        currentIdRef.current = task.task_id
        useAnalysisStore.getState().taskStarted(task.task_id, task.status, task.stage, task.progress)

        // 参数复用命中已完成任务：跳过订阅直接取报告（秒级返回）
        if (task.status === 'completed') {
          const report = await fetchTaskResult(task.task_id)
          applyReport(report)
          return
        }

        const es = new EventSource(`/api/v1/analyses/${task.task_id}/events`)
        esRef.current = es
        for (const type of ['stage', 'progress', 'completed', 'failed', 'cancelled'] as const) {
          es.addEventListener(type, (e: MessageEvent<string>) => {
            handleEvent(JSON.parse(e.data) as TaskEvent)
          })
        }
        es.onerror = () => {
          if (currentIdRef.current !== task.task_id) return
          // CONNECTING = 原生重连（Last-Event-ID 续传），交还 EventSource；
          // CLOSED = SSE 彻底不可用 → 轮询兜底
          if (es.readyState === EventSource.CLOSED) {
            cleanup()
            startPolling(task.task_id)
          }
        }
      } catch (err) {
        const message = (err as Error).message
        const st = useAnalysisStore.getState()
        st.taskFailed(message)
        st.setIsoLoading(false)
        st.setPoiLoading(false)
      }
    },
    [applyReport, cleanup, handleEvent, startPolling],
  )

  return { run }
}
