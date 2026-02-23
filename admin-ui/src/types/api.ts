// 凭据状态响应
export interface CredentialsStatusResponse {
  total: number
  available: number
  currentId: number
  credentials: CredentialStatusItem[]
}

// 单个凭据状态
export interface CredentialStatusItem {
  id: number
  priority: number
  disabled: boolean
  failureCount: number
  isCurrent: boolean
  expiresAt: string | null
  authMethod: string | null
  hasProfileArn: boolean
  email?: string
  refreshTokenHash?: string
  successCount: number
  sessionCount: number
  lastUsedAt: string | null
  hasProxy: boolean
  proxyUrl?: string
  subscriptionTitle: string | null
  group: 'free' | 'pro' | 'priority' | null
}

// 余额响应
export interface BalanceResponse {
  id: number
  subscriptionTitle: string | null
  currentUsage: number
  usageLimit: number
  remaining: number
  usagePercentage: number
  nextResetAt: number | null
}

// 成功响应
export interface SuccessResponse {
  success: boolean
  message: string
}

// 错误响应
export interface AdminErrorResponse {
  error: {
    type: string
    message: string
  }
}

// 请求类型
export interface SetDisabledRequest {
  disabled: boolean
}

export interface SetPriorityRequest {
  priority: number
}

// 添加凭据请求
export interface AddCredentialRequest {
  refreshToken: string
  authMethod?: 'social' | 'idc'
  clientId?: string
  clientSecret?: string
  priority?: number
  authRegion?: string
  apiRegion?: string
  machineId?: string
  proxyUrl?: string
  proxyUsername?: string
  proxyPassword?: string
}

// 添加凭据响应
export interface AddCredentialResponse {
  success: boolean
  message: string
  credentialId: number
  email?: string
}

// Token 用量
export interface TokenPair {
  input: number
  output: number
}

export interface TokenUsage {
  today: TokenPair
  yesterday: TokenPair
  models: Record<string, { today: TokenPair; yesterday: TokenPair }>
}

// 统计数据
export interface RequestStats {
  totalRequests: number
  sessionRequests: number
  rpm: number
  peakRpm: number
  modelCounts: Record<string, number>
  modelCredCounts: Record<string, Record<string, number>>  // {model: {credId: count}}
  credentialCount: number
  availableCount: number
  tokenUsage: TokenUsage
}

// 模型信息
export interface ModelInfo {
  id: string
  displayName: string
}

// 路由配置
export interface RoutingConfig {
  freeModels: string[]
}
