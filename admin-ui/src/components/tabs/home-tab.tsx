import { useState, useEffect, useMemo } from 'react'
import { Activity, Zap, TrendingUp, Hash, Server, Cpu, HardDrive, ArrowUpRight, ArrowDownRight, Calendar } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { getRequestStats, getSystemStats, getTokenUsageHistory, getTokenUsageHourly, getKeyUsageStats, type KeyUsageStat } from '@/api/credentials'
import type { MemoryBreakdownItem, RequestStats, SystemStats } from '@/types/api'

// 凭据 ID 对应的颜色
const CRED_COLORS = [
  '#3b82f6', '#ef4444', '#22c55e', '#f59e0b', '#8b5cf6',
  '#ec4899', '#06b6d4', '#f97316', '#14b8a6', '#6366f1',
  '#84cc16', '#e11d48', '#0ea5e9', '#d946ef', '#a3e635',
]
function credColor(credId: number): string {
  return CRED_COLORS[credId % CRED_COLORS.length]
}

type TimeDimension = 'day' | 'week' | 'month'
const TIME_LABELS: Record<TimeDimension, string> = { day: '今日', week: '本周', month: '本月' }
const TIME_DAYS: Record<TimeDimension, number> = { day: 1, week: 7, month: 31 }

type UsageFilter = 'all' | 'group' | 'user'
const FILTER_LABELS: Record<UsageFilter, string> = { all: '全部', group: '按分组', user: '按用户' }

interface HistoryEntry {
  input: number
  output: number
  models: Record<string, { input: number; output: number }>
}

interface ChartSeries {
  name: string
  color: string
  values: number[]
}

interface HomeTabProps {
  credentialCount: number
  availableCount: number
}

export function HomeTab({ credentialCount, availableCount }: HomeTabProps) {
  const [stats, setStats] = useState<RequestStats | null>(null)
  const [sysStats, setSysStats] = useState<SystemStats | null>(null)
  const [timeDim, setTimeDim] = useState<TimeDimension>('day')
  const [usageFilter, setUsageFilter] = useState<UsageFilter>('all')
  const [history, setHistory] = useState<Record<string, HistoryEntry>>({})
  const [hourly, setHourly] = useState<Record<string, { input: number; output: number }>>({})
  const [keyStats, setKeyStats] = useState<KeyUsageStat[]>([])

  useEffect(() => {
    let cancelled = false
    let fetching = false

    const fetchAll = async () => {
      if (cancelled || document.hidden || fetching) return
      fetching = true
      try {
        const [nextStats, nextSysStats, nextKeyStats] = await Promise.all([
          getRequestStats().catch(() => null),
          getSystemStats().catch(() => null),
          getKeyUsageStats().catch(() => null),
        ])
        if (cancelled) return
        if (nextStats) setStats(nextStats)
        if (nextSysStats) setSysStats(nextSysStats)
        if (nextKeyStats) setKeyStats(nextKeyStats.keys)
      } finally {
        fetching = false
      }
    }

    const handleVisibilityChange = () => {
      if (!document.hidden) void fetchAll()
    }

    void fetchAll()
    const timer = window.setInterval(() => {
      void fetchAll()
    }, 15000)
    document.addEventListener('visibilitychange', handleVisibilityChange)

    return () => {
      cancelled = true
      clearInterval(timer)
      document.removeEventListener('visibilitychange', handleVisibilityChange)
    }
  }, [])

  // 切换维度时拉取数据
  useEffect(() => {
    if (timeDim === 'day') {
      getTokenUsageHourly().then(r => setHourly(r.hourly)).catch(() => {})
    } else {
      getTokenUsageHistory(TIME_DAYS[timeDim]).then(r => setHistory(r.history)).catch(() => {})
    }
  }, [timeDim])

  const modelEntries = stats
    ? Object.entries(stats.modelCounts).sort((a, b) => b[1] - a[1])
    : []

  const tokenUsage = stats?.tokenUsage

  // 聚合选定时间范围的 token 用量
  const aggregated = useMemo(() => {
    if (timeDim === 'day') {
      return {
        input: tokenUsage?.today.input ?? 0,
        output: tokenUsage?.today.output ?? 0,
        models: Object.fromEntries(
          Object.entries(tokenUsage?.models ?? {}).map(([m, v]) => [m, { input: v.today.input, output: v.today.output }])
        ),
      }
    }
    let totalIn = 0, totalOut = 0
    const modelAgg: Record<string, { input: number; output: number }> = {}
    for (const entry of Object.values(history)) {
      totalIn += entry.input
      totalOut += entry.output
      for (const [m, v] of Object.entries(entry.models ?? {})) {
        const cur = modelAgg[m] ?? { input: 0, output: 0 }
        cur.input += v.input
        cur.output += v.output
        modelAgg[m] = cur
      }
    }
    return { input: totalIn, output: totalOut, models: modelAgg }
  }, [timeDim, tokenUsage, history])

  // 折线图数据点
  const chartPoints = useMemo(() => {
    if (timeDim === 'day') {
      const points: { label: string; dateKey: string; input: number; output: number; isWeekStart?: boolean }[] = []
      for (let h = 0; h < 24; h++) {
        const key = `${h.toString().padStart(2, '0')}`
        const entry = hourly[key]
        points.push({ label: `${key}:00`, dateKey: key, input: entry?.input ?? 0, output: entry?.output ?? 0 })
      }
      return points
    }
    const days = TIME_DAYS[timeDim]
    const points: { label: string; dateKey: string; input: number; output: number; isWeekStart?: boolean }[] = []
    const now = new Date()
    for (let i = days - 1; i >= 0; i--) {
      const d = new Date(now)
      d.setDate(d.getDate() - i)
      const ds = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
      const entry = history[ds]
      const dayOfWeek = d.getDay()
      points.push({
        label: ds.slice(5),
        dateKey: ds,
        input: entry?.input ?? 0,
        output: entry?.output ?? 0,
        isWeekStart: timeDim === 'month' && dayOfWeek === 1,
      })
    }
    return points
  }, [timeDim, hourly, history])

  // 折线图系列数据（支持按分组/用户筛选）
  const chartSeries = useMemo((): ChartSeries[] => {
    if (usageFilter === 'all') {
      return [
        { name: '输入', color: '#3b82f6', values: chartPoints.map(p => p.input) },
        { name: '输出', color: '#f97316', values: chartPoints.map(p => p.output) },
      ]
    }
    const grouped: Record<string, KeyUsageStat[]> = {}
    for (const ks of keyStats) {
      const key = usageFilter === 'group' ? (ks.group || '未分组') : ks.name
      ;(grouped[key] ??= []).push(ks)
    }
    return Object.entries(grouped).map(([name, stats], i) => ({
      name,
      color: CRED_COLORS[i % CRED_COLORS.length],
      values: chartPoints.map(p => {
        if (timeDim === 'day') {
          return stats.reduce((sum, ks) => {
            const h = ks.hourlyUsage?.[p.dateKey]
            return sum + (h ? h.input + h.output : 0)
          }, 0)
        }
        return stats.reduce((sum, ks) => {
          const d = ks.dailyUsage?.[p.dateKey]
          return sum + (d ? d.input + d.output : 0)
        }, 0)
      }),
    }))
  }, [usageFilter, keyStats, chartPoints, timeDim])

  // 按维度聚合的模型统计
  const filteredModelStats = useMemo(() => {
    if (usageFilter === 'all') return null // 使用原有逻辑
    const grouped: Record<string, { modelCounts: Record<string, number>; modelTokens: Record<string, { input: number; output: number }>; requestCount: number }> = {}
    for (const ks of keyStats) {
      const key = usageFilter === 'group' ? (ks.group || '未分组') : ks.name
      if (!grouped[key]) grouped[key] = { modelCounts: {}, modelTokens: {}, requestCount: 0 }
      const g = grouped[key]
      g.requestCount += ks.requestCount
      for (const [m, c] of Object.entries(ks.modelCounts ?? {})) {
        g.modelCounts[m] = (g.modelCounts[m] ?? 0) + c
      }
      for (const [m, t] of Object.entries(ks.modelTokens ?? {})) {
        if (!g.modelTokens[m]) g.modelTokens[m] = { input: 0, output: 0 }
        g.modelTokens[m].input += t.input
        g.modelTokens[m].output += t.output
      }
    }
    return grouped
  }, [usageFilter, keyStats])

  return (
    <div className="space-y-6">
      <div className="grid gap-4 grid-cols-2 md:grid-cols-4">
        <StatCard icon={<Hash className="h-4 w-4" />} label="总调用次数" value={stats?.totalRequests ?? '-'} />
        <StatCard icon={<Zap className="h-4 w-4" />} label="本次会话调用" value={stats?.sessionRequests ?? '-'} />
        <StatCard icon={<Activity className="h-4 w-4" />} label="当前 RPM" value={stats?.rpm ?? '-'} color="text-blue-600" />
        <StatCard icon={<TrendingUp className="h-4 w-4" />} label="峰值 RPM" value={stats?.peakRpm ?? '-'} color="text-orange-600" />
      </div>

      {/* Token 用量 - 带时间维度切换 */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <Calendar className="h-4 w-4" /> Token 用量
          </CardTitle>
          <div className="flex gap-1">
            {(Object.keys(TIME_LABELS) as TimeDimension[]).map(dim => (
              <Button
                key={dim}
                variant={timeDim === dim ? 'default' : 'outline'}
                size="sm"
                className="h-7 px-2.5 text-xs"
                onClick={() => setTimeDim(dim)}
              >
                {TIME_LABELS[dim]}
              </Button>
            ))}
            <span className="w-px bg-border mx-1" />
            {(Object.keys(FILTER_LABELS) as UsageFilter[]).map(f => (
              <Button
                key={f}
                variant={usageFilter === f ? 'secondary' : 'ghost'}
                size="sm"
                className="h-7 px-2 text-xs"
                onClick={() => setUsageFilter(f)}
              >
                {FILTER_LABELS[f]}
              </Button>
            ))}
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid gap-4 grid-cols-2 md:grid-cols-4">
            <StatCard icon={<Server className="h-4 w-4" />} label="凭据总数" value={credentialCount} />
            <StatCard icon={<Server className="h-4 w-4" />} label="可用凭据" value={availableCount} color="text-green-600" />
            <div>
              <div className="text-sm text-muted-foreground mb-1">{TIME_LABELS[timeDim]}输入 Tokens</div>
              <div className="text-2xl font-bold">{formatTokenCount(aggregated.input)}</div>
              {timeDim === 'day' && (tokenUsage?.yesterday.input ?? 0) > 0 && (
                <ChangeIndicator current={aggregated.input} previous={tokenUsage?.yesterday.input ?? 0} />
              )}
            </div>
            <div>
              <div className="text-sm text-muted-foreground mb-1">{TIME_LABELS[timeDim]}输出 Tokens</div>
              <div className="text-2xl font-bold">{formatTokenCount(aggregated.output)}</div>
              {timeDim === 'day' && (tokenUsage?.yesterday.output ?? 0) > 0 && (
                <ChangeIndicator current={aggregated.output} previous={tokenUsage?.yesterday.output ?? 0} />
              )}
            </div>
          </div>

          {/* 折线图 */}
          {chartPoints.length > 0 && (
            <div className="mt-4">
              <div className="text-xs text-muted-foreground mb-2">
                {timeDim === 'day' ? '今日小时用量趋势' : timeDim === 'week' ? '本周每日用量趋势' : '本月每日用量趋势'}
                {usageFilter !== 'all' && ` (${FILTER_LABELS[usageFilter]})`}
              </div>
              <LineChart
                labels={chartPoints.map(p => p.label)}
                series={chartSeries}
                dimKey={timeDim}
                weekStarts={chartPoints.map(p => !!p.isWeekStart)}
              />
            </div>
          )}

          {/* 按模型的 token 汇总 */}
          {Object.keys(aggregated.models).length > 0 && (
            <div className="mt-4 space-y-1.5">
              <div className="text-xs text-muted-foreground">按模型</div>
              {Object.entries(aggregated.models)
                .sort((a, b) => (b[1].input + b[1].output) - (a[1].input + a[1].output))
                .map(([model, v]) => (
                  <div key={model} className="flex items-center gap-2 text-xs">
                    <span className="font-mono w-40 truncate" title={model}>{model}</span>
                    <span className="text-muted-foreground">输入</span>
                    <span className="font-medium w-16 text-right">{formatTokenCount(v.input)}</span>
                    <span className="text-muted-foreground">输出</span>
                    <span className="font-medium w-16 text-right">{formatTokenCount(v.output)}</span>
                  </div>
                ))}
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 grid-cols-2 md:grid-cols-2">
        <StatCard icon={<Cpu className="h-4 w-4" />} label="CPU 使用率" value={sysStats ? `${sysStats.cpuPercent}%` : '-'} />
        <MemoryStatCard
          icon={<HardDrive className="h-4 w-4" />}
          label="进程内存"
          memoryMb={sysStats?.memoryMb}
          breakdown={sysStats?.memoryBreakdown ?? []}
          tracedMemoryMb={sysStats?.tracedMemoryMb}
        />
      </div>

      {/* 模型调用统计 */}
      {(modelEntries.length > 0 || filteredModelStats) && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              模型调用统计{usageFilter === 'all' ? '（本次会话）' : ` (${FILTER_LABELS[usageFilter]})`}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {filteredModelStats ? (
              <div className="space-y-4">
                {Object.entries(filteredModelStats).map(([name, data], gi) => {
                  const entries = Object.entries(data.modelCounts).sort((a, b) => b[1] - a[1])
                  if (entries.length === 0) return null
                  const maxCount = entries[0][1]
                  return (
                    <div key={name}>
                      <div className="flex items-center gap-2 mb-2">
                        <span className="w-3 h-3 rounded-sm flex-shrink-0" style={{ backgroundColor: CRED_COLORS[gi % CRED_COLORS.length] }} />
                        <span className="text-sm font-medium">{name}</span>
                        <span className="text-xs text-muted-foreground">{data.requestCount} 次请求</span>
                      </div>
                      <div className="space-y-1.5 pl-5">
                        {entries.map(([model, count]) => {
                          const pct = maxCount > 0 ? (count / maxCount) * 100 : 0
                          const mt = data.modelTokens[model]
                          return (
                            <div key={model} className="flex items-center gap-2 text-xs">
                              <span className="font-mono w-32 truncate" title={model}>{model}</span>
                              <div className="flex-1 h-4 bg-muted rounded-full overflow-hidden">
                                <div className="h-full rounded-full" style={{ width: `${pct}%`, backgroundColor: CRED_COLORS[gi % CRED_COLORS.length], opacity: 0.7 }} />
                              </div>
                              <span className="w-8 text-right font-medium">{count}</span>
                              {mt && (
                                <span className="text-muted-foreground w-28 text-right font-mono hidden md:inline">
                                  {formatTokenCount(mt.input)} / {formatTokenCount(mt.output)}
                                </span>
                              )}
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  )
                })}
              </div>
            ) : (
              <div className="space-y-3">
                {modelEntries.map(([model, count]) => {
                  const maxCount = modelEntries[0][1]
                  const pct = maxCount > 0 ? (count / maxCount) * 100 : 0
                  const credBreakdown = stats?.modelCredCounts?.[model] || {}
                  const segments = Object.entries(credBreakdown)
                    .map(([cid, cnt]) => ({ credId: Number(cid), count: cnt }))
                    .sort((a, b) => b.count - a.count)
                  const modelTokens = tokenUsage?.models?.[model]

                  return (
                    <ModelBar
                      key={model} model={model} total={count} pct={pct} segments={segments}
                      inputTokens={modelTokens?.today.input ?? 0}
                      outputTokens={modelTokens?.today.output ?? 0}
                    />
                  )
                })}
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}

function LineChart({ labels, series, dimKey, weekStarts }: {
  labels: string[]
  series: ChartSeries[]
  dimKey: TimeDimension
  weekStarts?: boolean[]
}) {
  const [hoverIdx, setHoverIdx] = useState<number | null>(null)

  const W = 800, H = 180, PL = 50, PR = 16, PT = 16, PB = 32
  const cw = W - PL - PR, ch = H - PT - PB

  const maxVal = Math.max(...series.flatMap(s => s.values), 1)
  const n = labels.length

  const toX = (i: number) => PL + (n > 1 ? (i / (n - 1)) * cw : cw / 2)
  const toY = (v: number) => PT + ch - (v / maxVal) * ch

  const makePath = (values: number[]) =>
    values.map((v, i) => `${i === 0 ? 'M' : 'L'}${toX(i).toFixed(1)},${toY(v).toFixed(1)}`).join(' ')

  const makeArea = (values: number[]) => {
    const line = values.map((v, i) => `${toX(i).toFixed(1)},${toY(v).toFixed(1)}`)
    return `M${line[0]} L${line.join(' L')} L${toX(n - 1).toFixed(1)},${(PT + ch).toFixed(1)} L${PL.toFixed(1)},${(PT + ch).toFixed(1)} Z`
  }

  const yTicks = [0, 0.25, 0.5, 0.75, 1].map(r => ({
    y: PT + ch - r * ch,
    label: formatTokenCount(Math.round(r * maxVal)),
  }))

  const xLabels: { i: number; label: string; bold?: boolean }[] = []
  if (dimKey === 'day') {
    for (let i = 0; i < n; i += 3) xLabels.push({ i, label: labels[i].slice(0, 2) })
  } else if (dimKey === 'week') {
    labels.forEach((l, i) => xLabels.push({ i, label: l }))
  } else {
    labels.forEach((l, i) => {
      const ws = weekStarts?.[i]
      if (ws || i === 0 || i === n - 1) {
        xLabels.push({ i, label: l, bold: !!ws })
      }
    })
  }

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full h-auto"
        onMouseLeave={() => setHoverIdx(null)}
      >
        {yTicks.map((t, i) => (
          <g key={i}>
            <line x1={PL} y1={t.y} x2={W - PR} y2={t.y} stroke="currentColor" strokeOpacity={0.08} />
            <text x={PL - 6} y={t.y + 3} textAnchor="end" className="fill-muted-foreground" fontSize={9}>{t.label}</text>
          </g>
        ))}

        {dimKey === 'month' && weekStarts?.map((ws, i) => ws ? (
          <line key={i} x1={toX(i)} y1={PT} x2={toX(i)} y2={PT + ch} stroke="currentColor" strokeOpacity={0.1} strokeDasharray="3,3" />
        ) : null)}

        {series.map((s, si) => (
          <g key={si}>
            <path d={makeArea(s.values)} fill={s.color} fillOpacity={0.06} />
            <path d={makePath(s.values)} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" />
          </g>
        ))}

        {xLabels.map(({ i, label, bold }) => (
          <text key={i} x={toX(i)} y={H - 4} textAnchor="middle" className="fill-muted-foreground" fontSize={9} fontWeight={bold ? 600 : 400}>{label}</text>
        ))}

        {labels.map((_, i) => (
          <rect
            key={i}
            x={toX(i) - (cw / n / 2)}
            y={PT}
            width={cw / n}
            height={ch}
            fill="transparent"
            onMouseEnter={() => setHoverIdx(i)}
          />
        ))}

        {hoverIdx !== null && (
          <g>
            <line x1={toX(hoverIdx)} y1={PT} x2={toX(hoverIdx)} y2={PT + ch} stroke="currentColor" strokeOpacity={0.2} strokeDasharray="3,3" />
            {series.map((s, si) => (
              <circle key={si} cx={toX(hoverIdx)} cy={toY(s.values[hoverIdx])} r={3.5} fill={s.color} stroke="white" strokeWidth={1.5} />
            ))}
          </g>
        )}
      </svg>

      {/* 图例 */}
      <div className="flex items-center gap-4 mt-1 ml-12 text-[10px] text-muted-foreground flex-wrap">
        {series.map((s, i) => (
          <span key={i} className="flex items-center gap-1">
            <span className="w-3 h-[2px] inline-block" style={{ backgroundColor: s.color }} /> {s.name}
          </span>
        ))}
      </div>

      {/* 悬浮 tooltip */}
      {hoverIdx !== null && (
        <div
          className="absolute z-50 bg-popover border rounded px-2.5 py-1.5 text-xs shadow pointer-events-none"
          style={{
            left: `${(toX(hoverIdx) / W) * 100}%`,
            top: 0,
            transform: `translateX(${hoverIdx > n * 0.7 ? '-100%' : '0'})`,
          }}
        >
          <div className="font-medium mb-0.5">{labels[hoverIdx]}</div>
          {series.map((s, i) => (
            <div key={i} style={{ color: s.color }}>{s.name}: {formatTokenCount(s.values[hoverIdx])}</div>
          ))}
        </div>
      )}
    </div>
  )
}

function ChangeIndicator({ current, previous }: { current: number; previous: number }) {
  const pctChange = previous > 0 ? ((current - previous) / previous) * 100 : 0
  return (
    <div className={`flex items-center gap-1 text-xs mt-1 ${pctChange >= 0 ? 'text-orange-500' : 'text-green-500'}`}>
      {pctChange >= 0 ? <ArrowUpRight className="h-3 w-3" /> : <ArrowDownRight className="h-3 w-3" />}
      <span>{Math.abs(pctChange).toFixed(0)}% vs 昨日</span>
    </div>
  )
}

function MemoryStatCard({ icon, label, memoryMb, breakdown, tracedMemoryMb }: {
  icon: React.ReactNode
  label: string
  memoryMb?: number
  breakdown: MemoryBreakdownItem[]
  tracedMemoryMb?: number
}) {
  const [dialogOpen, setDialogOpen] = useState(false)
  const hasBreakdown = breakdown.length > 0

  return (
    <>
      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-2">
          <CardTitle className="text-sm font-medium text-muted-foreground">{label}</CardTitle>
          {icon}
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">{typeof memoryMb === 'number' ? `${memoryMb} MB` : '-'}</div>
          <Button className="mt-3 h-7 px-2.5 text-xs" variant="outline" onClick={() => setDialogOpen(true)}>
            查看详细占用
          </Button>
        </CardContent>
      </Card>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-xl max-h-[80vh] flex flex-col">
          <DialogHeader>
            <DialogTitle>进程内存详情</DialogTitle>
            <DialogDescription>
              当前进程 RSS：{typeof memoryMb === 'number' ? `${memoryMb} MB` : 'N/A'}；Python tracemalloc 可追踪内存：
              {typeof tracedMemoryMb === 'number' ? ` ${tracedMemoryMb.toFixed(2)} MB` : ' N/A'}
            </DialogDescription>
          </DialogHeader>

          {hasBreakdown ? (
            <div className="space-y-2 overflow-y-auto pr-1">
              {breakdown.map((item) => (
                <div key={`${item.module}-${item.path}`} className="rounded-md border p-2.5">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs truncate" title={item.path}>{item.module}</span>
                    <span className="ml-auto text-sm font-semibold">{item.memoryMb.toFixed(2)} MB</span>
                  </div>
                  <div className="text-[11px] text-muted-foreground mt-1">
                    占比 {item.sharePercent.toFixed(1)}% · {item.path}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-sm text-muted-foreground">暂无可用明细，请稍后重试。</div>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}

function ModelBar({ model, total, pct, segments, inputTokens, outputTokens }: {
  model: string; total: number; pct: number
  segments: { credId: number; count: number }[]
  inputTokens: number; outputTokens: number
}) {
  const [hovered, setHovered] = useState(false)

  return (
    <div className="flex items-center gap-2 md:gap-3">
      <span className="text-sm font-mono w-20 md:w-48 truncate" title={model}>{model}</span>
      <div
        className="flex-1 h-6 bg-muted rounded-full overflow-hidden relative cursor-pointer"
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      >
        {hovered && segments.length > 1 ? (
          // 悬浮时显示多段
          <div className="flex h-full" style={{ width: `${pct}%` }}>
            {segments.map(seg => {
              const segPct = total > 0 ? (seg.count / total) * 100 : 0
              return (
                <div
                  key={seg.credId}
                  className="h-full relative group"
                  style={{ width: `${segPct}%`, backgroundColor: credColor(seg.credId) }}
                  title={`#${seg.credId}: ${seg.count} 次`}
                >
                  {segPct > 15 && (
                    <span className="absolute inset-0 flex items-center justify-center text-[10px] text-white font-medium">
                      #{seg.credId}
                    </span>
                  )}
                </div>
              )
            })}
          </div>
        ) : (
          // 默认单色
          <div className="h-full bg-primary/70 rounded-full transition-all" style={{ width: `${pct}%` }} />
        )}

        {/* 悬浮提示 */}
        {hovered && segments.length > 0 && (
          <div className="absolute left-0 top-full mt-1 z-50 bg-popover border rounded-md shadow-md p-2 text-xs min-w-48">
            {segments.map(seg => (
              <div key={seg.credId} className="flex items-center gap-2 py-0.5">
                <span className="w-3 h-3 rounded-sm flex-shrink-0" style={{ backgroundColor: credColor(seg.credId) }} />
                <span className="font-mono">#{seg.credId}</span>
                <span className="ml-auto font-medium">{seg.count} 次</span>
              </div>
            ))}
          </div>
        )}
      </div>
      <span className="text-sm font-medium w-8 md:w-12 text-right">{total}</span>
      <span className="text-xs text-muted-foreground w-36 text-right font-mono hidden md:inline" title={`输入: ${inputTokens.toLocaleString()} / 输出: ${outputTokens.toLocaleString()}`}>
        {formatTokenCount(inputTokens)} / {formatTokenCount(outputTokens)}
      </span>
    </div>
  )
}

function StatCard({ icon, label, value, color }: {
  icon: React.ReactNode; label: string; value: string | number; color?: string
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{label}</CardTitle>
        {icon}
      </CardHeader>
      <CardContent>
        <div className={`text-2xl font-bold ${color || ''}`}>{value}</div>
      </CardContent>
    </Card>
  )
}

function formatTokenCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}
