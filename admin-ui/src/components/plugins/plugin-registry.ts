import { lazy, type ComponentType } from 'react'

// 插件 ID -> 前端组件的静态注册表
// 新增插件前端时在此添加一行即可
const registry: Record<string, () => Promise<{ default: ComponentType }>> = {
  'remote-api': () => import('./remote-api-plugin'),
}

export function getPluginComponent(pluginId: string): ComponentType | null {
  const loader = registry[pluginId]
  if (!loader) return null
  return lazy(loader)
}
