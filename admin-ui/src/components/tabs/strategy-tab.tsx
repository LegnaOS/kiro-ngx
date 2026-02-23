import { useState, useEffect } from 'react'
import { Save, RotateCcw, ArrowRight } from 'lucide-react'
import { toast } from 'sonner'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Checkbox } from '@/components/ui/checkbox'
import { getModelList, getRoutingConfig, setRoutingConfig } from '@/api/credentials'
import type { ModelInfo } from '@/types/api'

export function StrategyTab() {
  const [models, setModels] = useState<ModelInfo[]>([])
  const [freeModels, setFreeModels] = useState<Set<string>>(new Set())
  const [savedFreeModels, setSavedFreeModels] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    Promise.all([getModelList(), getRoutingConfig()])
      .then(([modelList, routing]) => {
        setModels(modelList)
        const fm = new Set(routing.freeModels)
        setFreeModels(fm)
        setSavedFreeModels(fm)
      })
      .catch(() => toast.error('加载配置失败'))
      .finally(() => setLoading(false))
  }, [])

  const hasChanges = (() => {
    if (freeModels.size !== savedFreeModels.size) return true
    for (const m of freeModels) {
      if (!savedFreeModels.has(m)) return true
    }
    return false
  })()

  const toggleModel = (modelId: string) => {
    setFreeModels(prev => {
      const next = new Set(prev)
      if (next.has(modelId)) {
        next.delete(modelId)
      } else {
        next.add(modelId)
      }
      return next
    })
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      await setRoutingConfig({ freeModels: Array.from(freeModels) })
      setSavedFreeModels(new Set(freeModels))
      toast.success('路由配置已保存')
    } catch {
      toast.error('保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleReset = () => {
    setFreeModels(new Set(savedFreeModels))
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

      {/* 操作按钮 */}
      <div className="flex gap-2">
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
                  <ModelRow key={m.id} model={m} checked={true} onToggle={() => toggleModel(m.id)} />
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
                  <ModelRow key={m.id} model={m} checked={false} onToggle={() => toggleModel(m.id)} />
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

function ModelRow({ model, checked, onToggle }: { model: ModelInfo; checked: boolean; onToggle: () => void }) {
  return (
    <label className="flex items-center gap-2 py-1.5 px-2 rounded hover:bg-muted/50 cursor-pointer">
      <Checkbox checked={checked} onCheckedChange={onToggle} />
      <span className="text-sm font-mono">{model.displayName}</span>
      <span className="text-xs text-muted-foreground ml-auto">{model.id}</span>
    </label>
  )
}
