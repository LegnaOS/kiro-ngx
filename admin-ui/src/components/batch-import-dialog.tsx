import { useState, useEffect } from 'react'
import { toast } from 'sonner'
import { CheckCircle2, XCircle, AlertCircle, Loader2, Plus, X } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useCredentials, useAddCredential, useDeleteCredential } from '@/hooks/use-credentials'
import { getCredentialBalance, setCredentialDisabled } from '@/api/credentials'
import { extractErrorMessage } from '@/lib/utils'

interface BatchImportDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

interface CredentialInput {
  refreshToken: string
  clientId?: string
  clientSecret?: string
  region?: string
  authRegion?: string
  apiRegion?: string
  priority?: number
  machineId?: string
}

interface VerificationResult {
  index: number
  status: 'pending' | 'checking' | 'verifying' | 'verified' | 'duplicate' | 'failed'
  error?: string
  usage?: string
  email?: string
  credentialId?: number
  rollbackStatus?: 'success' | 'failed' | 'skipped'
  rollbackError?: string
}

async function sha256Hex(value: string): Promise<string> {
  const encoded = new TextEncoder().encode(value)
  // crypto.subtle 仅在 HTTPS 或 localhost 下可用，HTTP 环境回退到简单哈希
  if (globalThis.crypto?.subtle) {
    const digest = await crypto.subtle.digest('SHA-256', encoded)
    return Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2, '0')).join('')
  }
  // 回退：FNV-1a 风格的简单哈希（仅用于重复检测，非安全用途）
  let h = 0x811c9dc5
  for (const b of encoded) {
    h ^= b
    h = Math.imul(h, 0x01000193)
  }
  return (h >>> 0).toString(16).padStart(8, '0') + value.length.toString(16).padStart(8, '0')
}

const REGIONS_STORAGE_KEY = 'kiro-import-regions'
const DEFAULT_REGIONS = ['eu-north-1', 'us-east-1']
function loadRegions(): string[] {
  try { const s = localStorage.getItem(REGIONS_STORAGE_KEY); if (s) return JSON.parse(s) } catch {}
  return [...DEFAULT_REGIONS]
}

export function BatchImportDialog({ open, onOpenChange }: BatchImportDialogProps) {
  const [jsonInput, setJsonInput] = useState('')
  const [importing, setImporting] = useState(false)
  const [skipVerify, setSkipVerify] = useState(true)
  const [progress, setProgress] = useState({ current: 0, total: 0 })
  const [currentProcessing, setCurrentProcessing] = useState<string>('')
  const [results, setResults] = useState<VerificationResult[]>([])
  const [regions, setRegions] = useState<string[]>(loadRegions)
  const [newRegion, setNewRegion] = useState('')

  const { data: existingCredentials } = useCredentials()
  const { mutateAsync: addCredential } = useAddCredential()
  const { mutateAsync: deleteCredential } = useDeleteCredential()

  // 持久化 region 列表
  useEffect(() => { localStorage.setItem(REGIONS_STORAGE_KEY, JSON.stringify(regions)) }, [regions])

  const rollbackCredential = async (id: number): Promise<{ success: boolean; error?: string }> => {
    try {
      await setCredentialDisabled(id, true)
    } catch (error) {
      return {
        success: false,
        error: `禁用失败: ${extractErrorMessage(error)}`,
      }
    }

    try {
      await deleteCredential(id)
      return { success: true }
    } catch (error) {
      return {
        success: false,
        error: `删除失败: ${extractErrorMessage(error)}`,
      }
    }
  }

  const resetForm = () => {
    setJsonInput('')
    setProgress({ current: 0, total: 0 })
    setCurrentProcessing('')
    setResults([])
  }

  const handleBatchImport = async () => {
    try {
      // 1. 解析 JSON
      const parsed = JSON.parse(jsonInput)
      let credentials: CredentialInput[] = Array.isArray(parsed) ? parsed : [parsed]

      if (credentials.length === 0) {
        toast.error('没有可导入的凭据')
        return
      }

      setImporting(true)
      setProgress({ current: 0, total: credentials.length })

      // 2. 初始化结果
      const initialResults: VerificationResult[] = credentials.map((_, i) => ({
        index: i + 1,
        status: 'pending'
      }))
      setResults(initialResults)

      // 3. 检测重复
      const existingTokenHashes = new Set(
        existingCredentials?.credentials
          .map(c => c.refreshTokenHash)
          .filter((hash): hash is string => Boolean(hash)) || []
      )

      let successCount = 0
      let duplicateCount = 0
      let failCount = 0
      let rollbackSuccessCount = 0
      let rollbackFailedCount = 0
      let rollbackSkippedCount = 0

      // 4. 导入并验活
      for (let i = 0; i < credentials.length; i++) {
        const cred = credentials[i]
        const token = cred.refreshToken.trim()
        const tokenHash = await sha256Hex(token)

        // 更新状态为检查中
        setCurrentProcessing(`正在处理凭据 ${i + 1}/${credentials.length}`)
        setResults(prev => {
          const newResults = [...prev]
          newResults[i] = { ...newResults[i], status: 'checking' }
          return newResults
        })

        // 检查重复
        if (existingTokenHashes.has(tokenHash)) {
          duplicateCount++
          const existingCred = existingCredentials?.credentials.find(c => c.refreshTokenHash === tokenHash)
          setResults(prev => {
            const newResults = [...prev]
            newResults[i] = {
              ...newResults[i],
              status: 'duplicate',
              error: '该凭据已存在',
              email: existingCred?.email || undefined
            }
            return newResults
          })
          setProgress({ current: i + 1, total: credentials.length })
          continue
        }

        // 更新状态为验活中
        setResults(prev => {
          const newResults = [...prev]
          newResults[i] = { ...newResults[i], status: 'verifying' }
          return newResults
        })

        let addedCredId: number | null = null

        try {
          // 添加凭据
          const clientId = cred.clientId?.trim() || undefined
          const clientSecret = cred.clientSecret?.trim() || undefined
          const authMethod = clientId && clientSecret ? 'idc' : 'social'

          // idc 模式下必须同时提供 clientId 和 clientSecret
          if (authMethod === 'social' && (clientId || clientSecret)) {
            throw new Error('idc 模式需要同时提供 clientId 和 clientSecret')
          }

          const addedCred = await (async () => {
            const specifiedRegion = cred.authRegion?.trim() || cred.region?.trim()
            // 指定的区域优先，但失败后继续尝试列表中其余区域
            const regionsToTry = specifiedRegion
              ? [specifiedRegion, ...regions.filter(r => r !== specifiedRegion)]
              : regions
            let lastErr: any = null
            for (const region of regionsToTry) {
              try {
                return await addCredential({
                  refreshToken: token, authMethod,
                  authRegion: region,
                  apiRegion: cred.apiRegion?.trim() || undefined,
                  clientId, clientSecret,
                  priority: cred.priority || 0,
                  machineId: cred.machineId?.trim() || undefined,
                })
              } catch (e) { lastErr = e }
            }
            throw lastErr || new Error('所有区域均失败')
          })()

          addedCredId = addedCred.credentialId

          if (skipVerify) {
            // 跳过验活，直接标记成功
            successCount++
            existingTokenHashes.add(tokenHash)
            setCurrentProcessing(addedCred.email ? `导入成功: ${addedCred.email}` : `导入成功: 凭据 ${i + 1}`)
            setResults(prev => {
              const newResults = [...prev]
              newResults[i] = {
                ...newResults[i],
                status: 'verified',
                email: addedCred.email || undefined,
                credentialId: addedCred.credentialId
              }
              return newResults
            })
          } else {
            // 延迟 1 秒
            await new Promise(resolve => setTimeout(resolve, 1000))

            // 验活
            const balance = await getCredentialBalance(addedCred.credentialId)

            // 验活成功
            successCount++
            existingTokenHashes.add(tokenHash)
            setCurrentProcessing(addedCred.email ? `验活成功: ${addedCred.email}` : `验活成功: 凭据 ${i + 1}`)
            setResults(prev => {
              const newResults = [...prev]
              newResults[i] = {
                ...newResults[i],
                status: 'verified',
                usage: `${balance.currentUsage}/${balance.usageLimit}`,
                email: addedCred.email || undefined,
                credentialId: addedCred.credentialId
              }
              return newResults
            })
          }
        } catch (error) {
          // 验活失败，尝试回滚（先禁用再删除）
          let rollbackStatus: VerificationResult['rollbackStatus'] = 'skipped'
          let rollbackError: string | undefined

          if (addedCredId) {
            const rollbackResult = await rollbackCredential(addedCredId)
            if (rollbackResult.success) {
              rollbackStatus = 'success'
              rollbackSuccessCount++
            } else {
              rollbackStatus = 'failed'
              rollbackFailedCount++
              rollbackError = rollbackResult.error
            }
          } else {
            rollbackSkippedCount++
          }

          failCount++
          setResults(prev => {
            const newResults = [...prev]
            newResults[i] = {
              ...newResults[i],
              status: 'failed',
              error: extractErrorMessage(error),
              email: undefined,
              rollbackStatus,
              rollbackError,
            }
            return newResults
          })
        }

        setProgress({ current: i + 1, total: credentials.length })
      }

      // 显示结果
      if (failCount === 0 && duplicateCount === 0) {
        toast.success(`成功导入并验活 ${successCount} 个凭据`)
      } else {
        const failureSummary = failCount > 0
          ? `，失败 ${failCount} 个（已排除 ${rollbackSuccessCount}，未排除 ${rollbackFailedCount}，无需排除 ${rollbackSkippedCount}）`
          : ''
        toast.info(`验活完成：成功 ${successCount} 个，重复 ${duplicateCount} 个${failureSummary}`)

        if (rollbackFailedCount > 0) {
          toast.warning(`有 ${rollbackFailedCount} 个失败凭据回滚未完成，请手动禁用并删除`)
        }
      }
    } catch (error) {
      toast.error('JSON 格式错误: ' + extractErrorMessage(error))
    } finally {
      setImporting(false)
    }
  }

  const getStatusIcon = (status: VerificationResult['status']) => {
    switch (status) {
      case 'pending':
        return <div className="w-5 h-5 rounded-full border-2 border-gray-300" />
      case 'checking':
      case 'verifying':
        return <Loader2 className="w-5 h-5 animate-spin text-blue-500" />
      case 'verified':
        return <CheckCircle2 className="w-5 h-5 text-green-500" />
      case 'duplicate':
        return <AlertCircle className="w-5 h-5 text-yellow-500" />
      case 'failed':
        return <XCircle className="w-5 h-5 text-red-500" />
    }
  }

  const getStatusText = (result: VerificationResult) => {
    switch (result.status) {
      case 'pending':
        return '等待中'
      case 'checking':
        return '检查重复...'
      case 'verifying':
        return '验活中...'
      case 'verified':
        return '验活成功'
      case 'duplicate':
        return '重复凭据'
      case 'failed':
        if (result.rollbackStatus === 'success') return '验活失败（已排除）'
        if (result.rollbackStatus === 'failed') return '验活失败（未排除）'
        return '验活失败（未创建）'
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(newOpen) => {
        // 关闭时清空表单（但不在导入过程中清空）
        if (!newOpen && !importing) {
          resetForm()
        }
        onOpenChange(newOpen)
      }}
    >
      <DialogContent className="sm:max-w-2xl max-h-[80vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>批量导入凭据{skipVerify ? '' : '（自动验活）'}</DialogTitle>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto space-y-4 py-4">
          {/* Region 列表 */}
          <div className="space-y-2">
            <label className="text-sm font-medium">
              Region 列表 <span className="text-xs text-muted-foreground font-normal">导入失败时按顺序尝试其余区域</span>
            </label>
            <div className="flex flex-wrap items-center gap-2">
              {regions.map(r => (
                <div key={r} className="flex items-center gap-1 rounded border px-2 py-1 text-xs">
                  <span className="font-mono">{r}</span>
                  {!DEFAULT_REGIONS.includes(r) && (
                    <button className="text-muted-foreground hover:text-destructive ml-1"
                      onClick={() => setRegions(prev => prev.filter(x => x !== r))}>
                      <X className="h-3 w-3" />
                    </button>
                  )}
                </div>
              ))}
              <div className="flex items-center gap-1">
                <Input type="text" placeholder="新 region" className="w-32 h-7 text-xs"
                  value={newRegion} onChange={e => setNewRegion(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && newRegion.trim() && !regions.includes(newRegion.trim())) {
                      setRegions(prev => [...prev, newRegion.trim()])
                      setNewRegion('')
                    }
                  }} />
                <Button size="sm" variant="outline" className="h-7 text-xs"
                  disabled={!newRegion.trim() || regions.includes(newRegion.trim())}
                  onClick={() => { setRegions(prev => [...prev, newRegion.trim()]); setNewRegion('') }}>
                  <Plus className="h-3 w-3" />
                </Button>
              </div>
            </div>
          </div>

          <div className="space-y-2">
            <label className="text-sm font-medium">
              JSON 格式凭据
            </label>
            <textarea
              placeholder={'粘贴 JSON 格式的凭据（支持单个对象或数组）\n例如: [{"refreshToken":"...","clientId":"...","clientSecret":"...","authRegion":"us-east-1"}]\n支持 region 字段自动映射为 authRegion'}
              value={jsonInput}
              onChange={(e) => setJsonInput(e.target.value)}
              disabled={importing}
              className="flex min-h-[200px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 font-mono"
            />
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={skipVerify}
                onChange={(e) => setSkipVerify(e.target.checked)}
                disabled={importing}
                className="rounded"
              />
              跳过验活（直接导入，不检查凭据有效性）
            </label>
          </div>

          {(importing || results.length > 0) && (
            <>
              {/* 进度条 */}
              <div className="space-y-2">
                <div className="flex justify-between text-sm">
                  <span>{importing ? '验活进度' : '验活完成'}</span>
                  <span>{progress.current} / {progress.total}</span>
                </div>
                <div className="w-full bg-secondary rounded-full h-2">
                  <div
                    className="bg-primary h-2 rounded-full transition-all"
                    style={{ width: `${(progress.current / progress.total) * 100}%` }}
                  />
                </div>
                {importing && currentProcessing && (
                  <div className="text-xs text-muted-foreground">
                    {currentProcessing}
                  </div>
                )}
              </div>

              {/* 统计 */}
              <div className="flex gap-4 text-sm">
                <span className="text-green-600 dark:text-green-400">
                  ✓ 成功: {results.filter(r => r.status === 'verified').length}
                </span>
                <span className="text-yellow-600 dark:text-yellow-400">
                  ⚠ 重复: {results.filter(r => r.status === 'duplicate').length}
                </span>
                <span className="text-red-600 dark:text-red-400">
                  ✗ 失败: {results.filter(r => r.status === 'failed').length}
                </span>
              </div>

              {/* 结果列表 */}
              <div className="border rounded-md divide-y max-h-[300px] overflow-y-auto">
                {results.map((result) => (
                  <div key={result.index} className="p-3">
                    <div className="flex items-start gap-3">
                      {getStatusIcon(result.status)}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium">
                            {result.email || `凭据 #${result.index}`}
                          </span>
                          <span className="text-xs text-muted-foreground">
                            {getStatusText(result)}
                          </span>
                        </div>
                        {result.usage && (
                          <div className="text-xs text-muted-foreground mt-1">
                            用量: {result.usage}
                          </div>
                        )}
                        {result.error && (
                          <div className="text-xs text-red-600 dark:text-red-400 mt-1">
                            {result.error}
                          </div>
                        )}
                        {result.rollbackError && (
                          <div className="text-xs text-red-600 dark:text-red-400 mt-1">
                            回滚失败: {result.rollbackError}
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              onOpenChange(false)
              resetForm()
            }}
            disabled={importing}
          >
            {importing ? '验活中...' : results.length > 0 ? '关闭' : '取消'}
          </Button>
          {results.length === 0 && (
            <Button
              type="button"
              onClick={handleBatchImport}
              disabled={importing || !jsonInput.trim()}
            >
              {skipVerify ? '开始导入' : '开始导入并验活'}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
