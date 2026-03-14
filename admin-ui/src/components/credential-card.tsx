import { useState } from 'react'
import { toast } from 'sonner'
import { RefreshCw, Wallet, Trash2, Loader2 } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Switch } from '@/components/ui/switch'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import type { CredentialStatusItem, BalanceResponse } from '@/types/api'
import {
  useSetDisabled,
  useResetFailure,
  useDeleteCredential,
} from '@/hooks/use-credentials'

interface CredentialCardProps {
  credential: CredentialStatusItem
  onViewBalance: (id: number) => void
  selected: boolean
  onToggleSelect: () => void
  balance: BalanceResponse | null
  loadingBalance: boolean
}

function formatLastUsed(lastUsedAt: string | null): string {
  if (!lastUsedAt) return '从未使用'
  const date = new Date(lastUsedAt)
  const now = new Date()
  const diff = now.getTime() - date.getTime()
  if (diff < 0) return '刚刚'
  const seconds = Math.floor(diff / 1000)
  if (seconds < 60) return `${seconds}秒前`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}小时前`
  return `${Math.floor(hours / 24)}天前`
}
function scoreColor(score: number): string {
  if (score < -30) return 'text-green-500'
  if (score < 30) return 'text-yellow-500'
  return 'text-red-500'
}

export function CredentialCard({
  credential, onViewBalance, selected, onToggleSelect, balance, loadingBalance,
}: CredentialCardProps) {
  const [showDeleteDialog, setShowDeleteDialog] = useState(false)
  const setDisabled = useSetDisabled()
  const resetFailure = useResetFailure()
  const deleteCredential = useDeleteCredential()

  const handleToggleDisabled = () => {
    setDisabled.mutate(
      { id: credential.id, disabled: !credential.disabled },
      { onSuccess: (res) => toast.success(res.message), onError: (err) => toast.error('操作失败: ' + (err as Error).message) }
    )
  }
  const handleReset = () => {
    resetFailure.mutate(credential.id, {
      onSuccess: (res) => toast.success(res.message),
      onError: (err) => toast.error('操作失败: ' + (err as Error).message),
    })
  }
  const handleDelete = () => {
    if (!credential.disabled) { toast.error('请先禁用凭据再删除'); setShowDeleteDialog(false); return }
    deleteCredential.mutate(credential.id, {
      onSuccess: (res) => { toast.success(res.message); setShowDeleteDialog(false) },
      onError: (err) => toast.error('删除失败: ' + (err as Error).message),
    })
  }

  const hasScore = credential.balanceScore !== null && credential.balanceScore !== undefined
  const score = credential.balanceScore ?? 0
  const barPercent = hasScore ? Math.min(100, Math.max(0, (score + 100) / 2)) : 0
  const decay = credential.balanceDecay ?? 0
  const credRpm = credential.balanceRpm ?? 0
  const effectiveBalance = balance ?? credential.cachedBalance ?? null

  return (
    <>
      <Card className={`${credential.isCurrent ? 'ring-2 ring-primary' : ''} ${credential.disabled ? 'opacity-60' : ''}`}>
        <CardContent className="p-4 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 min-w-0">
              <Checkbox checked={selected} onCheckedChange={onToggleSelect} />
              <span className="font-medium truncate">{credential.email || `#${credential.id}`}</span>
              {credential.isCurrent && <Badge variant="success">当前</Badge>}
              {credential.disabled && (
                <Badge variant="destructive">
                  {credential.disabledReason === 'too_many_failures' || credential.disabledReason === 'quota_exceeded' ? '封禁' : '禁用'}
                </Badge>
              )}
            </div>
            <Switch checked={!credential.disabled} onCheckedChange={handleToggleDisabled} disabled={setDisabled.isPending} />
          </div>

          {/* 均衡评分条 + hover 面板 */}
          {hasScore && (
            <div className="group/balance relative flex items-center gap-2 text-xs cursor-help">
              <span className="text-muted-foreground shrink-0">均衡</span>
              <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
                <div
                  className="h-full rounded-full transition-all bg-gradient-to-r from-green-500 via-yellow-500 to-red-500"
                  style={{ width: `${barPercent}%` }}
                />
              </div>
              <span className={`font-mono shrink-0 ${scoreColor(score)}`}>{score}</span>
              {/* hover 详情面板 */}
              <div className="absolute left-0 top-full mt-1 z-50 hidden group-hover/balance:flex items-center gap-3 px-2.5 py-1.5 rounded-md bg-popover border shadow-md text-xs whitespace-nowrap">
                <span className="text-green-500 font-mono">RPM: +{credRpm}</span>
                <span className="text-red-500 font-mono">时间减益: -{decay}</span>
                <span className="text-muted-foreground">= {score}</span>
              </div>
            </div>
          )}

          <div className="grid grid-cols-3 gap-x-4 gap-y-1 text-xs">
            <div>
              <span className="text-muted-foreground">订阅 </span>
              <span className="font-medium">
                {loadingBalance ? <Loader2 className="inline w-3 h-3 animate-spin" />
                  : credential.subscriptionTitle || effectiveBalance?.subscriptionTitle || '未知'}
              </span>
            </div>
            <div>
              <span className="text-muted-foreground">成功 </span>
              <span className="font-medium">{credential.successCount}</span>
            </div>
            <div>
              <span className="text-muted-foreground">会话 </span>
              <span className="font-medium">{credential.sessionCount}</span>
            </div>
            <div>
              <span className="text-muted-foreground">失败 </span>
              <span className={credential.failureCount > 0 ? 'text-red-500 font-medium' : ''}>
                {credential.failureCount}
              </span>
            </div>
            <div className="col-span-2">
              <span className="text-muted-foreground">最后 </span>
              <span className="font-medium">{formatLastUsed(credential.lastUsedAt)}</span>
            </div>
          </div>

          {effectiveBalance && (() => {
            const remaining = 100 - effectiveBalance.usagePercentage
            const barColor = remaining > 60 ? 'bg-green-500' : remaining > 30 ? 'bg-yellow-500' : 'bg-red-500'
            return (
              <div className="text-xs">
                <div className="flex justify-between mb-0.5">
                  <span className="text-muted-foreground">剩余</span>
                  <span>{effectiveBalance.remaining.toFixed(1)} / {effectiveBalance.usageLimit.toFixed(1)} ({remaining.toFixed(0)}%)</span>
                </div>
                <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                  <div className={`h-full rounded-full transition-all ${barColor}`} style={{ width: `${remaining}%` }} />
                </div>
                {credential.balanceUpdatedAt && (
                  <div className="text-[11px] text-muted-foreground mt-1">
                    更新于 {formatLastUsed(credential.balanceUpdatedAt)}
                  </div>
                )}
              </div>
            )
          })()}

          {credential.hasProxy && (
            <div className="text-xs">
              <span className="text-muted-foreground">代理 </span>
              <span className="font-medium">{credential.proxyUrl}</span>
            </div>
          )}

          <div className="flex gap-1.5 pt-1 border-t">
            <Button size="sm" variant="outline" className="h-7 text-xs px-2"
              onClick={handleReset} disabled={resetFailure.isPending || credential.failureCount === 0}>
              <RefreshCw className="h-3 w-3 mr-1" />重置
            </Button>
            <Button size="sm" variant="default" className="h-7 text-xs px-2"
              onClick={() => onViewBalance(credential.id)}>
              <Wallet className="h-3 w-3 mr-1" />余额
            </Button>
            <Button size="sm" variant="destructive" className="h-7 text-xs px-2 ml-auto"
              onClick={() => setShowDeleteDialog(true)} disabled={!credential.disabled}
              title={!credential.disabled ? '需要先禁用才能删除' : undefined}>
              <Trash2 className="h-3 w-3 mr-1" />删除
            </Button>
          </div>
        </CardContent>
      </Card>

      <Dialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认删除凭据</DialogTitle>
            <DialogDescription>确定要删除凭据 #{credential.id} 吗？此操作无法撤销。</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowDeleteDialog(false)} disabled={deleteCredential.isPending}>取消</Button>
            <Button variant="destructive" onClick={handleDelete} disabled={deleteCredential.isPending || !credential.disabled}>确认删除</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
