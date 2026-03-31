import { useState, useEffect } from 'react'
import { KeyRound, Plus, RefreshCw, Trash2, RotateCcw, Copy, Check, Eye, EyeOff, Shield } from 'lucide-react'
import { toast } from 'sonner'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Switch } from '@/components/ui/switch'
import {
  Dialog, DialogContent, DialogHeader, DialogFooter,
  DialogTitle, DialogDescription,
} from '@/components/ui/dialog'
import {
  getApiKeys, addApiKey, updateApiKey, deleteApiKey,
  regenerateApiKey, resetApiKeyUsage, setApiKeyGroup, deleteApiKeyGroup,
  type ApiKeyEntry, type ApiKeyGroup,
} from '@/api/credentials'

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(Math.round(n))
}

export function KeysTab() {
  const [keys, setKeys] = useState<ApiKeyEntry[]>([])
  const [groups, setGroups] = useState<Record<string, ApiKeyGroup>>({})
  const [showAdd, setShowAdd] = useState(false)
  const [showGroupEdit, setShowGroupEdit] = useState(false)
  const [copiedKey, setCopiedKey] = useState<string | null>(null)
  const [revealedKey, setRevealedKey] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)

  // 新建 key 表单
  const [newName, setNewName] = useState('')
  const [newGroup, setNewGroup] = useState('')
  const [newRate, setNewRate] = useState('')
  const [newQuota, setNewQuota] = useState('')

  // 分组编辑
  const [groupName, setGroupName] = useState('')
  const [groupRate, setGroupRate] = useState('')
  const [groupQuota, setGroupQuota] = useState('')

  const reload = () => {
    getApiKeys().then(r => { setKeys(r.keys); setGroups(r.groups) }).catch(() => toast.error('加载 Key 列表失败'))
  }

  useEffect(() => {
    reload()
    const timer = setInterval(reload, 5000)
    return () => clearInterval(timer)
  }, [])

  const handleAdd = async () => {
    if (!newName.trim()) { toast.error('请输入名称'); return }
    try {
      const r = await addApiKey({
        name: newName.trim(),
        group: newGroup.trim() || undefined as unknown as string,
        rate: newRate ? parseFloat(newRate) : null,
        monthlyQuota: newQuota ? parseInt(newQuota) : null,
      })
      toast.success(`已创建 Key: ${r.key.maskedKey}`)
      setShowAdd(false)
      setNewName(''); setNewGroup(''); setNewRate(''); setNewQuota('')
      reload()
    } catch { toast.error('创建失败') }
  }

  const handleToggle = async (entry: ApiKeyEntry) => {
    try {
      await updateApiKey(entry.key, { enabled: !entry.enabled })
      reload()
    } catch { toast.error('操作失败') }
  }

  const handleRegenerate = async (key: string) => {
    try {
      const r = await regenerateApiKey(key)
      toast.success(`Key 已重新生成: ${r.key.maskedKey}`)
      setRevealedKey(r.key.key)
      reload()
    } catch { toast.error('重新生成失败') }
  }

  const handleResetUsage = async (key: string) => {
    try {
      await resetApiKeyUsage(key)
      toast.success('用量已重置')
      reload()
    } catch { toast.error('重置失败') }
  }

  const handleDelete = async (key: string) => {
    try {
      await deleteApiKey(key)
      toast.success('已删除')
      setConfirmDelete(null)
      reload()
    } catch { toast.error('删除失败') }
  }

  const handleSaveGroup = async () => {
    if (!groupName.trim()) { toast.error('请输入分组名'); return }
    try {
      await setApiKeyGroup(groupName.trim(), {
        rate: parseFloat(groupRate) || 1.0,
        monthlyQuota: groupQuota ? parseInt(groupQuota) : -1,
      })
      toast.success(`分组 "${groupName}" 已保存`)
      setShowGroupEdit(false)
      setGroupName(''); setGroupRate(''); setGroupQuota('')
      reload()
    } catch { toast.error('保存失败') }
  }

  const handleDeleteGroup = async (name: string) => {
    try {
      await deleteApiKeyGroup(name)
      toast.success(`分组 "${name}" 已删除`)
      reload()
    } catch { toast.error('删除失败，可能仍有 Key 在使用此分组') }
  }

  const copyKey = (key: string) => {
    navigator.clipboard.writeText(key).then(() => {
      setCopiedKey(key)
      setTimeout(() => setCopiedKey(null), 1500)
    })
  }

  return (
    <div className="space-y-6">
      {/* 分组管理 */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <Shield className="h-4 w-4" /> 分组
          </CardTitle>
          <div className="flex gap-1">
            <Button variant="outline" size="sm" className="h-7 gap-1" onClick={() => setShowGroupEdit(true)}>
              <Plus className="h-3.5 w-3.5" /> 新建分组
            </Button>
            <Button variant="ghost" size="sm" onClick={reload}><RefreshCw className="h-4 w-4" /></Button>
          </div>
        </CardHeader>
        <CardContent>
          {Object.keys(groups).length === 0 ? (
            <div className="text-sm text-muted-foreground py-2 text-center">暂无分组，创建分组可统一管理倍率和额度</div>
          ) : (
            <div className="space-y-2">
              {Object.entries(groups).map(([name, g]) => (
                <div key={name} className="flex items-center gap-3 p-2 rounded-md border text-sm">
                  <Badge variant="outline" className="font-mono">{name}</Badge>
                  <span className="text-muted-foreground">倍率</span>
                  <span className="font-medium">{g.rate}x</span>
                  <span className="text-muted-foreground">月额度</span>
                  <span className="font-medium">{g.monthlyQuota < 0 ? '无限' : formatTokens(g.monthlyQuota)}</span>
                  <Button variant="ghost" size="sm" className="h-6 w-6 p-0 ml-auto" onClick={() => handleDeleteGroup(name)}>
                    <Trash2 className="h-3.5 w-3.5 text-red-500" />
                  </Button>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Key 列表 */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <KeyRound className="h-4 w-4" /> API Keys
          </CardTitle>
          <div className="flex gap-1">
            <Button size="sm" className="h-7 gap-1" onClick={() => setShowAdd(true)}>
              <Plus className="h-3.5 w-3.5" /> 新建 Key
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {keys.length === 0 ? (
            <div className="text-sm text-muted-foreground py-4 text-center">暂无 API Key</div>
          ) : (
            <div className="space-y-2">
              {keys.map(entry => {
                const quotaUsed = entry.effectiveQuota > 0 ? (entry.billedTokens / entry.effectiveQuota) * 100 : 0
                const isRevealed = revealedKey === entry.key
                const isCopied = copiedKey === entry.key
                const isAdmin = !!(entry as any).isAdmin

                return (
                  <div key={entry.key} className={`p-3 rounded-md border text-sm space-y-2 ${!entry.enabled ? 'opacity-50' : ''} ${isAdmin ? 'border-amber-400/50 bg-amber-50/30 dark:bg-amber-950/10' : ''}`}>
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{entry.name}</span>
                      {isAdmin && <Badge className="text-[10px] bg-amber-500 hover:bg-amber-500 text-white">管理员</Badge>}
                      {entry.group && !isAdmin && <Badge variant="outline" className="text-[10px]">{entry.group}</Badge>}
                      {!isAdmin && <span className="text-xs text-muted-foreground ml-auto">{entry.effectiveRate}x</span>}
                      {isAdmin && <span className="text-xs text-muted-foreground ml-auto">无限制</span>}
                      {!isAdmin && <Switch checked={entry.enabled} onCheckedChange={() => handleToggle(entry)} />}
                    </div>

                    {/* Key 显示 */}
                    <div className="flex items-center gap-1.5">
                      <code className="text-xs text-muted-foreground font-mono flex-1 truncate">
                        {isRevealed ? entry.key : entry.maskedKey}
                      </code>
                      <button className="text-muted-foreground hover:text-foreground" onClick={() => setRevealedKey(isRevealed ? null : entry.key)}>
                        {isRevealed ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                      </button>
                      <button className="text-muted-foreground hover:text-foreground" onClick={() => copyKey(entry.key)}>
                        {isCopied ? <Check className="h-3.5 w-3.5 text-green-500" /> : <Copy className="h-3.5 w-3.5" />}
                      </button>
                    </div>

                    {/* 额度进度（管理员不显示） */}
                    {!isAdmin && (
                      <div className="space-y-1">
                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                          <span>已用 {formatTokens(entry.billedTokens)}</span>
                          <span>/</span>
                          <span>{entry.effectiveQuota < 0 ? '无限' : formatTokens(entry.effectiveQuota)}</span>
                          <span className="ml-auto">{entry.requestCount} 次请求</span>
                        </div>
                        {entry.effectiveQuota > 0 && (
                          <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                            <div
                              className={`h-full rounded-full transition-all ${quotaUsed > 90 ? 'bg-red-500' : quotaUsed > 70 ? 'bg-orange-500' : 'bg-blue-500'}`}
                              style={{ width: `${Math.min(quotaUsed, 100)}%` }}
                            />
                          </div>
                        )}
                      </div>
                    )}

                    {/* 操作按钮（管理员只显示复制） */}
                    {!isAdmin && (
                      <div className="flex items-center gap-1 pt-1">
                        <Button variant="outline" size="sm" className="h-6 text-[11px] gap-1" onClick={() => handleRegenerate(entry.key)}>
                          <RotateCcw className="h-3 w-3" /> 重新生成
                        </Button>
                        <Button variant="outline" size="sm" className="h-6 text-[11px] gap-1" onClick={() => handleResetUsage(entry.key)}>
                          <RefreshCw className="h-3 w-3" /> 重置用量
                        </Button>
                        <Button variant="ghost" size="sm" className="h-6 w-6 p-0 ml-auto" onClick={() => setConfirmDelete(entry.key)}>
                          <Trash2 className="h-3.5 w-3.5 text-red-500" />
                        </Button>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* 新建 Key 弹窗 */}
      <Dialog open={showAdd} onOpenChange={setShowAdd}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>新建 API Key</DialogTitle>
            <DialogDescription>创建后请立即复制 Key，之后只能查看脱敏版本</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-sm">名称 / 标记</label>
              <Input className="h-8 mt-1" value={newName} onChange={e => setNewName(e.target.value)} placeholder="用户A" />
            </div>
            <div>
              <label className="text-sm">分组（可选）</label>
              <select className="h-8 w-full rounded-md border border-input bg-background px-2 text-sm mt-1" value={newGroup} onChange={e => setNewGroup(e.target.value)}>
                <option value="">不使用分组</option>
                {Object.keys(groups).map(g => <option key={g} value={g}>{g}</option>)}
              </select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-sm">计费倍率（覆盖分组）</label>
                <Input className="h-8 mt-1 font-mono" value={newRate} onChange={e => setNewRate(e.target.value)} placeholder="留空用分组默认" />
              </div>
              <div>
                <label className="text-sm">月额度 tokens（覆盖分组）</label>
                <Input className="h-8 mt-1 font-mono" value={newQuota} onChange={e => setNewQuota(e.target.value)} placeholder="留空用分组默认" />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowAdd(false)}>取消</Button>
            <Button onClick={handleAdd}>创建</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 新建分组弹窗 */}
      <Dialog open={showGroupEdit} onOpenChange={setShowGroupEdit}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>新建 / 编辑分组</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-sm">分组名</label>
              <Input className="h-8 mt-1 font-mono" value={groupName} onChange={e => setGroupName(e.target.value)} placeholder="free / standard / vip" />
            </div>
            <div>
              <label className="text-sm">计费倍率</label>
              <Input className="h-8 mt-1 font-mono" value={groupRate} onChange={e => setGroupRate(e.target.value)} placeholder="0.08" />
            </div>
            <div>
              <label className="text-sm">月额度 tokens（-1 = 无限）</label>
              <Input className="h-8 mt-1 font-mono" value={groupQuota} onChange={e => setGroupQuota(e.target.value)} placeholder="1000000" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowGroupEdit(false)}>取消</Button>
            <Button onClick={handleSaveGroup}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 删除确认 */}
      <Dialog open={confirmDelete !== null} onOpenChange={open => { if (!open) setConfirmDelete(null) }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>确认删除</DialogTitle>
            <DialogDescription>删除后无法恢复，使用此 Key 的客户端将无法访问。</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmDelete(null)}>取消</Button>
            <Button variant="destructive" onClick={() => confirmDelete && handleDelete(confirmDelete)}>删除</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
