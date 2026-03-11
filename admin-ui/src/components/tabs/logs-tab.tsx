import { useEffect, useMemo, useRef, useState } from 'react'
import { RefreshCw, Search, Pause, Play, Trash2 } from 'lucide-react'
import { toast } from 'sonner'

import { getRuntimeLogs } from '@/api/credentials'
import type { RuntimeLogEntry } from '@/types/api'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'

const MAX_RENDERED_ENTRIES = 1000
const POLL_INTERVAL_MS = 2000

function levelClass(level: string): string {
  switch (level) {
    case 'ERROR':
    case 'CRITICAL':
      return 'text-red-500'
    case 'WARNING':
      return 'text-yellow-500'
    case 'INFO':
      return 'text-blue-500'
    default:
      return 'text-muted-foreground'
  }
}

function formatTimestamp(ts: string): string {
  try {
    return new Date(ts).toLocaleString('zh-CN', { hour12: false })
  } catch {
    return ts
  }
}

export function LogsTab() {
  const [entries, setEntries] = useState<RuntimeLogEntry[]>([])
  const [cursor, setCursor] = useState(0)
  const [bufferSize, setBufferSize] = useState(0)
  const [loading, setLoading] = useState(false)
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [level, setLevel] = useState('')
  const [limit, setLimit] = useState(100)

  const listRef = useRef<HTMLDivElement>(null)

  const title = useMemo(() => {
    if (bufferSize <= 0) return '最近运行日志'
    return `最近运行日志（缓冲 ${bufferSize} 行）`
  }, [bufferSize])

  const scrollToBottom = () => {
    const el = listRef.current
    if (el) {
      el.scrollTop = el.scrollHeight
    }
  }

  const reloadTail = async () => {
    setLoading(true)
    try {
      const data = await getRuntimeLogs({
        limit,
        level: level || undefined,
        q: keyword || undefined,
      })
      setEntries(data.entries)
      setCursor(data.nextCursor)
      setBufferSize(data.bufferSize)
      requestAnimationFrame(scrollToBottom)
    } catch {
      toast.error('读取日志失败')
    } finally {
      setLoading(false)
    }
  }

  const fetchIncremental = async () => {
    try {
      const data = await getRuntimeLogs({
        cursor,
        limit,
        level: level || undefined,
        q: keyword || undefined,
      })
      if (data.entries.length > 0) {
        setEntries(prev => {
          const merged = [...prev, ...data.entries]
          return merged.slice(-MAX_RENDERED_ENTRIES)
        })
        requestAnimationFrame(scrollToBottom)
      }
      setCursor(data.nextCursor)
      setBufferSize(data.bufferSize)
    } catch {
      // 轮询失败时静默，避免刷屏
    }
  }

  useEffect(() => {
    reloadTail()
  }, [limit, level, keyword])

  useEffect(() => {
    if (!autoRefresh) return
    const timer = window.setInterval(fetchIncremental, POLL_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [autoRefresh, cursor, limit, level, keyword])

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">{title}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-col gap-2 lg:flex-row lg:items-center">
            <div className="flex flex-1 items-center gap-2">
              <div className="relative flex-1">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={keywordInput}
                  onChange={(e) => setKeywordInput(e.target.value)}
                  className="pl-9"
                  placeholder="按日志内容或 logger 过滤"
                />
              </div>
              <Button
                variant="outline"
                onClick={() => setKeyword(keywordInput.trim())}
              >
                搜索
              </Button>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <select
                className="h-10 rounded-md border border-input bg-background px-3 text-sm"
                value={level}
                onChange={(e) => setLevel(e.target.value)}
              >
                <option value="">全部级别</option>
                <option value="ERROR">ERROR</option>
                <option value="WARNING">WARNING</option>
                <option value="INFO">INFO</option>
                <option value="DEBUG">DEBUG</option>
              </select>
              <select
                className="h-10 rounded-md border border-input bg-background px-3 text-sm"
                value={String(limit)}
                onChange={(e) => setLimit(Number(e.target.value))}
              >
                <option value="50">50 行</option>
                <option value="100">100 行</option>
                <option value="200">200 行</option>
              </select>
              <Button variant="outline" onClick={() => setAutoRefresh(v => !v)}>
                {autoRefresh ? <Pause className="mr-1 h-4 w-4" /> : <Play className="mr-1 h-4 w-4" />}
                {autoRefresh ? '暂停' : '继续'}
              </Button>
              <Button variant="outline" onClick={reloadTail} disabled={loading}>
                <RefreshCw className={`mr-1 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
                刷新
              </Button>
              <Button
                variant="outline"
                onClick={() => {
                  setEntries([])
                  setCursor(0)
                  setBufferSize(0)
                }}
              >
                <Trash2 className="mr-1 h-4 w-4" />
                清空视图
              </Button>
            </div>
          </div>

          <div
            ref={listRef}
            className="h-[60vh] overflow-auto rounded-md border bg-muted/20 p-3 font-mono text-xs"
          >
            {entries.length === 0 ? (
              <div className="flex h-full items-center justify-center text-muted-foreground">
                暂无日志
              </div>
            ) : (
              <div className="space-y-1.5">
                {entries.map((entry) => (
                  <div key={entry.seq} className="rounded border border-border/40 bg-background px-2 py-1.5">
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                      <span className="text-muted-foreground">#{entry.seq}</span>
                      <span className={levelClass(entry.level)}>{entry.level}</span>
                      <span className="text-muted-foreground">{formatTimestamp(entry.timestamp)}</span>
                      <span className="text-slate-500 dark:text-slate-400">{entry.logger}</span>
                    </div>
                    <div className="mt-1 whitespace-pre-wrap break-all leading-5">
                      {entry.message}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="text-xs text-muted-foreground">
            页面只保留最近 {MAX_RENDERED_ENTRIES} 行，服务端只暴露最近一段运行时日志，不会一次性加载全量。
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
