import { useState, useEffect, Suspense } from 'react'
import { Puzzle, Info } from 'lucide-react'
import { toast } from 'sonner'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { getPlugins, type PluginManifest } from '@/api/plugins'
import { getPluginComponent } from '@/components/plugins/plugin-registry'

export function PluginsTab() {
  const [plugins, setPlugins] = useState<PluginManifest[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getPlugins()
      .then(list => {
        setPlugins(list)
        if (list.length > 0) setSelected(list[0].id)
      })
      .catch(() => toast.error('获取插件列表失败'))
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
      </div>
    )
  }

  if (plugins.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
        <Puzzle className="h-12 w-12 mb-4 opacity-30" />
        <p>暂无已加载的插件</p>
        <p className="text-xs mt-1">在 plugins/ 目录下添加插件后重启服务</p>
      </div>
    )
  }

  const current = plugins.find(p => p.id === selected)
  const Component = selected ? getPluginComponent(selected) : null

  return (
    <div className="flex gap-4 min-h-[60vh]">
      {/* 左侧插件列表 */}
      <div className="w-48 flex-shrink-0 space-y-1">
        {plugins.map(p => (
          <button
            key={p.id}
            onClick={() => setSelected(p.id)}
            className={`w-full text-left px-3 py-2.5 rounded-md text-sm transition-colors ${
              selected === p.id
                ? 'bg-primary text-primary-foreground'
                : 'hover:bg-muted text-muted-foreground hover:text-foreground'
            }`}
          >
            <div className="font-medium">{p.name}</div>
            <div className={`text-xs mt-0.5 ${selected === p.id ? 'opacity-70' : 'opacity-50'}`}>
              v{p.version}
            </div>
          </button>
        ))}
      </div>

      {/* 右侧内容区 */}
      <div className="flex-1 min-w-0">
        {Component ? (
          <Suspense fallback={
            <div className="flex items-center justify-center py-12">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
            </div>
          }>
            <Component />
          </Suspense>
        ) : current ? (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Info className="h-4 w-4" /> {current.name}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-sm text-muted-foreground">{current.description}</p>
              <div className="flex gap-2 flex-wrap">
                <Badge variant="outline">v{current.version}</Badge>
                <Badge variant="secondary">API: {current.api_prefix}</Badge>
                <Badge variant={current.has_frontend ? 'success' : 'secondary'}>
                  {current.has_frontend ? '有前端页面' : '仅后端'}
                </Badge>
              </div>
            </CardContent>
          </Card>
        ) : null}
      </div>
    </div>
  )
}
