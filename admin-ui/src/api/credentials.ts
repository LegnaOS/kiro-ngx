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
export async function getCredentialBalance(id: number): Promise<BalanceResponse> {
  const { data } = await api.get<BalanceResponse>(`/credentials/${id}/balance`)
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
export async function getSystemStats(): Promise<{ cpuPercent: number; memoryMb: number }> {
  const { data } = await api.get<{ cpuPercent: number; memoryMb: number }>('/system/stats')
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
