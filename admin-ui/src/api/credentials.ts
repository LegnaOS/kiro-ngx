import axios from 'axios'
import { storage } from '@/lib/storage'
import type {
  CredentialsStatusResponse,
  BalanceResponse,
  SuccessResponse,
  SetDisabledRequest,
  SetPriorityRequest,
  AddCredentialRequest,
  AddCredentialResponse,
  RequestStats,
  ModelInfo,
  RoutingConfig,
  RuntimeLogResponse,
  SystemStats,
} from '@/types/api'

// 创建 axios 实例
const api = axios.create({
  baseURL: '/api/admin',
  headers: {
    'Content-Type': 'application/json',
  },
})

// 请求拦截器添加 API Key
api.interceptors.request.use((config) => {
  const apiKey = storage.getApiKey()
  if (apiKey) {
    config.headers['x-api-key'] = apiKey
  }
  return config
})

// 获取所有凭据状态
export async function getCredentials(): Promise<CredentialsStatusResponse> {
  const { data } = await api.get<CredentialsStatusResponse>('/credentials')
  return data
}

// 设置凭据禁用状态
export async function setCredentialDisabled(
  id: number,
  disabled: boolean
): Promise<SuccessResponse> {
  const { data } = await api.post<SuccessResponse>(
    `/credentials/${id}/disabled`,
    { disabled } as SetDisabledRequest
  )
  return data
}

// 设置凭据优先级
export async function setCredentialPriority(
  id: number,
  priority: number
): Promise<SuccessResponse> {
  const { data } = await api.post<SuccessResponse>(
    `/credentials/${id}/priority`,
    { priority } as SetPriorityRequest
  )
  return data
}

// 重置失败计数
export async function resetCredentialFailure(
  id: number
): Promise<SuccessResponse> {
  const { data } = await api.post<SuccessResponse>(`/credentials/${id}/reset`)
  return data
}

// 获取凭据余额
export async function getCredentialBalance(
  id: number,
  options?: { forceRefresh?: boolean }
): Promise<BalanceResponse> {
  const { data } = await api.get<BalanceResponse>(`/credentials/${id}/balance`, {
    params: options?.forceRefresh ? { forceRefresh: 1 } : undefined,
  })
  return data
}

// 添加新凭据
export async function addCredential(
  req: AddCredentialRequest
): Promise<AddCredentialResponse> {
  const { data } = await api.post<AddCredentialResponse>('/credentials', req)
  return data
}

// 删除凭据
export async function deleteCredential(id: number): Promise<SuccessResponse> {
  const { data } = await api.delete<SuccessResponse>(`/credentials/${id}`)
  return data
}

// 批量设置凭据分组
export async function setCredentialGroups(groups: Record<number, string>): Promise<SuccessResponse> {
  const { data } = await api.put<SuccessResponse>('/credentials/groups', { groups })
  return data
}

// 重置所有凭据计数器
export async function resetAllCounters(): Promise<SuccessResponse> {
  const { data } = await api.post<SuccessResponse>('/credentials/reset-all')
  return data
}

// 读取 credentials.json 原始内容
export async function getRawCredentials(): Promise<{ content: string }> {
  const { data } = await api.get<{ content: string }>('/credentials-raw')
  return data
}

// 写入 credentials.json 原始内容
export async function saveRawCredentials(content: string): Promise<{ success: boolean; message: string }> {
  const { data } = await api.put<{ success: boolean; message: string }>('/credentials-raw', { content })
  return data
}

// 获取系统资源监控
export async function getSystemStats(): Promise<SystemStats> {
  const { data } = await api.get<SystemStats>('/system/stats')
  return data
}

// 版本信息
export interface VersionInfo {
  current: string
  latest: string
  hasUpdate: boolean
  behindCount: number
}

export async function getVersionInfo(): Promise<VersionInfo> {
  const { data } = await api.get<VersionInfo>('/version')
  return data
}

// Git 状态
export interface GitStatus {
  hasLocalChanges: boolean
  changedFiles: string[]
}

export async function getGitStatus(): Promise<GitStatus> {
  const { data } = await api.get<GitStatus>('/git/status')
  return data
}

// Git commit 列表
export interface GitCommit {
  hash: string
  short: string
  message: string
  date: string
  isCurrent: boolean
}

export async function getGitLog(): Promise<{ currentHash: string; commits: GitCommit[] }> {
  const { data } = await api.get<{ currentHash: string; commits: GitCommit[] }>('/git/log')
  return data
}

// 请求统计
export async function getRequestStats(): Promise<RequestStats> {
  const { data } = await api.get<RequestStats>('/stats')
  return data
}

// Token 用量历史
export async function getTokenUsageHistory(days: number = 7): Promise<{ history: Record<string, { input: number; output: number; models: Record<string, { input: number; output: number }> }> }> {
  const { data } = await api.get('/token-usage/history', { params: { days } })
  return data
}

// Token 小时级用量
export async function getTokenUsageHourly(): Promise<{ hourly: Record<string, { input: number; output: number }> }> {
  const { data } = await api.get('/token-usage/hourly')
  return data
}

// ============ Claude Code 配置管理 ============

export interface ClaudeSettings {
  env?: Record<string, string>
  permissions?: { defaultMode?: string }
  model?: string
  language?: string
  alwaysThinkingEnabled?: boolean
  skipDangerousModePermissionPrompt?: boolean
  effortLevel?: string
  enabledPlugins?: Record<string, boolean>
  [key: string]: unknown
}

export interface ClaudeProfile {
  filename: string
  path: string
  baseUrl: string
  model: string
  isActive: boolean
}

export interface ClaudeSession {
  sessionId: string
  project: string
  firstPrompt: string
  firstTimestamp: number
  lastTimestamp: number
  promptCount: number
}

export async function getClaudeSettings(): Promise<{ settings: ClaudeSettings; path: string; exists: boolean }> {
  const { data } = await api.get('/claude/settings')
  return data
}

export async function saveClaudeSettings(settings: ClaudeSettings): Promise<SuccessResponse> {
  const { data } = await api.put<SuccessResponse>('/claude/settings', { settings })
  return data
}

export async function getClaudeProfiles(): Promise<{ profiles: ClaudeProfile[] }> {
  const { data } = await api.get('/claude/profiles')
  return data
}

export async function switchClaudeProfile(filename: string): Promise<SuccessResponse> {
  const { data } = await api.post<SuccessResponse>('/claude/profiles/switch', { filename })
  return data
}

export async function getClaudeSessions(params?: { limit?: number; project?: string }): Promise<{ sessions: ClaudeSession[] }> {
  const { data } = await api.get('/claude/sessions', { params })
  return data
}

// 模型列表
export async function getModelList(): Promise<ModelInfo[]> {
  const { data } = await api.get<{ models: ModelInfo[] }>('/models')
  return data.models
}

// 路由配置
export async function getRoutingConfig(): Promise<RoutingConfig> {
  const { data } = await api.get<RoutingConfig>('/routing')
  return data
}

export async function setRoutingConfig(config: RoutingConfig): Promise<SuccessResponse> {
  const { data } = await api.put<SuccessResponse>('/routing', config)
  return data
}

// 消息日志开关
export async function getLogStatus(): Promise<{ enabled: boolean }> {
  const { data } = await api.get<{ enabled: boolean }>('/log')
  return data
}

export async function setLogStatus(enabled: boolean): Promise<SuccessResponse> {
  const { data } = await api.put<SuccessResponse>('/log', { enabled })
  return data
}

export async function getRuntimeLogs(params?: {
  cursor?: number
  limit?: number
  level?: string
  q?: string
}): Promise<RuntimeLogResponse> {
  const { data } = await api.get<RuntimeLogResponse>('/logs/runtime', { params })
  return data
}

// 重启服务（发送请求后轮询等待服务恢复）
export async function restartServer(): Promise<{ success: boolean; message: string }> {
  try {
    await api.post('/restart')
  } catch {
    // 重启导致连接断开是正常的
  }
  const maxAttempts = 30
  const interval = 1000
  for (let i = 0; i < maxAttempts; i++) {
    await new Promise(r => setTimeout(r, interval))
    try {
      await api.get('/credentials')
      return { success: true, message: '服务已重启成功' }
    } catch {
      // 服务尚未恢复
    }
  }
  return { success: false, message: '服务重启超时，请手动检查' }
}

// 获取更新进度日志
export async function getUpdateStatus(): Promise<{ log: string[] }> {
  const { data } = await api.get<{ log: string[] }>('/update/status')
  return data
}

// 拉取更新并重启（git pull + build + restart），支持进度回调和指定 commit
export async function updateAndRestart(
  onProgress?: (step: string) => void,
  targetCommit?: string,
): Promise<{ success: boolean; message: string }> {
  try {
    await api.post('/update', targetCommit ? { targetCommit } : {})
  } catch {
    // 更新重启导致连接断开是正常的
  }

  let lastLogLen = 0
  const maxAttempts = 120
  const interval = 2000

  for (let i = 0; i < maxAttempts; i++) {
    await new Promise(r => setTimeout(r, interval))

    // 轮询进度日志
    try {
      const { log } = await getUpdateStatus()
      if (log.length > lastLogLen) {
        for (let j = lastLogLen; j < log.length; j++) {
          onProgress?.(log[j])
        }
        lastLogLen = log.length
      }
      // 如果日志包含失败标记，提前返回
      const last = log[log.length - 1] ?? ''
      if (last.includes('失败') && last.includes('中止')) {
        return { success: false, message: log.join('\n') }
      }
    } catch {
      // 服务可能已重启，尝试检测恢复
      try {
        await api.get('/credentials')
        return { success: true, message: '更新并重启成功' }
      } catch {
        // 服务尚未恢复
      }
    }
  }

  // 超时，返回最后的日志作为错误信息
  try {
    const { log } = await getUpdateStatus()
    return { success: false, message: `更新超时\n${log.join('\n')}` }
  } catch {
    return { success: false, message: '更新重启超时，请手动检查' }
  }
}

// ============ 多 API Key 管理 ============

export interface ApiKeyEntry {
  key: string
  maskedKey: string
  name: string
  group: string
  rate: number | null
  monthlyQuota: number | null
  effectiveRate: number
  effectiveQuota: number
  billedTokens: number
  billedMonth: string
  totalRawTokens: number
  requestCount: number
  enabled: boolean
  createdAt: string
  isAdmin?: boolean
}

export interface ApiKeyGroup {
  rate: number
  monthlyQuota: number
}

export async function getApiKeys(): Promise<{ keys: ApiKeyEntry[]; groups: Record<string, ApiKeyGroup> }> {
  const { data } = await api.get('/keys')
  return data
}

export async function addApiKey(req: { name: string; group: string; rate?: number | null; monthlyQuota?: number | null }): Promise<{ success: boolean; key: ApiKeyEntry }> {
  const { data } = await api.post('/keys', req)
  return data
}

export async function updateApiKey(key: string, fields: Partial<{ name: string; group: string; rate: number | null; monthlyQuota: number | null; enabled: boolean }>): Promise<{ success: boolean; key: ApiKeyEntry }> {
  const { data } = await api.put(`/keys/${encodeURIComponent(key)}`, fields)
  return data
}

export async function deleteApiKey(key: string): Promise<SuccessResponse> {
  const { data } = await api.delete<SuccessResponse>(`/keys/${encodeURIComponent(key)}`)
  return data
}

export async function regenerateApiKey(key: string): Promise<{ success: boolean; key: ApiKeyEntry }> {
  const { data } = await api.post(`/keys/${encodeURIComponent(key)}/regenerate`)
  return data
}

export async function resetApiKeyUsage(key: string): Promise<SuccessResponse> {
  const { data } = await api.post<SuccessResponse>(`/keys/${encodeURIComponent(key)}/reset`)
  return data
}

export async function setApiKeyGroup(name: string, group: { rate: number; monthlyQuota: number }): Promise<SuccessResponse> {
  const { data } = await api.put<SuccessResponse>(`/keys/groups/${encodeURIComponent(name)}`, group)
  return data
}

export async function deleteApiKeyGroup(name: string): Promise<SuccessResponse> {
  const { data } = await api.delete<SuccessResponse>(`/keys/groups/${encodeURIComponent(name)}`)
  return data
}

// Key 用量统计（不含 key 字符串，供首页展示）
export interface KeyUsageStat {
  name: string
  group: string
  modelCounts: Record<string, number>
  modelTokens: Record<string, { input: number; output: number }>
  dailyUsage: Record<string, { input: number; output: number }>
  hourlyUsage: Record<string, { input: number; output: number }>
  billedTokens: number
  totalRawTokens: number
  requestCount: number
}

export async function getKeyUsageStats(): Promise<{ keys: KeyUsageStat[]; groups: Record<string, ApiKeyGroup> }> {
  const { data } = await api.get('/key-usage-stats')
  return data
}
