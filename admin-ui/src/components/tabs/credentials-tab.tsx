import { useState, useEffect, useRef, useMemo } from 'react'
import { RefreshCw, Plus, Upload, FileUp, Trash2, RotateCcw, CheckCircle2, GripVertical, ListRestart } from 'lucide-react'
import { DndContext, closestCenter, DragOverlay, useDroppable, useDraggable, type DragEndEvent, type DragStartEvent, PointerSensor, useSensor, useSensors } from '@dnd-kit/core'
import { toast } from 'sonner'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { CredentialCard } from '@/components/credential-card'
import { BalanceDialog } from '@/components/balance-dialog'
import { AddCredentialDialog } from '@/components/add-credential-dialog'
import { BatchImportDialog } from '@/components/batch-import-dialog'
import { CredentialsEditorDialog } from '@/components/credentials-editor-dialog'
import { BatchVerifyDialog, type VerifyResult } from '@/components/batch-verify-dialog'
import { useCredentials, useDeleteCredential, useResetFailure } from '@/hooks/use-credentials'
import { getCredentialBalance, setCredentialGroups, resetAllCounters, setCredentialDisabled } from '@/api/credentials'
import { extractErrorMessage } from '@/lib/utils'
import type { BalanceResponse, CredentialStatusItem } from '@/types/api'

export function CredentialsTab() {
  const [selectedCredentialId, setSelectedCredentialId] = useState<number | null>(null)
  const [balanceDialogOpen, setBalanceDialogOpen] = useState(false)
  const [addDialogOpen, setAddDialogOpen] = useState(false)
  const [batchImportDialogOpen, setBatchImportDialogOpen] = useState(false)
  const [credentialsEditorOpen, setCredentialsEditorOpen] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [verifyDialogOpen, setVerifyDialogOpen] = useState(false)
  const [verifying, setVerifying] = useState(false)
  const [verifyProgress, setVerifyProgress] = useState({ current: 0, total: 0 })
  const [verifyResults, setVerifyResults] = useState<Map<number, VerifyResult>>(new Map())
  const [balanceMap, setBalanceMap] = useState<Map<number, BalanceResponse>>(new Map())
  const [loadingBalanceIds, setLoadingBalanceIds] = useState<Set<number>>(new Set())
  const [queryingInfo, setQueryingInfo] = useState(false)
  const [queryInfoProgress, setQueryInfoProgress] = useState({ current: 0, total: 0 })
  const cancelVerifyRef = useRef(false)

  const { data, refetch } = useCredentials()
  const { mutate: deleteCredential } = useDeleteCredential()
  const { mutate: resetFailure } = useResetFailure()

  const disabledCredentialCount = data?.credentials.filter(c => c.disabled).length || 0

  const selectedDisabledCount = Array.from(selectedIds).filter(id => {
    const credential = data?.credentials.find(c => c.id === id)
    return Boolean(credential?.disabled)
  }).length

  // 清理已删除凭据的缓存
  useEffect(() => {
    if (!data?.credentials) {
      setBalanceMap(new Map())
      setLoadingBalanceIds(new Set())
      return
    }
    const validIds = new Set(data.credentials.map(c => c.id))
    setBalanceMap(prev => {
      const next = new Map<number, BalanceResponse>()
      prev.forEach((v, id) => { if (validIds.has(id)) next.set(id, v) })
      return next.size === prev.size ? prev : next
    })
    setLoadingBalanceIds(prev => {
      if (prev.size === 0) return prev
      const next = new Set<number>()
      prev.forEach(id => { if (validIds.has(id)) next.add(id) })
      return next.size === prev.size ? prev : next
    })
  }, [data?.credentials])

  const handleViewBalance = (id: number) => {
    setSelectedCredentialId(id)
    setBalanceDialogOpen(true)
  }

  const toggleSelect = (id: number) => {
    const s = new Set(selectedIds)
    s.has(id) ? s.delete(id) : s.add(id)
    setSelectedIds(s)
  }
  const deselectAll = () => setSelectedIds(new Set())

  // 批量删除
  const handleBatchDelete = async () => {
    if (selectedIds.size === 0) return
    const disabledIds = Array.from(selectedIds).filter(id => data?.credentials.find(c => c.id === id)?.disabled)
    if (disabledIds.length === 0) { toast.error('选中的凭据中没有已禁用项'); return }
    const skip = selectedIds.size - disabledIds.length
    if (!confirm(`确定要删除 ${disabledIds.length} 个已禁用凭据吗？${skip > 0 ? `（跳过 ${skip} 个未禁用）` : ''}`)) return
    let ok = 0, fail = 0
    for (const id of disabledIds) {
      try { await new Promise<void>((res, rej) => deleteCredential(id, { onSuccess: () => { ok++; res() }, onError: (e) => { fail++; rej(e) } })) } catch {}
    }
    toast[fail === 0 ? 'success' : 'warning'](`删除：成功 ${ok}，失败 ${fail}`)
    deselectAll()
  }

  // 批量恢复
  const handleBatchReset = async () => {
    if (selectedIds.size === 0) return
    const ids = Array.from(selectedIds).filter(id => (data?.credentials.find(c => c.id === id)?.failureCount ?? 0) > 0)
    if (ids.length === 0) { toast.error('没有失败的凭据'); return }
    let ok = 0, fail = 0
    for (const id of ids) {
      try { await new Promise<void>((res, rej) => resetFailure(id, { onSuccess: () => { ok++; res() }, onError: (e) => { fail++; rej(e) } })) } catch {}
    }
    toast[fail === 0 ? 'success' : 'warning'](`恢复：成功 ${ok}，失败 ${fail}`)
    deselectAll()
  }

  // 清除所有已禁用
  const handleClearAll = async () => {
    const disabled = data?.credentials.filter(c => c.disabled) || []
    if (disabled.length === 0) { toast.error('没有已禁用凭据'); return }
    if (!confirm(`确定清除所有 ${disabled.length} 个已禁用凭据？`)) return
    let ok = 0, fail = 0
    for (const c of disabled) {
      try { await new Promise<void>((res, rej) => deleteCredential(c.id, { onSuccess: () => { ok++; res() }, onError: (e) => { fail++; rej(e) } })) } catch {}
    }
    toast[fail === 0 ? 'success' : 'warning'](`清除：成功 ${ok}，失败 ${fail}`)
    deselectAll()
  }
  // 查询所有凭据信息
  const handleQueryAllInfo = async () => {
    const ids = (data?.credentials || []).filter(c => !c.disabled).map(c => c.id)
    if (ids.length === 0) { toast.error('没有可查询的启用凭据'); return }
    setQueryingInfo(true)
    setQueryInfoProgress({ current: 0, total: ids.length })
    let ok = 0, fail = 0, autoDisabled = 0
    for (let i = 0; i < ids.length; i++) {
      const id = ids[i]
      setLoadingBalanceIds(prev => new Set(prev).add(id))
      try {
        const balance = await getCredentialBalance(id)
        ok++
        setBalanceMap(prev => new Map(prev).set(id, balance))
      } catch (err: unknown) {
        fail++
        // 仅凭据本身无效（4xx）时自动禁用，网络/服务端错误不禁用
        const status = (err as { response?: { status?: number } })?.response?.status
        if (status && status >= 400 && status < 500) {
          try { await setCredentialDisabled(id, true); autoDisabled++ }
          catch { /* 禁用失败忽略 */ }
        }
      }
      finally { setLoadingBalanceIds(prev => { const n = new Set(prev); n.delete(id); return n }) }
      setQueryInfoProgress({ current: i + 1, total: ids.length })
    }
    setQueryingInfo(false)
    refetch()
    const msg = `查询：成功 ${ok}/${ids.length}` + (autoDisabled > 0 ? `，已自动禁用 ${autoDisabled} 个` : '')
    toast[fail === 0 ? 'success' : 'warning'](msg)
  }

  // 批量验活
  const handleBatchVerify = async () => {
    if (selectedIds.size === 0) { toast.error('请先选择凭据'); return }
    setVerifying(true)
    cancelVerifyRef.current = false
    const ids = Array.from(selectedIds)
    setVerifyProgress({ current: 0, total: ids.length })
    const initial = new Map<number, VerifyResult>()
    ids.forEach(id => initial.set(id, { id, status: 'pending' }))
    setVerifyResults(initial)
    setVerifyDialogOpen(true)
    let ok = 0
    for (let i = 0; i < ids.length; i++) {
      if (cancelVerifyRef.current) { toast.info('已取消验活'); break }
      const id = ids[i]
      setVerifyResults(prev => new Map(prev).set(id, { id, status: 'verifying' }))
      try {
        const b = await getCredentialBalance(id)
        ok++
        setVerifyResults(prev => new Map(prev).set(id, { id, status: 'success', usage: `${b.currentUsage}/${b.usageLimit}` }))
      } catch (e) {
        setVerifyResults(prev => new Map(prev).set(id, { id, status: 'failed', error: extractErrorMessage(e) }))
      }
      setVerifyProgress({ current: i + 1, total: ids.length })
      if (i < ids.length - 1 && !cancelVerifyRef.current) await new Promise(r => setTimeout(r, 2000))
    }
    setVerifying(false)
    if (!cancelVerifyRef.current) toast.success(`验活完成：成功 ${ok}/${ids.length}`)
  }

  // 分组数据
  const groupedCredentials = useMemo(() => {
    const all = data?.credentials || []
    return {
      free: all.filter(c => c.group === 'free'),
      pro: all.filter(c => c.group === 'pro' || (!c.group && !c.subscriptionTitle?.toUpperCase().includes('FREE'))),
      priority: all.filter(c => c.group === 'priority'),
    }
  }, [data?.credentials])

  // 拖拽
  const [draggingId, setDraggingId] = useState<number | null>(null)
  const [draggingWidth, setDraggingWidth] = useState<number>(0)
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 8 } }))
  const draggingCredential = useMemo(() => {
    if (!draggingId || !data?.credentials) return null
    return data.credentials.find(c => c.id === draggingId) || null
  }, [draggingId, data?.credentials])

  const dragNodeRefs = useRef<Map<number, HTMLDivElement>>(new Map())

  const handleDragStart = (e: DragStartEvent) => {
    const id = e.active.id as number
    setDraggingId(id)
    const el = dragNodeRefs.current.get(id)
    if (el) setDraggingWidth(el.getBoundingClientRect().width)
  }
  const handleDragEnd = (e: DragEndEvent) => {
    setDraggingId(null)
    const { active, over } = e
    if (!over || !data?.credentials) return
    const credId = active.id as number
    const targetGroup = over.id as string
    const cred = data.credentials.find(c => c.id === credId)
    if (!cred) return
    const isFree = cred.subscriptionTitle?.toUpperCase().includes('FREE')
    if (isFree && targetGroup !== 'free') { toast.error('FREE 凭据只能在免费分组'); return }
    if (!isFree && targetGroup === 'free') { toast.error('非 FREE 凭据不能拖到免费分组'); return }
    if (cred.group === targetGroup) return
    setCredentialGroups({ [credId]: targetGroup })
      .then(() => { refetch(); toast.success(`凭据 #${credId} 已移至${targetGroup === 'priority' ? '高优先级' : 'Pro'}分组`) })
      .catch(() => toast.error('分组更新失败'))
  }
  return (
    <div className="space-y-4">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
        <div className="flex items-center gap-4">
          <h2 className="text-xl font-semibold">凭据管理</h2>
          {selectedIds.size > 0 && (
            <div className="flex items-center gap-2">
              <Badge variant="secondary">已选择 {selectedIds.size} 个</Badge>
              <Button onClick={deselectAll} size="sm" variant="ghost">取消选择</Button>
            </div>
          )}
        </div>
        <div className="flex gap-2 flex-wrap">
          {selectedIds.size > 0 && (
            <>
              <Button onClick={handleBatchVerify} size="sm" variant="outline">
                <CheckCircle2 className="h-4 w-4 mr-1" />验活
              </Button>
              <Button onClick={handleBatchReset} size="sm" variant="outline">
                <RotateCcw className="h-4 w-4 mr-1" />恢复异常
              </Button>
              <Button onClick={handleBatchDelete} size="sm" variant="destructive" disabled={selectedDisabledCount === 0}>
                <Trash2 className="h-4 w-4 mr-1" />批量删除
              </Button>
            </>
          )}
          {verifying && !verifyDialogOpen && (
            <Button onClick={() => setVerifyDialogOpen(true)} size="sm" variant="secondary">
              <CheckCircle2 className="h-4 w-4 mr-1 animate-spin" />验活中 {verifyProgress.current}/{verifyProgress.total}
            </Button>
          )}
          {(data?.credentials?.length ?? 0) > 0 && (
            <>
              <Button onClick={handleQueryAllInfo} size="sm" variant="outline" disabled={queryingInfo}>
                <RefreshCw className={`h-4 w-4 mr-1 ${queryingInfo ? 'animate-spin' : ''}`} />
                {queryingInfo ? `${queryInfoProgress.current}/${queryInfoProgress.total}` : '查询信息'}
              </Button>
              <Button onClick={async () => {
                try { const res = await resetAllCounters(); toast.success(res.message); await refetch() }
                catch { toast.error('重置失败') }
              }} size="sm" variant="outline">
                <ListRestart className="h-4 w-4 mr-1" />重置均衡
              </Button>
              <Button onClick={handleClearAll} size="sm" variant="outline" className="text-destructive hover:text-destructive" disabled={disabledCredentialCount === 0}>
                <Trash2 className="h-4 w-4 mr-1" />清除已禁用
              </Button>
            </>
          )}
          <Button onClick={() => setCredentialsEditorOpen(true)} size="sm" variant="outline">
            <FileUp className="h-4 w-4 mr-1" />编辑凭据文件
          </Button>
          <Button onClick={() => setBatchImportDialogOpen(true)} size="sm" variant="outline">
            <Upload className="h-4 w-4 mr-1" />批量导入
          </Button>
          <Button onClick={() => setAddDialogOpen(true)} size="sm">
            <Plus className="h-4 w-4 mr-1" />添加凭据
          </Button>
        </div>
      </div>

      {(data?.credentials?.length ?? 0) === 0 ? (
        <Card><CardContent className="py-8 text-center text-muted-foreground">暂无凭据</CardContent></Card>
      ) : (
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragStart={handleDragStart} onDragEnd={handleDragEnd}>
          <div className="space-y-6">
            <GroupDropZone id="priority" label="高优先级" color="orange" count={groupedCredentials.priority.length}>
              {groupedCredentials.priority.map(c => (
                <DraggableCredentialCard key={c.id} credential={c} onViewBalance={handleViewBalance}
                  selected={selectedIds.has(c.id)} onToggleSelect={() => toggleSelect(c.id)}
                  balance={balanceMap.get(c.id) || null} loadingBalance={loadingBalanceIds.has(c.id)}
                  nodeRefs={dragNodeRefs} />
              ))}
            </GroupDropZone>
            <GroupDropZone id="pro" label="Pro" color="blue" count={groupedCredentials.pro.length}>
              {groupedCredentials.pro.map(c => (
                <DraggableCredentialCard key={c.id} credential={c} onViewBalance={handleViewBalance}
                  selected={selectedIds.has(c.id)} onToggleSelect={() => toggleSelect(c.id)}
                  balance={balanceMap.get(c.id) || null} loadingBalance={loadingBalanceIds.has(c.id)}
                  nodeRefs={dragNodeRefs} />
              ))}
            </GroupDropZone>
            <GroupDropZone id="free" label="免费" color="gray" count={groupedCredentials.free.length}>
              {groupedCredentials.free.map(c => (
                <DraggableCredentialCard key={c.id} credential={c} onViewBalance={handleViewBalance}
                  selected={selectedIds.has(c.id)} onToggleSelect={() => toggleSelect(c.id)}
                  balance={balanceMap.get(c.id) || null} loadingBalance={loadingBalanceIds.has(c.id)} draggable={false}
                  nodeRefs={dragNodeRefs} />
              ))}
            </GroupDropZone>
          </div>
          <DragOverlay dropAnimation={null}>
            {draggingCredential ? (
              <div className="opacity-90" style={{ width: draggingWidth || undefined }}>
                <CredentialCard credential={draggingCredential} onViewBalance={() => {}}
                  selected={false} onToggleSelect={() => {}}
                  balance={balanceMap.get(draggingCredential.id) || null} loadingBalance={false} />
              </div>
            ) : null}
          </DragOverlay>
        </DndContext>
      )}

      <BalanceDialog credentialId={selectedCredentialId} open={balanceDialogOpen} onOpenChange={setBalanceDialogOpen} />
      <AddCredentialDialog open={addDialogOpen} onOpenChange={setAddDialogOpen} />
      <BatchImportDialog open={batchImportDialogOpen} onOpenChange={setBatchImportDialogOpen} />
      <CredentialsEditorDialog open={credentialsEditorOpen} onOpenChange={setCredentialsEditorOpen} />
      <BatchVerifyDialog open={verifyDialogOpen} onOpenChange={setVerifyDialogOpen}
        verifying={verifying} progress={verifyProgress} results={verifyResults} />
    </div>
  )
}
// 分组放置区域
function GroupDropZone({ id, label, color, count, children }: {
  id: string; label: string; color: 'orange' | 'blue' | 'gray'; count: number; children: React.ReactNode
}) {
  const { isOver, setNodeRef } = useDroppable({ id })
  const colorMap = {
    orange: 'border-orange-400 bg-orange-50/50 dark:bg-orange-950/20',
    blue: 'border-blue-400 bg-blue-50/50 dark:bg-blue-950/20',
    gray: 'border-gray-400 bg-gray-50/50 dark:bg-gray-950/20',
  }
  const badgeColorMap = {
    orange: 'bg-orange-100 text-orange-700 dark:bg-orange-900 dark:text-orange-300',
    blue: 'bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300',
    gray: 'bg-gray-100 text-gray-700 dark:bg-gray-900 dark:text-gray-300',
  }
  return (
    <div ref={setNodeRef} className={`rounded-lg border-2 border-dashed p-4 transition-colors ${colorMap[color]} ${isOver ? 'ring-2 ring-primary' : ''}`}>
      <div className="flex items-center gap-2 mb-3">
        <span className="font-semibold text-sm">{label}</span>
        <span className={`text-xs px-2 py-0.5 rounded-full ${badgeColorMap[color]}`}>{count}</span>
      </div>
      {count === 0 ? (
        <div className="text-center py-4 text-sm text-muted-foreground">拖拽凭据到此分组</div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">{children}</div>
      )}
    </div>
  )
}

// 可拖拽凭据卡片
function DraggableCredentialCard({ credential, draggable = true, nodeRefs, ...props }: {
  credential: CredentialStatusItem; onViewBalance: (id: number) => void
  selected: boolean; onToggleSelect: () => void
  balance: BalanceResponse | null; loadingBalance: boolean; draggable?: boolean
  nodeRefs: React.MutableRefObject<Map<number, HTMLDivElement>>
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id: credential.id, disabled: !draggable })
  const combinedRef = (el: HTMLDivElement | null) => {
    setNodeRef(el)
    if (el) nodeRefs.current.set(credential.id, el)
    else nodeRefs.current.delete(credential.id)
  }
  return (
    <div ref={combinedRef} className={`relative ${isDragging ? 'opacity-30' : ''}`}>
      {draggable && (
        <div {...listeners} {...attributes} className="absolute -top-1.5 left-1/2 -translate-x-1/2 z-10 cursor-grab active:cursor-grabbing px-2 py-0.5 rounded-b bg-muted/80 hover:bg-muted">
          <GripVertical className="h-3.5 w-3.5 text-muted-foreground rotate-90" />
        </div>
      )}
      <CredentialCard credential={credential} {...props} />
    </div>
  )
}
