import axios from 'axios'
import { storage } from '@/lib/storage'

const api = axios.create({ baseURL: '/api/admin' })
api.interceptors.request.use((config) => {
  const apiKey = storage.getApiKey()
  if (apiKey) config.headers['x-api-key'] = apiKey
  return config
})

export interface PluginManifest {
  id: string
  name: string
  description: string
  version: string
  icon: string
  has_frontend: boolean
  api_prefix: string
  public_mount?: string
}

export async function getPlugins(): Promise<PluginManifest[]> {
  const { data } = await api.get<{ plugins: PluginManifest[] }>('/plugins')
  return data.plugins
}

export type RemoteApiName =
  | 'availableCredentials'
  | 'batchImport'
  | 'restart'
  | 'refreshQuota'
  | 'totalRemainingQuota'
  | 'todayTokenTotal'
  | 'totalCalls'

export interface RemoteApiConfig {
  enabledApis: Record<RemoteApiName, boolean>
}

export async function getRemoteApiConfig(): Promise<RemoteApiConfig> {
  const { data } = await api.get<RemoteApiConfig>('/plugins/remote-api/config')
  return data
}

export async function updateRemoteApiConfig(enabledApis: Partial<Record<RemoteApiName, boolean>>): Promise<RemoteApiConfig> {
  const { data } = await api.put<RemoteApiConfig>('/plugins/remote-api/config', { enabledApis })
  return data
}

