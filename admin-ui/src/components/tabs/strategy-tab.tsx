import { useState, useEffect } from 'react'
import { Save, RotateCcw, ArrowRight, Plus, X } from 'lucide-react'
import { toast } from 'sonner'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { getModelList, getRoutingConfig, setRoutingConfig } from '@/api/credentials'
import type { ModelInfo } from '@/types/api'

export function StrategyTab() {
  const [models, setModels] = useState<ModelInfo[]>([])
  const [freeModels, setFreeModels] = useState<Set<string>>(new Set())
  const [savedFreeModels, setSavedFreeModels] = useState<Set<string>>(new Set())
  const [savedCustomModels, setSavedCustomModels] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [newModelId, setNewModelId] = useState('')

  useEffect(() => {
    Promise.all([getModelList(), getRoutingConfig()])
      .then(([modelList, routing]) => {
        setModels(modelList)
        const fm = new Set(routing.freeModels)
        setFreeModels(fm)
        setSavedFreeModels(fm)
        setSavedCustomModels(routing.customModels ?? [])
      })
      .catch(() => toast.error('加载配置失败'))
      .finally(() => setLoading(false))
  }, [])

  // 当前自定义模型列表（从 models 中提取）
  const currentCustomIds = models.filter(m => m.custom).map(m => m.id)

  const hasChanges = (() => {
    if (freeModels.size !== savedFreeModels.size) return true
    for (const m of freeModels) {
      if (!savedFreeModels.has(m)) return true
    }
    // 自定义模型变化
    const customIds = currentCustomIds
    if (customIds.length !== savedCustomModels.length) return true
    for (let i = 0; i < customIds.length; i++) {
      if (customIds[i] !== savedCustomModels[i]) return true
    }
    return false
  })()
  const toggleModel = (modelId: string) => {
    setFreeModels(prev => {
      const next = new Set(prev)
      next.has(modelId) ? next.delete(modelId) : next.add(modelId)
      return next
    })
  }

  const handleAddCustomModel = () => {
    const id = newModelId.trim()
    if (!id) return
    if (models.some(m => m.id === id)) {
      toast.error('该模型 ID 已存在')
      return
    }
    setModels(prev => [...prev, { id, displayName: id, custom: true }])
    setNewModelId('')
  }

  const handleDeleteCustomModel = (modelId: string) => {
    setModels(prev => prev.filter(m => m.id !== modelId))
    setFreeModels(prev => {
      const next = new Set(prev)
      next.delete(modelId)
      return next
    })
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      const customIds = models.filter(m => m.custom).map(m => m.id)
      await setRoutingConfig({ freeModels: Array.from(freeModels), customModels: customIds })
      setSavedFreeModels(new Set(freeModels))
      setSavedCustomModels(customIds)
      toast.success('路由配置已保存')
    } catch {
      toast.error('保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleReset = () => {
    setFreeModels(new Set(savedFreeModels))
    // 恢复自定义模型列表
    setModels(prev => {
      const builtins = prev.filter(m => !m.custom)
      const customs = savedCustomModels.map(id => ({ id, displayName: id, custom: true as const }))
      return [...builtins, ...customs]
    })
  }
  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
      </div>
    )
  }

  const proModels = models.filter(m => !freeModels.has(m.id))
  const freeModelList = models.filter(m => freeModels.has(m.id))

  return (
    <div className="space-y-6">
      {/* 路由说明 */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">路由策略</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground space-y-1">
          <p>免费模型请求 <ArrowRight className="inline h-3 w-3" /> 优先使用免费分组凭据，耗尽后回退到 Pro/高优先级分组</p>
          <p>Pro 模型请求 <ArrowRight className="inline h-3 w-3" /> 仅使用 Pro/高优先级分组凭据（跳过免费分组）</p>
        </CardContent>
      </Card>

      {/* 添加自定义模型 + 操作按钮 */}
      <div className="flex items-center gap-2 flex-wrap">
        <Input
          placeholder="输入自定义模型 ID"
          value={newModelId}
          onChange={e => setNewModelId(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleAddCustomModel()}
          className="w-full md:w-64"
        />
        <Button onClick={handleAddCustomModel} disabled={!newModelId.trim()} size="sm" variant="outline">
          <Plus className="h-4 w-4 mr-1" />
          添加模型
        </Button>
        <div className="flex-1" />
        <Button onClick={handleSave} disabled={!hasChanges || saving} size="sm">
          <Save className="h-4 w-4 mr-1" />
          {saving ? '保存中...' : '保存配置'}
        </Button>
        <Button onClick={handleReset} disabled={!hasChanges} size="sm" variant="outline">
          <RotateCcw className="h-4 w-4 mr-1" />
          重置
        </Button>
      </div>
      {/* 两列模型列表 */}
      <div className="grid gap-6 md:grid-cols-2">
        {/* 免费模型 */}
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center gap-2">
              <CardTitle className="text-base">免费模型</CardTitle>
              <Badge variant="secondary">{freeModelList.length}</Badge>
            </div>
          </CardHeader>
          <CardContent>
            {freeModelList.length === 0 ? (
              <p className="text-sm text-muted-foreground py-4 text-center">
                从右侧勾选模型添加到免费列表
              </p>
            ) : (
              <div className="space-y-2">
                {freeModelList.map(m => (
                  <ModelRow key={m.id} model={m} checked={true} onToggle={() => toggleModel(m.id)} onDelete={m.custom ? () => handleDeleteCustomModel(m.id) : undefined} />
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Pro 模型 */}
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center gap-2">
              <CardTitle className="text-base">Pro 模型</CardTitle>
              <Badge variant="secondary">{proModels.length}</Badge>
            </div>
          </CardHeader>
          <CardContent>
            {proModels.length === 0 ? (
              <p className="text-sm text-muted-foreground py-4 text-center">所有模型已设为免费</p>
            ) : (
              <div className="space-y-2">
                {proModels.map(m => (
                  <ModelRow key={m.id} model={m} checked={false} onToggle={() => toggleModel(m.id)} onDelete={m.custom ? () => handleDeleteCustomModel(m.id) : undefined} />
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

function ModelRow({ model, checked, onToggle, onDelete }: { model: ModelInfo; checked: boolean; onToggle: () => void; onDelete?: () => void }) {
  return (
    <label className="flex items-center gap-2 py-1.5 px-2 rounded hover:bg-muted/50 cursor-pointer">
      <Checkbox checked={checked} onCheckedChange={onToggle} />
      <span className="text-sm font-mono">{model.displayName}</span>
      {model.custom && <Badge variant="outline" className="text-[10px] px-1 py-0">自定义</Badge>}
      <span className="text-xs text-muted-foreground ml-auto">{model.id}</span>
      {onDelete && (
        <button
          onClick={e => { e.preventDefault(); e.stopPropagation(); onDelete() }}
          className="p-0.5 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive transition-colors"
          title="删除自定义模型"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </label>
  )
}
