/**
 * 报告导出（docs/01 §8 交付清单：图片 / PDF / JSON）。
 * 图片与 PDF 走 html2canvas 捕获报告 DOM（echarts 画布与 antd 组件一并成像），
 * 长报告 PDF 按 A4 高度分页切片；JSON 直接落报告原文（评审与存档口径）。
 */

import html2canvas from 'html2canvas'
import { jsPDF } from 'jspdf'
import type { AnalysisReport } from '../../types/analysis'

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  // revoke 必须延迟：Firefox/Safari 在 click 之后才异步解引用 blob URL，同一 tick
  // 同步 revoke 会中止进行中的下载且无任何报错（Chrome 快照机制下不复现，跨浏览器
  // 测试易漏）。Blob 已在内存中完整生成，延迟释放不影响正确性，只推迟几十秒回收。
  setTimeout(() => URL.revokeObjectURL(url), 40_000)
}

export function exportJson(report: AnalysisReport): void {
  downloadBlob(
    new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' }),
    `openmap-report-${report.task_id}.json`,
  )
}

async function capture(el: HTMLElement) {
  return html2canvas(el, { scale: 2, backgroundColor: '#ffffff', useCORS: true })
}

export async function exportPng(el: HTMLElement, taskId: string): Promise<void> {
  const canvas = await capture(el)
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/png'))
  if (blob) downloadBlob(blob, `openmap-report-${taskId}.png`)
}

export async function exportPdf(el: HTMLElement, taskId: string): Promise<void> {
  const canvas = await capture(el)
  const pdf = new jsPDF('p', 'mm', 'a4')
  const pageWidth = 210
  const pageHeight = 297
  const imgWidth = pageWidth
  const imgHeight = (canvas.height * imgWidth) / canvas.width
  if (imgHeight <= pageHeight) {
    pdf.addImage(canvas, 'PNG', 0, 0, imgWidth, imgHeight)
  } else {
    // 超出一页：addImage 以负 y 逐页上移，超出部分被页边界裁掉
    let remaining = imgHeight
    let offset = 0
    while (remaining > 0) {
      pdf.addImage(canvas, 'PNG', 0, offset, imgWidth, imgHeight)
      remaining -= pageHeight
      offset -= pageHeight
      if (remaining > 0) pdf.addPage()
    }
  }
  pdf.save(`openmap-report-${taskId}.pdf`)
}
