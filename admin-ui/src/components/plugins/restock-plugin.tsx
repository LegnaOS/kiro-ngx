import { useState, useEffect, useCallback, useRef } from 'react'
import { Package, ShoppingCart, FileText, Loader2, Search, Copy, Download, ChevronDown, ChevronRight, Play, Square, RefreshCw, RotateCcw, Power, Shield } from 'lucide-react'
import { toast } from 'sonner'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { addCredential, getCredentials } from '@/api/credentials'
import {
  restockLogin, getRestockInventory, getRestockOrders,
  getRestockOrderDetail, checkRestockBan,
  getRestockConfig, saveRestockConfig, type RestockConfig,
  restockDeliver, restockBatchReplace, analyzeRestockOrders,
  startAutoReplace, stopAutoReplace, getAutoReplaceStatus,
  startAutoRestock, stopAutoRestock, getAutoRestockStatus,
  refreshWarrantyList,
} from '@/api/plugins'

const STATUS_MAP: Record<string, string> = {
  paid: '已支付', completed: '已完成', cancelled: '已取消', pending: '待支付',
}

const DEFAULT_REGIONS = ['eu-north-1', 'us-east-1']

function checkWarranty(deliveredAt: string, warrantyHours: number): { expired: boolean; msg: string } {
  if (warrantyHours <= 0) return { expired: false, msg: '无质保' }
  if (!deliveredAt) return { expired: false, msg: '无发货时间' }
  const delivered = new Date(deliveredAt)
  const expire = new Date(delivered.getTime() + warrantyHours * 3600_000)
  const now = new Date()
  if (now > expire) return { expired: true, msg: '已过保' }
  const remainH = (expire.getTime() - now.getTime()) / 3600_000
  return { expired: false, msg: `剩余 ${remainH.toFixed(1)}h` }
}

async function sha256Hex(text: string): Promise<string> {
  if (crypto.subtle) {
    const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
    return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('')
  }
  // HTTP 环境下 crypto.subtle 不可用，用简单 hash 替代（仅用于 UI 比对）
  let h = 0
  for (let i = 0; i < text.length; i++) { h = ((h << 5) - h + text.charCodeAt(i)) | 0 }
  return (h >>> 0).toString(16).padStart(8, '0')
}

export default function RestockPlugin() {
  // 服务端配置
  const [config, setConfig] = useState<RestockConfig>({ email: '', password: '', token: '' })
  const [configLoaded, setConfigLoaded] = useState(false)
  const saveTimer = useRef<ReturnType<typeof setTimeout>>()
  const [loggingIn, setLoggingIn] = useState(false)

  const [inventory, setInventory] = useState<any[] | null>(null)
  const [loadingInv, setLoadingInv] = useState(false)
  const [orders, setOrders] = useState<any[] | null>(null)
  const [loadingOrders, setLoadingOrders] = useState(false)
  // 点击展开详情（支持多订单同时展开 + 缓存）
  const [expandedOrders, setExpandedOrders] = useState<Set<number>>(new Set())
  const [detailCache, setDetailCache] = useState<Map<number, any>>(new Map())
  const [loadingDetailIds, setLoadingDetailIds] = useState<Set<number>>(new Set())
  const [importingDeliveryId, setImportingDeliveryId] = useState<number | null>(null)
  // 已有凭据的 refreshTokenHash 集合（用于判断账号是否已导入）
  const [credHashSet, setCredHashSet] = useState<Set<string>>(new Set())
  // account refreshToken -> sha256 hash 映射（预计算）
  const [accountHashMap, setAccountHashMap] = useState<Map<string, string>>(new Map())
  // 封禁检测（展开订单时自动执行）
  const [banData, setBanData] = useState<Map<number, { banned_count: number; results: Array<{ email: string; banned: boolean }> }>>(new Map())
  const [loadingBan, setLoadingBan] = useState(false)
  // 过保的 delivery 默认收起
  const [collapsedDeliveries, setCollapsedDeliveries] = useState<Set<number>>(new Set())
  // 换号/提货操作中
  const [replacingDeliveryId, setReplacingDeliveryId] = useState<number | null>(null)
  const [deliveringOrderId, setDeliveringOrderId] = useState<number | null>(null)
  const [deliverCount, setDeliverCount] = useState('')
  // 全部导入
  const [importingAll, setImportingAll] = useState(false)
  // 过保分析
  const [analyzeResult, setAnalyzeResult] = useState<any | null>(null)
  const [loadingAnalyze, setLoadingAnalyze] = useState(false)
  // 自动补号
  const [arStatus, setArStatus] = useState<any | null>(null)
  const [arLoading, setArLoading] = useState(false)
  const [arInterval, setArInterval] = useState('1')
  const arPollRef = useRef<ReturnType<typeof setInterval>>()
  // 自动补货
  const [restockStatus, setRestockStatus] = useState<any | null>(null)
  const [restockLoading, setRestockLoading] = useState(false)
  const [restockInterval, setRestockInterval] = useState('30')
  const restockPollRef = useRef<ReturnType<typeof setInterval>>()
  const [warrantyRefreshing, setWarrantyRefreshing] = useState(false)

  // 初始化从服务端加载配置
  useEffect(() => {
    getRestockConfig()
      .then(c => { setConfig(c); setConfigLoaded(true) })
      .catch(() => setConfigLoaded(true))
  }, [])

  // debounce 保存配置到服务端
  const debounceSave = useCallback((c: RestockConfig) => {
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => {
      saveRestockConfig(c).catch(() => {})
    }, 800)
  }, [])

  const updateConfig = (patch: Partial<RestockConfig>) => {
    const next = { ...config, ...patch }
    setConfig(next)
    debounceSave(next)
  }

  const handleLogin = async () => {
    if (!config.email.trim() || !config.password.trim()) { toast.error('请填写账号和密码'); return }
    setLoggingIn(true)
    try {
      const t = await restockLogin(config.email, config.password)
      updateConfig({ token: t })
      toast.success('登录成功')
    } catch (e: any) {
      toast.error(e?.response?.data?.error || '登录失败')
    } finally { setLoggingIn(false) }
  }

  const requireToken = useCallback(() => {
    if (!config.token.trim()) { toast.error('请先登录或填入 Token'); return false }
    return true
  }, [config.token])

  const fetchInventory = async () => {
    if (!requireToken()) return
    setLoadingInv(true)
    try { setInventory(await getRestockInventory(config.token)) }
    catch (e: any) { toast.error(e?.response?.data?.error || '库存查询失败') }
    finally { setLoadingInv(false) }
  }

  const fetchOrders = async () => {
    if (!requireToken()) return
    setLoadingOrders(true)
    try { setOrders((await getRestockOrders(config.token)).orders) }
    catch (e: any) { toast.error(e?.response?.data?.error || '订单查询失败') }
    finally { setLoadingOrders(false) }
  }

  // 点击订单行展开/收起详情
  const handleToggleOrder = async (orderId: number) => {
    // 折叠
    if (expandedOrders.has(orderId)) {
      setExpandedOrders(prev => { const next = new Set(prev); next.delete(orderId); return next })
      return
    }
    // 展开
    setExpandedOrders(prev => new Set(prev).add(orderId))

    // 已有缓存则跳过
    if (detailCache.has(orderId)) return

    setLoadingDetailIds(prev => new Set(prev).add(orderId))
    try {
      const [d, creds] = await Promise.all([
        getRestockOrderDetail(config.token, orderId),
        getCredentials().catch(() => null),
      ])
      setDetailCache(prev => new Map(prev).set(orderId, d))
      if (creds) {
        setCredHashSet(new Set(creds.credentials.map(c => c.refreshTokenHash).filter(Boolean) as string[]))
      }
      // 预计算 hash
      try {
        const allRts: string[] = []
        for (const del of d.deliveries || []) {
          for (const a of del.account_data || []) {
            const rt = a.refresh_token || ''
            if (rt) allRts.push(rt)
          }
        }
        const hashEntries = await Promise.all(allRts.map(async rt => [rt, await sha256Hex(rt)] as const))
        setAccountHashMap(prev => {
          const next = new Map(prev)
          for (const [k, v] of hashEntries) next.set(k, v)
          return next
        })
      } catch {}

      // 过保 delivery 默认收起
      const warrantyHours = (d as any).warranty_hours ?? orders?.find(o => o.id === orderId)?.warranty_hours ?? 0
      if (warrantyHours > 0 && d.deliveries) {
        const collapsed = new Set<number>()
        for (const del of d.deliveries) {
          const w = checkWarranty(del.delivered_at, warrantyHours)
          if (w.expired) collapsed.add(del.id)
        }
        setCollapsedDeliveries(prev => {
          const next = new Set(prev)
          for (const id of collapsed) next.add(id)
          return next
        })
      }

      // 封禁检测
      setLoadingBan(true)
      checkRestockBan(config.token, orderId)
        .then(res => {
          const map = new Map<number, { banned_count: number; results: Array<{ email: string; banned: boolean }> }>()
          for (const d of res.deliveries || []) {
            map.set(d.delivery_id, { banned_count: d.banned_count, results: d.results })
          }
          setBanData(prev => {
            const next = new Map(prev)
            for (const [k, v] of map) next.set(k, v)
            return next
          })
        })
        .catch(() => {})
        .finally(() => setLoadingBan(false))
    } catch (e: any) {
      toast.error(e?.response?.data?.error || '订单详情查询失败')
      setExpandedOrders(prev => { const next = new Set(prev); next.delete(orderId); return next })
    } finally {
      setLoadingDetailIds(prev => { const next = new Set(prev); next.delete(orderId); return next })
    }
  }

  // 复制发货批次凭据
  const handleCopyDelivery = (accountData: any[]) => {
    const accounts = accountData
      .map(a => { try { return JSON.parse(a.account_json) } catch { return null } })
      .filter(Boolean)
    navigator.clipboard.writeText(JSON.stringify(accounts, null, 2))
    toast.success(`已复制 ${accounts.length} 条凭据`)
  }

  // 导入发货批次凭据到系统（按 region 列表依次尝试，跳过已封号账号）
  const handleImportDelivery = async (deliveryId: number, accountData: any[]) => {
    setImportingDeliveryId(deliveryId)
    let success = 0, skipped = 0, bannedSkipped = 0
    const errors: string[] = []
    // 获取该批次的封禁数据
    const deliveryBan = banData.get(deliveryId)
    const bannedEmails = new Set(
      deliveryBan?.results?.filter(r => r.banned).map(r => r.email) ?? []
    )
    for (const item of accountData) {
      const email = item.email || '(未知)'
      // 跳过已封号账号
      if (bannedEmails.has(email)) { bannedSkipped++; continue }
      try {
        const jsonArr = JSON.parse(item.account_json)
        const cred = Array.isArray(jsonArr) ? jsonArr[0] : jsonArr
        if (!cred) { errors.push(`${email}: account_json 为空`); continue }
        const refreshToken = cred.refreshToken || cred.refresh_token || ''
        if (!refreshToken) { errors.push(`${email}: 缺少 refreshToken`); continue }
        // 跳过已存在的凭据
        const hash = await sha256Hex(refreshToken)
        if (credHashSet.has(hash)) { skipped++; continue }
        const clientId = (cred.clientId || cred.client_id || '').trim() || undefined
        const clientSecret = (cred.clientSecret || cred.client_secret || '').trim() || undefined
        const authMethod = (clientId && clientSecret) ? 'idc' as const : 'social' as const
        const base = { refreshToken, authMethod, clientId, clientSecret }
        let ok = false
        let lastErr = ''
        const importRegions = (() => {
          try { const s = localStorage.getItem('kiro-import-regions'); if (s) return JSON.parse(s) as string[] } catch {}
          return DEFAULT_REGIONS
        })()
        // 指定的区域优先，失败后继续尝试列表中其余区域
        const specifiedRegion = (item.region || cred.region || '').trim()
        const regionsToTry = specifiedRegion
          ? [specifiedRegion, ...importRegions.filter((r: string) => r !== specifiedRegion)]
          : importRegions
        for (const region of regionsToTry) {
          try {
            await addCredential({ ...base, authRegion: region })
            ok = true
            break
          } catch (e: any) {
            lastErr = e?.response?.data?.error || e?.response?.data?.message || e?.message || String(e)
          }
        }
        if (ok) { success++; credHashSet.add(hash) } else errors.push(`${email}: ${lastErr}`)
      } catch (e: any) { errors.push(`${email}: ${e?.message || '解析失败'}`) }
    }
    setImportingDeliveryId(null)
    const parts: string[] = []
    if (success) parts.push(`成功 ${success}`)
    if (skipped) parts.push(`跳过已存在 ${skipped}`)
    if (bannedSkipped) parts.push(`跳过封号 ${bannedSkipped}`)
    if (errors.length) parts.push(`失败 ${errors.length}`)
    if (errors.length === 0) {
      toast.success(`导入完成: ${parts.join(', ')}`)
    } else {
      toast.warning(`导入完成: ${parts.join(', ')}`)
      for (const err of errors.slice(0, 5)) toast.error(err)
      if (errors.length > 5) toast.error(`...还有 ${errors.length - 5} 条失败`)
    }
  }

  // 一键换号
  const handleBatchReplace = async (orderId: number, deliveryId: number) => {
    if (!requireToken()) return
    setReplacingDeliveryId(deliveryId)
    try {
      const res = await restockBatchReplace(config.token, orderId, deliveryId)
      const count = res?.replaced_count ?? '?'
      toast.success(`换号成功，替换 ${count} 个`)
    } catch (e: any) { toast.error(e?.response?.data?.error || '换号失败') }
    finally { setReplacingDeliveryId(null) }
  }

  // 提货
  const handleDeliver = async (orderId: number) => {
    if (!requireToken()) return
    const count = parseInt(deliverCount)
    if (!count || count <= 0) { toast.error('请输入有效的提货数量'); return }
    setDeliveringOrderId(orderId)
    try {
      await restockDeliver(config.token, orderId, count)
      toast.success(`提货成功: ${count} 个`)
      setDeliverCount('')
    } catch (e: any) { toast.error(e?.response?.data?.error || '提货失败') }
    finally { setDeliveringOrderId(null) }
  }

  // 全部导入：遍历所有订单，未过保+未封禁的账号全部导入
  const handleImportAll = async () => {
    if (!requireToken()) return
    if (!orders || orders.length === 0) { toast.error('请先查询订单'); return }
    setImportingAll(true)
    let totalSuccess = 0, totalSkipped = 0, totalBannedSkipped = 0, totalFail = 0

    try {
      // 获取已有凭据 hash 集合
      const creds = await getCredentials().catch(() => null)
      const existingHashes = new Set(
        creds?.credentials.map(c => c.refreshTokenHash).filter(Boolean) as string[] ?? []
      )

      for (const order of orders) {
        const warrantyHours = order.warranty_hours ?? 0
        let detail: any
        try {
          detail = await getRestockOrderDetail(config.token, order.id)
        } catch { continue }

        // 封禁检测
        let banMap = new Map<number, Set<string>>()
        try {
          const banRes = await checkRestockBan(config.token, order.id)
          for (const d of banRes.deliveries || []) {
            const bannedEmails = new Set(d.results?.filter(r => r.banned).map(r => r.email) ?? [])
            banMap.set(d.delivery_id, bannedEmails)
          }
        } catch { /* 封禁检测失败不阻塞 */ }

        for (const d of detail.deliveries || []) {
          const wh = (detail as any).warranty_hours ?? warrantyHours
          const warranty = checkWarranty(d.delivered_at, wh)
          if (warranty.expired) continue

          const bannedEmails = banMap.get(d.id) ?? new Set()
          for (const acc of d.account_data || []) {
            const email = acc.email || ''
            if (bannedEmails.has(email)) { totalBannedSkipped++; continue }
            try {
              const jsonArr = JSON.parse(acc.account_json)
              const cred = Array.isArray(jsonArr) ? jsonArr[0] : jsonArr
              if (!cred) continue
              const refreshToken = cred.refreshToken || cred.refresh_token || ''
              if (!refreshToken) continue
              const hash = await sha256Hex(refreshToken)
              if (existingHashes.has(hash)) { totalSkipped++; continue }
              const clientId = (cred.clientId || cred.client_id || '').trim() || undefined
              const clientSecret = (cred.clientSecret || cred.client_secret || '').trim() || undefined
              const authMethod = (clientId && clientSecret) ? 'idc' as const : 'social' as const
              const base = { refreshToken, authMethod, clientId, clientSecret }
              const importRegions = (() => {
                try { const s = localStorage.getItem('kiro-import-regions'); if (s) return JSON.parse(s) as string[] } catch {}
                return DEFAULT_REGIONS
              })()
              const specifiedRegion = (acc.region || cred.region || '').trim()
              const regionsToTry = specifiedRegion
                ? [specifiedRegion, ...importRegions.filter((r: string) => r !== specifiedRegion)]
                : importRegions
              let ok = false
              for (const region of regionsToTry) {
                try {
                  await addCredential({ ...base, authRegion: region })
                  ok = true
                  break
                } catch {}
              }
              if (ok) { totalSuccess++; existingHashes.add(hash) } else { totalFail++ }
            } catch { totalFail++ }
          }
        }
      }
    } catch (e: any) {
      toast.error(e?.message || '导入异常')
    }

    setImportingAll(false)
    const parts: string[] = []
    if (totalSuccess) parts.push(`成功 ${totalSuccess}`)
    if (totalSkipped) parts.push(`跳过已存在 ${totalSkipped}`)
    if (totalBannedSkipped) parts.push(`跳过封号 ${totalBannedSkipped}`)
    if (totalFail) parts.push(`失败 ${totalFail}`)
    if (totalFail === 0) {
      toast.success(`全部导入完成: ${parts.join(', ')}`)
    } else {
      toast.warning(`全部导入完成: ${parts.join(', ')}`)
    }
  }

  // 过保分析
  const handleAnalyze = async () => {
    if (!requireToken()) return
    setLoadingAnalyze(true)
    try { setAnalyzeResult(await analyzeRestockOrders(config.token)) }
    catch (e: any) { toast.error(e?.response?.data?.error || '分析失败') }
    finally { setLoadingAnalyze(false) }
  }

  // 自动补号控制
  const pollArStatus = useCallback(async () => {
    try { setArStatus(await getAutoReplaceStatus()) } catch {}
  }, [])

  const handleStartAr = async () => {
    setArLoading(true)
    try {
      await startAutoReplace(parseInt(arInterval) || 1)
      toast.success('自动补号已启动')
      pollArStatus()
    } catch (e: any) { toast.error(e?.response?.data?.error || '启动失败') }
    finally { setArLoading(false) }
  }

  const handleStopAr = async () => {
    setArLoading(true)
    try {
      await stopAutoReplace()
      toast.success('自动补号已停止')
      pollArStatus()
    } catch (e: any) { toast.error(e?.response?.data?.error || '停止失败') }
    finally { setArLoading(false) }
  }

  // 轮询自动补号状态（运行中 1秒，停止时 5秒）
  useEffect(() => {
    pollArStatus()
    const ms = arStatus?.running ? 1000 : 5000
    arPollRef.current = setInterval(pollArStatus, ms)
    return () => { if (arPollRef.current) clearInterval(arPollRef.current) }
  }, [pollArStatus, arStatus?.running])

  // 自动补货
  const pollRestockStatus = useCallback(async () => {
    try { setRestockStatus(await getAutoRestockStatus()) } catch {}
  }, [])

  const handleStartRestock = async () => {
    setRestockLoading(true)
    try {
      await startAutoRestock(parseInt(restockInterval) || 30)
      toast.success('自动补货已启动')
      pollRestockStatus()
    } catch (e: any) { toast.error(e?.response?.data?.error || '启动失败') }
    finally { setRestockLoading(false) }
  }

  const handleStopRestock = async () => {
    setRestockLoading(true)
    try {
      await stopAutoRestock()
      toast.success('自动补货已停止')
      pollRestockStatus()
    } catch (e: any) { toast.error(e?.response?.data?.error || '停止失败') }
    finally { setRestockLoading(false) }
  }

  const handleRefreshWarranty = async () => {
    setWarrantyRefreshing(true)
    try {
      const res = await refreshWarrantyList()
      toast.success(`在保名单已刷新，共 ${res.warranty_count} 个账号`)
      pollRestockStatus()
    } catch (e: any) { toast.error(e?.response?.data?.error || '刷新失败') }
    finally { setWarrantyRefreshing(false) }
  }

  useEffect(() => {
    pollRestockStatus()
    const ms = restockStatus?.running ? 5000 : 15000
    restockPollRef.current = setInterval(pollRestockStatus, ms)
    return () => { if (restockPollRef.current) clearInterval(restockPollRef.current) }
  }, [pollRestockStatus, restockStatus?.running])

  if (!configLoaded) {
    return <div className="flex items-center justify-center py-12"><Loader2 className="h-6 w-6 animate-spin" /></div>
  }

  return (
    <div className="space-y-6">
      {/* 认证区 */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <ShoppingCart className="h-4 w-4" /> Kiroshop 认证
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
            <Input type="text" autoComplete="off" placeholder="邮箱"
              value={config.email} onChange={e => updateConfig({ email: e.target.value })} />
            <Input type="text" autoComplete="off" placeholder="密码"
              value={config.password} onChange={e => updateConfig({ password: e.target.value })} />
            <Input type="text" autoComplete="off" placeholder="Token（登录后自动填充）"
              value={config.token} onChange={e => updateConfig({ token: e.target.value })} />
          </div>
          <div className="flex gap-2">
            <Button size="sm" onClick={handleLogin} disabled={loggingIn}>
              {loggingIn ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : null}
              {loggingIn ? '登录中...' : '登录获取 Token'}
            </Button>
            {config.token && (
              <span className="text-xs text-muted-foreground self-center">
                Token: {config.token.slice(0, 20)}...
              </span>
            )}
          </div>
        </CardContent>
      </Card>

      {/* 功能区 */}
      <div className="grid gap-6 md:grid-cols-2">
        {/* 库存查询 */}
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-base flex items-center gap-2">
                <Package className="h-4 w-4" /> 库存查询
              </CardTitle>
              <Button size="sm" onClick={fetchInventory} disabled={loadingInv}>
                {loadingInv ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4 mr-1" />}
                {loadingInv ? '查询中...' : '查询'}
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            {inventory === null ? (
              <p className="text-sm text-muted-foreground text-center py-4">点击查询获取库存信息</p>
            ) : inventory.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-4">暂无商品</p>
            ) : (
              <div className="space-y-2 max-h-80 overflow-y-auto">
                {inventory.map((item, i) => (
                  <div key={i} className="flex items-center justify-between py-2 px-2 rounded hover:bg-muted/50 text-sm">
                    <div>
                      <div className="font-medium">{item.name}</div>
                      <div className="text-xs text-muted-foreground">
                        ￥{item.price}
                        {item.max_per_user > 0 && ` · 限购${item.max_per_user}`}
                        {item.is_special === 1 && ` · 特价余${item.special_remaining}`}
                      </div>
                    </div>
                    <Badge variant={item.stock > 0 ? 'secondary' : 'destructive'}>库存 {item.stock}</Badge>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* 有效订单 — 点击展开详情 */}
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-base flex items-center gap-2">
                <FileText className="h-4 w-4" /> 有效订单
              </CardTitle>
              <Button size="sm" onClick={fetchOrders} disabled={loadingOrders}>
                {loadingOrders ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4 mr-1" />}
                {loadingOrders ? '查询中...' : '查询'}
              </Button>
              <Button size="sm" variant="outline" onClick={handleImportAll}
                disabled={importingAll || !orders || orders.length === 0}>
                {importingAll ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Download className="h-4 w-4 mr-1" />}
                {importingAll ? '导入中...' : '全部导入'}
              </Button>
            </div>
          </CardHeader>
          <CardContent className="p-0">
            {orders === null ? (
              <p className="text-sm text-muted-foreground text-center py-4 px-4">点击查询获取订单列表</p>
            ) : orders.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-4 px-4">暂无有效订单</p>
            ) : (
              <div className="divide-y max-h-[500px] overflow-y-auto">
                {orders.map(o => (
                  <div key={o.id}>
                    <div
                      className="flex items-center gap-2 py-2.5 px-4 cursor-pointer hover:bg-muted/50 transition-colors text-sm"
                      onClick={() => handleToggleOrder(o.id)}
                    >
                      {expandedOrders.has(o.id)
                        ? <ChevronDown className="h-3.5 w-3.5 shrink-0" />
                        : <ChevronRight className="h-3.5 w-3.5 shrink-0" />}
                      <span className="font-medium">#{o.id}</span>
                      <span className="text-muted-foreground truncate">{o.order_no}</span>
                      <span className="ml-auto text-xs text-muted-foreground">x{o.quantity} ￥{o.total_price}</span>
                      <Badge variant={o.status === 'paid' ? 'success' : o.status === 'completed' ? 'secondary' : 'warning'} className="ml-1">
                        {STATUS_MAP[o.status] || o.status}
                      </Badge>
                    </div>
                    {/* 展开的订单详情 */}
                    {expandedOrders.has(o.id) && (() => {
                      const detail = detailCache.get(o.id)
                      const loadingDetail = loadingDetailIds.has(o.id)
                      return (
                      <div className="px-4 pb-3 bg-muted/30">
                        {loadingDetail ? (
                          <div className="flex items-center gap-2 py-3 text-sm text-muted-foreground">
                            <Loader2 className="h-4 w-4 animate-spin" /> 加载详情...
                          </div>
                        ) : detail ? (
                          <div className="space-y-2 pt-2 text-sm">
                            <div className="text-xs text-muted-foreground">
                              {detail.product_name || `商品#${detail.product_id}`} · 配额: {detail.quota_type || '-'} · {detail.created_at}
                            </div>
                            {detail.deliveries?.length > 0 ? (
                              detail.deliveries.slice().reverse().map((d: any, di: number) => {
                                const accounts: any[] = d.account_data || []
                                const warrantyHours = (detail as any).warranty_hours ?? orders?.find(ord => ord.id === o.id)?.warranty_hours ?? 0
                                const warranty = checkWarranty(d.delivered_at, warrantyHours)
                                const isCollapsed = collapsedDeliveries.has(d.id)
                                const deliveryBan = banData.get(d.id)
                                return (
                                  <div key={di} className="rounded border bg-background p-2.5 space-y-1.5">
                                    <div
                                      className="flex items-center justify-between flex-wrap gap-1 cursor-pointer"
                                      onClick={e => {
                                        e.stopPropagation()
                                        setCollapsedDeliveries(prev => {
                                          const next = new Set(prev)
                                          if (next.has(d.id)) next.delete(d.id); else next.add(d.id)
                                          return next
                                        })
                                      }}
                                    >
                                      <span className="text-xs font-medium flex items-center gap-1.5">
                                        {isCollapsed ? <ChevronRight className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                                        发货批次 {di + 1} — 账号数: {accounts.length}
                                        <span className="text-muted-foreground">{d.delivered_at}</span>
                                        <Badge variant={warranty.expired ? 'destructive' : 'secondary'} className="text-[10px] px-1.5">
                                          {warranty.msg}
                                        </Badge>
                                        {deliveryBan && deliveryBan.banned_count > 0 && (
                                          <Badge variant="destructive" className="text-[10px] px-1.5">封禁 {deliveryBan.banned_count}</Badge>
                                        )}
                                        {deliveryBan && deliveryBan.banned_count === 0 && (
                                          <Badge variant="success" className="text-[10px] px-1.5">全部正常</Badge>
                                        )}
                                        {!deliveryBan && loadingBan && (
                                          <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
                                        )}
                                      </span>
                                      {!isCollapsed && (
                                        <div className="flex gap-1.5 flex-wrap" onClick={e => e.stopPropagation()}>
                                          <Button size="sm" variant="outline" className="h-7 text-xs"
                                            disabled={replacingDeliveryId === d.id}
                                            onClick={e => { e.stopPropagation(); handleBatchReplace(o.id, d.id) }}>
                                            <RotateCcw className="h-3 w-3 mr-1" />
                                            {replacingDeliveryId === d.id ? '换号中...' : '换号'}
                                          </Button>
                                          <Button size="sm" variant="outline" className="h-7 text-xs"
                                            onClick={e => { e.stopPropagation(); handleCopyDelivery(accounts) }}>
                                            <Copy className="h-3 w-3 mr-1" /> 复制
                                          </Button>
                                          <Button size="sm" variant="outline" className="h-7 text-xs"
                                            disabled={importingDeliveryId === d.id}
                                            onClick={e => { e.stopPropagation(); handleImportDelivery(d.id, accounts) }}>
                                            <Download className="h-3 w-3 mr-1" />
                                            {importingDeliveryId === d.id ? '导入中...' : '导入凭据'}
                                          </Button>
                                        </div>
                                      )}
                                    </div>
                                    {/* 账号列表（收起时隐藏） */}
                                    {!isCollapsed && (
                                      <div className="space-y-0.5 pt-1">
                                        {accounts.map((a: any, ai: number) => {
                                          const email = a.email || ''
                                          const rt = a.refresh_token || ''
                                          const hash = accountHashMap.get(rt)
                                          const exists = hash ? credHashSet.has(hash) : false
                                          const banInfo = deliveryBan?.results?.find(r => r.email === email)
                                          return (
                                            <div key={ai} className="flex items-center gap-2 text-xs py-0.5 px-1 rounded hover:bg-muted/50">
                                              <Badge variant={exists ? 'success' : 'secondary'} className="text-[10px] px-1.5 shrink-0">
                                                {exists ? '已导入' : '未导入'}
                                              </Badge>
                                              {banInfo !== undefined && (
                                                <span className="shrink-0">{banInfo.banned ? '🔴' : '🟢'}</span>
                                              )}
                                              <span className="font-mono truncate">{email || '(无邮箱)'}</span>
                                            </div>
                                          )
                                        })}
                                      </div>
                                    )}
                                  </div>
                                )
                              })
                            ) : (
                              <p className="text-xs text-muted-foreground">暂无发货信息</p>
                            )}
                            {/* 提货操作 */}
                            <div className="flex items-center gap-2 pt-1">
                              <Input type="number" min="1" placeholder="提货数量" className="w-28 h-7 text-xs"
                                value={deliverCount} onChange={e => setDeliverCount(e.target.value)}
                                onClick={e => e.stopPropagation()} />
                              <Button size="sm" variant="outline" className="h-7 text-xs"
                                disabled={deliveringOrderId === o.id}
                                onClick={e => { e.stopPropagation(); handleDeliver(o.id) }}>
                                <Package className="h-3 w-3 mr-1" />
                                {deliveringOrderId === o.id ? '提货中...' : '提货'}
                              </Button>
                            </div>
                          </div>
                        ) : null}
                      </div>
                      )
                    })()}
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* 自动补号 */}
        <Card className="md:col-span-2">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-base flex items-center gap-2">
                <RefreshCw className="h-4 w-4" /> 自动补号
              </CardTitle>
              <div className="flex items-center gap-2">
                <Button size="sm" variant="outline" onClick={handleAnalyze} disabled={loadingAnalyze}>
                  {loadingAnalyze ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Search className="h-4 w-4 mr-1" />}
                  分析订单
                </Button>
                <div className="flex items-center gap-1">
                  <span className="text-xs text-muted-foreground">间隔</span>
                  <Input type="number" min="1" className="w-16 h-8 text-xs" value={arInterval}
                    onChange={e => setArInterval(e.target.value)} disabled={arStatus?.running} />
                  <span className="text-xs text-muted-foreground">秒</span>
                </div>
                {arStatus?.running ? (
                  <Button size="sm" variant="destructive" onClick={handleStopAr} disabled={arLoading}>
                    <Square className="h-4 w-4 mr-1" /> 停止
                  </Button>
                ) : (
                  <Button size="sm" onClick={handleStartAr} disabled={arLoading}>
                    <Play className="h-4 w-4 mr-1" /> 开始补货
                  </Button>
                )}
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            {/* 状态栏 */}
            <div className="flex items-center gap-4 text-sm">
              <Badge variant={arStatus?.running ? 'success' : 'secondary'}>
                {arStatus?.running ? '运行中' : '已停止'}
              </Badge>
              {arStatus && (
                <>
                  <span className="text-muted-foreground">待补号: {arStatus.pending_tasks?.length ?? 0}</span>
                  {arStatus.last_check && <span className="text-muted-foreground">上次检查: {arStatus.last_check}</span>}
                </>
              )}
            </div>

            {/* 待补号列表（来自运行状态） */}
            {arStatus?.pending_tasks && arStatus.pending_tasks.length > 0 && (
              <div className="space-y-1">
                <div className="text-xs font-medium">待补号列表</div>
                <div className="rounded border bg-background p-2 space-y-1 max-h-40 overflow-y-auto">
                  {arStatus.pending_tasks.map((t: any) => (
                    <div key={`${t.order_id}-${t.delivery_id}`} className="flex items-center gap-3 text-xs py-0.5">
                      <span className="font-mono">订单#{t.order_id}</span>
                      <span className="font-mono">发货#{t.delivery_id}</span>
                      <Badge variant="destructive" className="text-[10px] px-1.5">封禁 {t.banned_count}</Badge>
                      <span className="text-muted-foreground">{t.warranty_msg}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* 分析结果（手动分析按钮） */}
            {analyzeResult && (
              <div className="text-sm space-y-2">
                <div className="text-xs font-medium">分析结果 — 待补号: {analyzeResult.pending_tasks?.length ?? 0}</div>
                {analyzeResult.summaries?.map((s: any) => (
                  <div key={s.order_id} className="rounded border bg-background p-2 text-xs space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="font-medium">#{s.order_id}</span>
                      <span className="text-muted-foreground">{s.order_no}</span>
                      <span className="text-muted-foreground">{s.product_name}</span>
                    </div>
                    {s.deliveries?.map((d: any) => (
                      <div key={d.delivery_id} className="flex items-center gap-2 pl-3">
                        {d.need_replace ? '🔴' : d.is_expired ? '⚫' : d.banned_count > 0 ? '🟡' : '🟢'}
                        <span>发货#{d.delivery_id}</span>
                        <span className="text-muted-foreground">{d.total_accounts}个账号</span>
                        {d.banned_count > 0 && <Badge variant="destructive" className="text-[10px] px-1.5">封禁 {d.banned_count}</Badge>}
                        <span className="text-muted-foreground">{d.warranty_msg}</span>
                        {d.need_replace && <Badge variant="warning" className="text-[10px] px-1.5">待补号</Badge>}
                        {d.is_expired && <Badge variant="secondary" className="text-[10px] px-1.5">已过保</Badge>}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            )}

            {/* 日志 */}
            {arStatus?.logs && arStatus.logs.length > 0 && (
              <div className="space-y-1">
                <div className="text-xs font-medium">运行日志</div>
                <div className="rounded border bg-muted/30 p-2 max-h-48 overflow-y-auto font-mono text-xs space-y-0.5">
                  {arStatus.logs.map((line: string, i: number) => (
                    <div key={i} className="text-muted-foreground">{line}</div>
                  ))}
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        {/* 自动补货 */}
        <Card className="md:col-span-2">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-base flex items-center gap-2">
                <Power className="h-4 w-4" /> 自动补货
              </CardTitle>
              <div className="flex items-center gap-2">
                <div className="flex items-center gap-1">
                  <span className="text-xs text-muted-foreground">检测间隔</span>
                  <Input type="number" min="5" className="w-16 h-8 text-xs" value={restockInterval}
                    onChange={e => setRestockInterval(e.target.value)} disabled={restockStatus?.running} />
                  <span className="text-xs text-muted-foreground">秒</span>
                </div>
                {restockStatus?.running ? (
                  <Button size="sm" variant="destructive" onClick={handleStopRestock} disabled={restockLoading}>
                    <Square className="h-4 w-4 mr-1" /> 停止
                  </Button>
                ) : (
                  <Button size="sm" onClick={handleStartRestock} disabled={restockLoading}>
                    <Power className="h-4 w-4 mr-1" /> 启动
                  </Button>
                )}
              </div>
            </div>
            <p className="text-xs text-muted-foreground">监控 pro/priority 凭据异常禁用，自动触发补货流程</p>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center gap-4 text-sm">
              <Badge variant={restockStatus?.running ? 'success' : 'secondary'}>
                {restockStatus?.running ? '监控中' : '已停止'}
              </Badge>
              {restockStatus?.disabled_creds && restockStatus.disabled_creds.length > 0 && (
                <span className="text-muted-foreground">异常禁用: {restockStatus.disabled_creds.length} 个</span>
              )}
            </div>

            {/* 在保账号 */}
            <div className="rounded border bg-background p-3 space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 text-sm">
                  <Shield className="h-4 w-4 text-muted-foreground" />
                  <span className="font-medium">在保账号</span>
                  <Badge variant="secondary">{restockStatus?.warranty_count ?? 0} 个</Badge>
                </div>
                <Button size="sm" variant="outline" className="h-7 text-xs"
                  onClick={handleRefreshWarranty} disabled={warrantyRefreshing}>
                  {warrantyRefreshing ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" /> : <Search className="h-3.5 w-3.5 mr-1" />}
                  {warrantyRefreshing ? '检查中...' : '检查所有订单'}
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">仅在保名单内的 client_id 失效时触发补货，每 30 分钟自动刷新</p>
            </div>

            {restockStatus?.disabled_creds && restockStatus.disabled_creds.length > 0 && (
              <div className="space-y-1">
                <div className="text-xs font-medium">异常禁用凭据</div>
                <div className="rounded border bg-background p-2 space-y-1 max-h-40 overflow-y-auto">
                  {restockStatus.disabled_creds.map((c: any) => (
                    <div key={c.id} className="flex items-center gap-3 text-xs py-0.5">
                      <span className="font-mono">#{c.id}</span>
                      <span className="truncate">{c.email || '(无邮箱)'}</span>
                      <Badge variant="secondary" className="text-[10px] px-1.5">{c.group}</Badge>
                      <Badge variant="destructive" className="text-[10px] px-1.5">
                        {c.reason === 'too_many_failures' ? '连续失败' : '额度耗尽'}
                      </Badge>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {restockStatus?.logs && restockStatus.logs.length > 0 && (
              <div className="space-y-1">
                <div className="text-xs font-medium">运行日志</div>
                <div className="rounded border bg-muted/30 p-2 max-h-48 overflow-y-auto font-mono text-xs space-y-0.5">
                  {restockStatus.logs.map((line: string, i: number) => (
                    <div key={i} className="text-muted-foreground">{line}</div>
                  ))}
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}