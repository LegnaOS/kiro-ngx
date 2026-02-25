import { useState, useEffect } from 'react'
import { Activity, Zap, TrendingUp, Hash, Server, Cpu, HardDrive, ArrowUpRight, ArrowDownRight } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { getRequestStats, getSystemStats } from '@/api/credentials'
import type { RequestStats } from '@/types/api'

// 凭据 ID 对应的颜色
const CRED_COLORS = [
  '#3b82f6', '#ef4444', '#22c55e', '#f59e0b', '#8b5cf6',
  '#ec4899', '#06b6d4', '#f97316', '#14b8a6', '#6366f1',
  '#84cc16', '#e11d48', '#0ea5e9', '#d946ef', '#a3e635',
]
function credColor(credId: number): string {
  return CRED_COLORS[credId % CRED_COLORS.length]
}

interface HomeTabProps {
  credentialCount: number
  availableCount: number
}

export function HomeTab({ credentialCount, availableCount }: HomeTabProps) {
  const [stats, setStats] = useState<RequestStats | null>(null)
  const [sysStats, setSysStats] = useState<{ cpuPercent: number; memoryMb: number } | null>(null)

  useEffect(() => {
    const fetchAll = () => {
      getRequestStats().then(setStats).catch(() => {})
      getSystemStats().then(setSysStats).catch(() => {})
    }
    fetchAll()
    const timer = setInterval(fetchAll, 5000)
    return () => clearInterval(timer)
  }, [])

  const modelEntries = stats
    ? Object.entries(stats.modelCounts).sort((a, b) => b[1] - a[1])
    : []

  const tokenUsage = stats?.tokenUsage

  return (
    <div className="space-y-6">
      <div className="grid gap-4 grid-cols-2 md:grid-cols-4">
        <StatCard icon={<Hash className="h-4 w-4" />} label="总调用次数" value={stats?.totalRequests ?? '-'} />
        <StatCard icon={<Zap className="h-4 w-4" />} label="本次会话调用" value={stats?.sessionRequests ?? '-'} />
        <StatCard icon={<Activity className="h-4 w-4" />} label="当前 RPM" value={stats?.rpm ?? '-'} color="text-blue-600" />
        <StatCard icon={<TrendingUp className="h-4 w-4" />} label="峰值 RPM" value={stats?.peakRpm ?? '-'} color="text-orange-600" />
      </div>
      <div className="grid gap-4 grid-cols-2 md:grid-cols-4">
        <StatCard icon={<Server className="h-4 w-4" />} label="凭据总数" value={credentialCount} />
        <StatCard icon={<Server className="h-4 w-4" />} label="可用凭据" value={availableCount} color="text-green-600" />
        <TokenStatCard
          label="今日输入 Tokens"
          value={tokenUsage?.today.input ?? 0}
          yesterday={tokenUsage?.yesterday.input ?? 0}
        />
        <TokenStatCard
          label="今日输出 Tokens"
          value={tokenUsage?.today.output ?? 0}
          yesterday={tokenUsage?.yesterday.output ?? 0}
        />
      </div>
      <div className="grid gap-4 grid-cols-2 md:grid-cols-2">
        <StatCard icon={<Cpu className="h-4 w-4" />} label="CPU 使用率" value={sysStats ? `${sysStats.cpuPercent}%` : '-'} />
        <StatCard icon={<HardDrive className="h-4 w-4" />} label="进程内存" value={sysStats ? `${sysStats.memoryMb} MB` : '-'} />
      </div>

      {/* 模型调用统计 - 多段柱状图 */}
      {modelEntries.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">模型调用统计（本次会话）</CardTitle>
          </CardHeader>
          <CardContent>
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
          </CardContent>
        </Card>
      )}
    </div>
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

function TokenStatCard({ label, value, yesterday }: {
  label: string; value: number; yesterday: number
}) {
  const pctChange = yesterday > 0 ? ((value - yesterday) / yesterday) * 100 : 0
  const showChange = yesterday > 0

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{label}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{formatTokenCount(value)}</div>
        {showChange && (
          <div className={`flex items-center gap-1 text-xs mt-1 ${pctChange >= 0 ? 'text-orange-500' : 'text-green-500'}`}>
            {pctChange >= 0
              ? <ArrowUpRight className="h-3 w-3" />
              : <ArrowDownRight className="h-3 w-3" />}
            <span>{Math.abs(pctChange).toFixed(0)}% vs 昨日</span>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
