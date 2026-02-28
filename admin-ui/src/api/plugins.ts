import axios from 'axios'
import { storage } from '@/lib/storage'

const api = axios.create({ baseURL: '/api/admin' })
api.interceptors.request.use((config) => {
  const apiKey = storage.getApiKey()
  if (apiKey) config.headers['x-api-key'] = apiKey
  return config
})

// 插件 manifest 类型
export interface PluginManifest {
  id: string
  name: string
  description: string
  version: string
  icon: string
  has_frontend: boolean
  api_prefix: string
}

// 获取已加载插件列表
export async function getPlugins(): Promise<PluginManifest[]> {
  const { data } = await api.get<{ plugins: PluginManifest[] }>('/plugins')
  return data.plugins
}

// ============ Restock 插件 API ============

function restockHeaders(token: string) {
  return { 'x-kiroshop-token': token }
}

// 配置读写
export interface RestockConfig {
  email: string
  password: string
  token: string
  ar_interval?: string
  restock_interval?: string
}

export async function getRestockConfig(): Promise<RestockConfig> {
  const { data } = await api.get<RestockConfig>('/plugins/restock/config')
  return data
}

export async function saveRestockConfig(config: Partial<RestockConfig>): Promise<void> {
  await api.put('/plugins/restock/config', config)
}

export async function restockLogin(email: string, password: string): Promise<string> {
  const { data } = await api.post<{ token: string }>('/plugins/restock/login', { email, password })
  return data.token
}

export async function getRestockInventory(token: string) {
  const { data } = await api.get('/plugins/restock/inventory', { headers: restockHeaders(token) })
  return data as Array<{
    name: string; price: number; stock: number
    max_per_user: number; is_special: number; special_remaining: number
  }>
}

export async function getRestockOrders(token: string) {
  const { data } = await api.get<{
    orders: Array<{
      id: number; order_no: string; product_id: number
      quantity: number; total_price: number; status: string; created_at: string
      warranty_hours?: number
    }>
    total: number
  }>('/plugins/restock/orders', { headers: restockHeaders(token) })
  return data
}

export async function getRestockOrderDetail(token: string, orderId: number) {
  const { data } = await api.get(`/plugins/restock/orders/${orderId}`, { headers: restockHeaders(token) })
  return data as {
    order_no: string; product_id: number; product_name: string
    quantity: number; total_price: number; status: string
    quota_type: string; created_at: string; paid_at: string
    warranty_hours?: number
    deliveries: Array<{
      id: number; delivered_at: string
      account_count: number
      account_data: Array<{
        id: number; email: string; account_json: string
        refresh_token: string; client_id: string; client_secret: string
        region: string; folder_name?: string
      }>
    }>
  }
}

export async function checkRestockBan(token: string, orderId: number) {
  const { data } = await api.post<{
    deliveries: Array<{
      delivery_id: number; success: boolean
      total: number; banned_count: number
      results: Array<{ email: string; banned: boolean }>
    }>
    message?: string
  }>(`/plugins/restock/orders/${orderId}/check-ban`, {}, { headers: restockHeaders(token) })
  return data
}

// 提货
export async function restockDeliver(token: string, orderId: number, count: number) {
  const { data } = await api.post(`/plugins/restock/orders/${orderId}/deliver`, { count }, { headers: restockHeaders(token) })
  return data
}

// 一键换号
export async function restockBatchReplace(token: string, orderId: number, deliveryId: number) {
  const { data } = await api.post(`/plugins/restock/orders/${orderId}/batch-replace`, { delivery_id: deliveryId }, { headers: restockHeaders(token) })
  return data
}

// 过保分析 + 封号检测
export async function analyzeRestockOrders(token: string) {
  const { data } = await api.get<{
    pending_tasks: Array<{ order_id: number; delivery_id: number; banned_count: number; warranty_msg: string }>
    summaries: Array<{
      order_id: number; order_no: string; product_name: string
      deliveries: Array<{
        delivery_id: number; delivered_at: string; account_count: number
        is_expired: boolean; warranty_msg: string
        banned_count: number; total_accounts: number; need_replace: boolean
      }>
    }>
  }>('/plugins/restock/orders/analyze', { headers: restockHeaders(token) })
  return data
}

// 自动补号控制
export async function startAutoReplace(interval: number = 1) {
  const { data } = await api.post<{ success: boolean; message?: string }>('/plugins/restock/auto-replace/start', { interval })
  return data
}

export async function stopAutoReplace() {
  const { data } = await api.post<{ success: boolean; message?: string }>('/plugins/restock/auto-replace/stop')
  return data
}

export async function getAutoReplaceStatus() {
  const { data } = await api.get<{
    running: boolean
    pending_tasks: Array<{ order_id: number; delivery_id: number; banned_count: number; warranty_msg: string }>
    interval: number
    last_check: string; logs: string[]
  }>('/plugins/restock/auto-replace/status')
  return data
}

// 自动补货控制
export async function startAutoRestock(interval: number = 30) {
  const { data } = await api.post<{ success: boolean; message?: string }>('/plugins/restock/auto-restock/start', { interval })
  return data
}

export async function stopAutoRestock() {
  const { data } = await api.post<{ success: boolean; message?: string }>('/plugins/restock/auto-restock/stop')
  return data
}

export async function getAutoRestockStatus() {
  const { data } = await api.get<{
    running: boolean
    disabled_creds: Array<{ id: number; email: string | null; group: string; reason: string }>
    interval: number; warranty_count: number; logs: string[]
  }>('/plugins/restock/auto-restock/status')
  return data
}

export async function refreshWarrantyList() {
  const { data } = await api.post<{ warranty_count: number }>('/plugins/restock/auto-restock/refresh-warranty')
  return data
}
