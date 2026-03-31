import { useState, useEffect } from 'react'
import { Save, RefreshCw, ArrowRightLeft, Terminal, Clock, FolderOpen, Copy, Check } from 'lucide-react'
import { toast } from 'sonner'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Switch } from '@/components/ui/switch'
import {
  getClaudeSettings, saveClaudeSettings, getClaudeProfiles,
  switchClaudeProfile, getClaudeSessions,
  type ClaudeSettings, type ClaudeProfile, type ClaudeSession,
} from '@/api/credentials'

export function ClaudeTab() {
  const [settings, setSettings] = useState<ClaudeSettings | null>(null)
  const [settingsPath, setSettingsPath] = useState('')
  const [profiles, setProfiles] = useState<ClaudeProfile[]>([])
  const [sessions, setSessions] = useState<ClaudeSession[]>([])
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [copiedId, setCopiedId] = useState<string | null>(null)

  const reload = () => {
    getClaudeSettings().then(r => { setSettings(r.settings); setSettingsPath(r.path); setDirty(false) }).catch(() => toast.error('读取 Claude 配置失败'))
    getClaudeProfiles().then(r => setProfiles(r.profiles)).catch(() => {})
    getClaudeSessions({ limit: 30 }).then(r => setSessions(r.sessions)).catch(() => {})
  }

  useEffect(() => { reload() }, [])

  const handleSave = async () => {
    if (!settings) return
    setSaving(true)
    try {
      await saveClaudeSettings(settings)
      toast.success('配置已保存，重启 Claude Code 后生效')
      setDirty(false)
    } catch { toast.error('保存失败') }
    setSaving(false)
  }

  const handleSwitch = async (filename: string) => {
    try {
      await switchClaudeProfile(filename)
      toast.success(`已切换到 ${filename}，重启 Claude Code 后生效`)
      reload()
    } catch { toast.error('切换失败') }
  }

  const updateEnv = (key: string, value: string) => {
    if (!settings) return
    const env = { ...settings.env, [key]: value }
    setSettings({ ...settings, env })
    setDirty(true)
  }

  const updateField = (key: string, value: unknown) => {
    if (!settings) return
    setSettings({ ...settings, [key]: value })
    setDirty(true)
  }

  const copyToClipboard = (text: string, id: string) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopiedId(id)
      setTimeout(() => setCopiedId(null), 1500)
    })
  }

  const env = settings?.env ?? {}

  return (
    <div className="space-y-6">
      {/* 配置文件切换 */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <ArrowRightLeft className="h-4 w-4" /> 配置文件
          </CardTitle>
          <Button variant="ghost" size="sm" onClick={reload}><RefreshCw className="h-4 w-4" /></Button>
        </CardHeader>
        <CardContent>
          <div className="text-xs text-muted-foreground mb-3">路径: {settingsPath}</div>
          <div className="space-y-2">
            {profiles.map(p => (
              <div key={p.filename} className="flex items-center gap-3 p-2 rounded-md border text-sm">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs">{p.filename}</span>
                    {p.isActive && <Badge variant="outline" className="text-[10px] border-green-400 text-green-600">当前</Badge>}
                  </div>
                  <div className="text-xs text-muted-foreground truncate mt-0.5">
                    {p.baseUrl || '(未设置端点)'} · {p.model || '(未设置模型)'}
                  </div>
                </div>
                {!p.isActive && (
                  <Button variant="outline" size="sm" className="h-7 text-xs" onClick={() => handleSwitch(p.filename)}>
                    切换
                  </Button>
                )}
              </div>
            ))}
            {profiles.length === 0 && <div className="text-sm text-muted-foreground">未找到配置文件</div>}
          </div>
        </CardContent>
      </Card>

      {/* 核心配置编辑 */}
      {settings && (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-3">
            <CardTitle className="text-base flex items-center gap-2">
              <Terminal className="h-4 w-4" /> 当前配置
            </CardTitle>
            <Button size="sm" className="h-8 gap-1" onClick={handleSave} disabled={saving || !dirty}>
              <Save className="h-3.5 w-3.5" /> {saving ? '保存中...' : '保存'}
            </Button>
          </CardHeader>
          <CardContent className="space-y-4">
            {/* 端点 */}
            <SettingRow label="API 端点" hint="ANTHROPIC_BASE_URL">
              <Input
                className="h-8 text-sm font-mono"
                value={env.ANTHROPIC_BASE_URL ?? ''}
                onChange={e => updateEnv('ANTHROPIC_BASE_URL', e.target.value)}
                placeholder="https://api.anthropic.com"
              />
            </SettingRow>

            {/* API Key */}
            <SettingRow label="API Key" hint="ANTHROPIC_AUTH_TOKEN">
              <Input
                className="h-8 text-sm font-mono"
                type="password"
                value={env.ANTHROPIC_AUTH_TOKEN ?? ''}
                onChange={e => updateEnv('ANTHROPIC_AUTH_TOKEN', e.target.value)}
                placeholder="sk-..."
              />
            </SettingRow>

            {/* 默认模型 */}
            <SettingRow label="默认模型层级" hint="model">
              <select
                className="h-8 rounded-md border border-input bg-background px-2 text-sm"
                value={settings.model ?? 'sonnet'}
                onChange={e => updateField('model', e.target.value)}
              >
                <option value="opus">Opus</option>
                <option value="sonnet">Sonnet</option>
                <option value="haiku">Haiku</option>
              </select>
            </SettingRow>

            {/* 模型映射 */}
            <SettingRow label="Opus 模型" hint="ANTHROPIC_DEFAULT_OPUS_MODEL">
              <Input className="h-8 text-sm font-mono" value={env.ANTHROPIC_DEFAULT_OPUS_MODEL ?? ''} onChange={e => updateEnv('ANTHROPIC_DEFAULT_OPUS_MODEL', e.target.value)} placeholder="claude-opus-4-6" />
            </SettingRow>
            <SettingRow label="Sonnet 模型" hint="ANTHROPIC_DEFAULT_SONNET_MODEL">
              <Input className="h-8 text-sm font-mono" value={env.ANTHROPIC_DEFAULT_SONNET_MODEL ?? ''} onChange={e => updateEnv('ANTHROPIC_DEFAULT_SONNET_MODEL', e.target.value)} placeholder="claude-sonnet-4-6" />
            </SettingRow>
            <SettingRow label="Haiku 模型" hint="ANTHROPIC_DEFAULT_HAIKU_MODEL">
              <Input className="h-8 text-sm font-mono" value={env.ANTHROPIC_DEFAULT_HAIKU_MODEL ?? ''} onChange={e => updateEnv('ANTHROPIC_DEFAULT_HAIKU_MODEL', e.target.value)} placeholder="claude-haiku-4-5" />
            </SettingRow>

            {/* 超时 */}
            <SettingRow label="API 超时 (ms)" hint="API_TIMEOUT_MS">
              <Input className="h-8 text-sm font-mono w-32" value={env.API_TIMEOUT_MS ?? ''} onChange={e => updateEnv('API_TIMEOUT_MS', e.target.value)} placeholder="600000" />
            </SettingRow>

            {/* 最大输出 tokens */}
            <SettingRow label="最大输出 Tokens" hint="CLAUDE_CODE_MAX_OUTPUT_TOKENS">
              <Input className="h-8 text-sm font-mono w-32" value={env.CLAUDE_CODE_MAX_OUTPUT_TOKENS ?? ''} onChange={e => updateEnv('CLAUDE_CODE_MAX_OUTPUT_TOKENS', e.target.value)} placeholder="16000" />
            </SettingRow>

            {/* 开关项 */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 pt-2">
              <ToggleRow label="始终启用思考" checked={settings.alwaysThinkingEnabled ?? false} onChange={v => updateField('alwaysThinkingEnabled', v)} />
              <ToggleRow label="跳过危险模式确认" checked={settings.skipDangerousModePermissionPrompt ?? false} onChange={v => updateField('skipDangerousModePermissionPrompt', v)} />
              <ToggleRow label="Agent Teams" checked={env.CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS === '1'} onChange={v => updateEnv('CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS', v ? '1' : '0')} />
            </div>

            {/* 权限模式 */}
            <SettingRow label="权限模式" hint="permissions.defaultMode">
              <select
                className="h-8 rounded-md border border-input bg-background px-2 text-sm"
                value={settings.permissions?.defaultMode ?? 'default'}
                onChange={e => updateField('permissions', { ...settings.permissions, defaultMode: e.target.value })}
              >
                <option value="default">默认（逐次确认）</option>
                <option value="bypassPermissions">绕过权限</option>
              </select>
            </SettingRow>

            {/* Effort Level */}
            <SettingRow label="推理强度" hint="effortLevel">
              <select
                className="h-8 rounded-md border border-input bg-background px-2 text-sm"
                value={settings.effortLevel ?? ''}
                onChange={e => updateField('effortLevel', e.target.value || undefined)}
              >
                <option value="">默认</option>
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
              </select>
            </SettingRow>
          </CardContent>
        </Card>
      )}

      {/* 会话列表 */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <Clock className="h-4 w-4" /> 会话记录
          </CardTitle>
          <span className="text-xs text-muted-foreground">{sessions.length} 个会话</span>
        </CardHeader>
        <CardContent>
          <div className="space-y-1.5 max-h-[500px] overflow-y-auto">
            {sessions.map(s => {
              const lastDate = new Date(s.lastTimestamp)
              const timeStr = lastDate.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
              const projectShort = s.project.split('/').slice(-2).join('/')
              const isWin = /^[A-Za-z]:/.test(s.project) || s.project.includes('\\')
              const cdCmd = isWin ? `cd "${s.project}"` : `cd "${s.project}"`
              const resumeCmd = isWin
                ? `${cdCmd}; claude --resume ${s.sessionId}`
                : `${cdCmd} && claude --resume ${s.sessionId}`
              const isCopied = copiedId === s.sessionId

              return (
                <div key={s.sessionId} className="flex items-start gap-2 p-2 rounded-md border text-sm hover:bg-muted/50 transition-colors">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <FolderOpen className="h-3 w-3 text-muted-foreground flex-shrink-0" />
                      <span className="text-xs text-muted-foreground truncate">{projectShort}</span>
                      <span className="text-[10px] text-muted-foreground ml-auto flex-shrink-0">{timeStr}</span>
                    </div>
                    <div className="truncate mt-0.5">{s.firstPrompt || '(空)'}</div>
                    <div className="flex items-center gap-2 mt-1">
                      <code className="text-[10px] text-muted-foreground font-mono">{s.sessionId}</code>
                      <button
                        className="text-muted-foreground hover:text-foreground transition-colors"
                        onClick={() => copyToClipboard(resumeCmd, s.sessionId)}
                        title="复制 resume 命令"
                      >
                        {isCopied ? <Check className="h-3 w-3 text-green-500" /> : <Copy className="h-3 w-3" />}
                      </button>
                      <Badge variant="outline" className="text-[10px]">{s.promptCount} 条</Badge>
                    </div>
                  </div>
                </div>
              )
            })}
            {sessions.length === 0 && <div className="text-sm text-muted-foreground py-4 text-center">未找到会话记录</div>}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

function SettingRow({ label, hint, children }: { label: string; hint: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col md:flex-row md:items-center gap-1 md:gap-3">
      <div className="w-40 flex-shrink-0">
        <div className="text-sm">{label}</div>
        <div className="text-[10px] text-muted-foreground font-mono">{hint}</div>
      </div>
      <div className="flex-1">{children}</div>
    </div>
  )
}

function ToggleRow({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="flex items-center justify-between p-2 rounded-md border">
      <span className="text-sm">{label}</span>
      <Switch checked={checked} onCheckedChange={onChange} />
    </div>
  )
}
