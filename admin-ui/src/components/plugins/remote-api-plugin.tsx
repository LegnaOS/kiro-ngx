import { useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { Input } from '@/components/ui/input'
import { getRemoteApiConfig, updateRemoteApiConfig, type RemoteApiName } from '@/api/plugins'

type RemoteApiSwitchItem = {
  key: RemoteApiName
  title: string
  desc: string
}

const API_ITEMS: RemoteApiSwitchItem[] = [
  { key: 'availableCredentials', title: '可用凭据数量', desc: 'GET /api/remote/credentials/available' },
  { key: 'batchImport', title: '批量导入凭据', desc: 'POST /api/remote/credentials/batch-import' },
  { key: 'restart', title: '远程重启服务', desc: 'POST /api/remote/server/restart' },
  { key: 'refreshQuota', title: '刷新额度', desc: 'POST /api/remote/quota/refresh' },
  { key: 'totalRemainingQuota', title: '获取总剩余额度', desc: 'GET /api/remote/quota/total-remaining' },
  { key: 'todayTokenTotal', title: '今日 Token 总量', desc: 'GET /api/remote/stats/today-tokens' },
  { key: 'totalCalls', title: '总调用次数', desc: 'GET /api/remote/stats/total-calls' },
]

async function sha256Hex(value: string): Promise<string> {
  const encoded = new TextEncoder().encode(value)
  if (!globalThis.crypto?.subtle) return '当前环境不支持 crypto.subtle'
  const digest = await crypto.subtle.digest('SHA-256', encoded)
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

export default function RemoteApiPlugin() {
  const [enabledApis, setEnabledApis] = useState<Record<RemoteApiName, boolean> | null>(null)
  const [saving, setSaving] = useState(false)
  const [adminKey, setAdminKey] = useState('')
  const [tokenHash, setTokenHash] = useState('')

  useEffect(() => {
    getRemoteApiConfig()
      .then((cfg) => setEnabledApis(cfg.enabledApis))
      .catch(() => toast.error('获取远程 API 配置失败'))
  }, [])

  useEffect(() => {
    let cancelled = false
    if (!adminKey.trim()) {
      setTokenHash('')
      return
    }
    sha256Hex(adminKey.trim())
      .then((hex) => {
        if (!cancelled) setTokenHash(hex)
      })
      .catch(() => {
        if (!cancelled) setTokenHash('计算失败')
      })
    return () => {
      cancelled = true
    }
  }, [adminKey])

  const disabledCount = useMemo(() => {
    if (!enabledApis) return 0
    return API_ITEMS.filter((it) => !enabledApis[it.key]).length
  }, [enabledApis])

  const updateOne = async (key: RemoteApiName, value: boolean) => {
    if (!enabledApis) return
    const prev = enabledApis
    const next = { ...enabledApis, [key]: value }
    setEnabledApis(next)
    setSaving(true)
    try {
      const data = await updateRemoteApiConfig({ [key]: value })
      setEnabledApis(data.enabledApis)
    } catch {
      setEnabledApis(prev)
      toast.error('保存失败')
    } finally {
      setSaving(false)
    }
  }

  if (!enabledApis) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">远程 API 开关</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="text-sm text-muted-foreground">
            当前已禁用 {disabledCount} 个接口。关闭后对应远程端点会返回 403。
          </div>
          {API_ITEMS.map((it) => (
            <div key={it.key} className="flex items-center justify-between rounded-md border p-3">
              <div className="min-w-0 pr-4">
                <div className="font-medium text-sm">{it.title}</div>
                <div className="text-xs text-muted-foreground font-mono">{it.desc}</div>
              </div>
              <Switch
                checked={!!enabledApis[it.key]}
                disabled={saving}
                onCheckedChange={(v) => updateOne(it.key, v)}
              />
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">远程鉴权 Token 计算</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="text-sm text-muted-foreground">
            远程接口鉴权 token = SHA-256(adminApiKey)。用于 `x-api-key` 或 `Authorization: Bearer`。
          </div>
          <Input
            type="password"
            placeholder="输入 adminApiKey 计算 hash"
            value={adminKey}
            onChange={(e) => setAdminKey(e.target.value)}
          />
          <Input readOnly value={tokenHash} placeholder="SHA-256 结果" className="font-mono text-xs" />
          <Button
            variant="outline"
            onClick={() => {
              if (!tokenHash) return
              navigator.clipboard.writeText(tokenHash).then(
                () => toast.success('已复制 token hash'),
                () => toast.error('复制失败'),
              )
            }}
            disabled={!tokenHash}
          >
            复制 Hash
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}

