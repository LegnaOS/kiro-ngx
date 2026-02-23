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

// 获取负载均衡模式
export async function getLoadBalancingMode(): Promise<{ mode: 'priority' | 'balanced' }> {
  const { data } = await api.get<{ mode: 'priority' | 'balanced' }>('/config/load-balancing')
  return data
}

// 设置负载均衡模式
export async function setLoadBalancingMode(mode: 'priority' | 'balanced'): Promise<{ mode: 'priority' | 'balanced' }> {
  const { data } = await api.put<{ mode: 'priority' | 'balanced' }>('/config/load-balancing', { mode })
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

// 拉取更新并重启（git pull + build + restart）
export async function updateAndRestart(): Promise<{ success: boolean; message: string }> {
  try {
    await api.post('/update')
  } catch {
    // 更新重启导致连接断开是正常的
  }
  // 更新+构建+重启需要更长时间
  const maxAttempts = 120
  const interval = 2000
  for (let i = 0; i < maxAttempts; i++) {
    await new Promise(r => setTimeout(r, interval))
    try {
      await api.get('/credentials')
      return { success: true, message: '更新并重启成功' }
    } catch {
      // 服务尚未恢复
    }
  }
  return { success: false, message: '更新重启超时，请手动检查' }
}
