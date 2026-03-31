import { useState, useEffect } from 'react'
import { RefreshCw, LogOut, Moon, Sun, Server, Power, Home, KeyRound, Settings, ScrollText, AlertTriangle, Puzzle, Terminal, Key } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { storage } from '@/lib/storage'
import { Button } from '@/components/ui/button'
import {
  Dialog, DialogContent, DialogHeader, DialogFooter,
  DialogTitle, DialogDescription,
} from '@/components/ui/dialog'
import { useCredentials } from '@/hooks/use-credentials'
import {
  restartServer, getVersionInfo, getLogStatus, setLogStatus,
  type VersionInfo,
} from '@/api/credentials'
import { HomeTab } from '@/components/tabs/home-tab'
import { CredentialsTab } from '@/components/tabs/credentials-tab'
import { LogsTab } from '@/components/tabs/logs-tab'
import { StrategyTab } from '@/components/tabs/strategy-tab'
import { PluginsTab } from '@/components/tabs/plugins-tab'
import { ClaudeTab } from '@/components/tabs/claude-tab'
import { KeysTab } from '@/components/tabs/keys-tab'

interface DashboardProps {
  onLogout: () => void
}

type TabId = 'home' | 'credentials' | 'keys' | 'logs' | 'strategy' | 'plugins' | 'claude'

const TABS: { id: TabId; label: string; icon: React.ReactNode }[] = [
  { id: 'home', label: '首页', icon: <Home className="h-4 w-4" /> },
  { id: 'credentials', label: '凭据管理', icon: <KeyRound className="h-4 w-4" /> },
  { id: 'keys', label: 'Key 管理', icon: <Key className="h-4 w-4" /> },
  { id: 'logs', label: '日志', icon: <ScrollText className="h-4 w-4" /> },
  { id: 'strategy', label: '策略配置', icon: <Settings className="h-4 w-4" /> },
  { id: 'plugins', label: '插件', icon: <Puzzle className="h-4 w-4" /> },
  { id: 'claude', label: 'Claude', icon: <Terminal className="h-4 w-4" /> },
]

export function Dashboard({ onLogout }: DashboardProps) {
  const [activeTab, setActiveTab] = useState<TabId>('home')
  const [darkMode, setDarkMode] = useState(() => document.documentElement.classList.contains('dark'))
  const [restarting, setRestarting] = useState(false)
  const [versionInfo, setVersionInfo] = useState<VersionInfo | null>(null)
  const [logEnabled, setLogEnabled] = useState(false)
  const [confirmAction, setConfirmAction] = useState<'restart' | null>(null)

  const queryClient = useQueryClient()
  const { data, isLoading, error, refetch } = useCredentials({
    refetchInterval: activeTab === 'credentials' ? 30000 : false,
  })

  useEffect(() => {
    const check = () => getVersionInfo().then(setVersionInfo).catch(() => {})
    check()
    const t = setInterval(check, 300000)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    getLogStatus().then(s => setLogEnabled(s.enabled)).catch(() => {})
  }, [])

  const toggleDarkMode = () => { setDarkMode(!darkMode); document.documentElement.classList.toggle('dark') }
  const handleLogout = () => { storage.removeApiKey(); queryClient.clear(); onLogout() }
  const handleRefresh = () => { refetch(); toast.success('已刷新') }

  const handleToggleLog = async () => {
    const next = !logEnabled
    try {
      await setLogStatus(next)
      setLogEnabled(next)
      toast.success(next ? '消息日志已开启' : '消息日志已关闭')
    } catch { toast.error('切换日志失败') }
  }

  const handleRestart = async () => {
    setConfirmAction(null)
    setRestarting(true)
    toast.info('正在重启...')
    const r = await restartServer()
    setRestarting(false)
    r.success ? toast.success(r.message) : toast.error(r.message)
    refetch()
  }

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary mx-auto mb-4" />
          <p className="text-muted-foreground">加载中...</p>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background p-4">
        <div className="text-center space-y-4">
          <div className="text-red-500">加载失败</div>
          <p className="text-muted-foreground">{(error as Error).message}</p>
          <div className="space-x-2">
            <Button onClick={() => refetch()}>重试</Button>
            <Button variant="outline" onClick={handleLogout}>重新登录</Button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-background">
      {/* 顶部导航 */}
      <header className="sticky top-0 z-50 w-full border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div className="container flex h-14 items-center justify-between px-4 md:px-8">
          <div className="flex items-center gap-3 min-w-0">
            {/* 版本号 */}
            <div className="flex items-center gap-1.5 flex-shrink-0">
              <Server className="h-5 w-5" />
              <span className="font-semibold">Kiro Admin</span>
              {versionInfo && <span className="text-xs text-muted-foreground">v{versionInfo.current}</span>}
            </div>
            {/* Tab 切换 */}
            <nav className="flex items-center gap-1 ml-2 md:ml-4">
              {TABS.map(tab => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={`flex items-center gap-1.5 px-2 md:px-3 py-1.5 rounded-md text-sm transition-colors ${
                    activeTab === tab.id
                      ? 'bg-primary text-primary-foreground'
                      : 'text-muted-foreground hover:text-foreground hover:bg-muted'
                  }`}
                  title={tab.label}
                >
                  {tab.icon}
                  <span className="hidden md:inline">{tab.label}</span>
                </button>
              ))}
            </nav>
          </div>
          <div className="flex items-center gap-1 flex-shrink-0">
            <Button variant="ghost" size="sm" className="gap-1" onClick={handleToggleLog} title={logEnabled ? '日志开' : '日志关'}>
              <ScrollText className={`h-4 w-4 ${logEnabled ? 'text-green-500' : ''}`} />
              <span className="text-xs hidden md:inline">{logEnabled ? '日志开' : '日志关'}</span>
            </Button>
            <Button variant="ghost" size="sm" className="gap-1" onClick={toggleDarkMode} title={darkMode ? '浅色' : '深色'}>
              {darkMode ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
              <span className="text-xs hidden md:inline">{darkMode ? '浅色' : '深色'}</span>
            </Button>
            <Button variant="ghost" size="sm" className="gap-1" onClick={() => setConfirmAction('restart')} disabled={restarting} title="重启">
              <Power className={`h-4 w-4 ${restarting ? 'animate-spin' : ''}`} />
              <span className="text-xs hidden md:inline">重启</span>
            </Button>
            <Button variant="ghost" size="sm" className="gap-1" onClick={handleRefresh} title="刷新">
              <RefreshCw className="h-4 w-4" />
              <span className="text-xs hidden md:inline">刷新</span>
            </Button>
            <Button variant="ghost" size="sm" className="gap-1" onClick={handleLogout} title="退出">
              <LogOut className="h-4 w-4" />
              <span className="text-xs hidden md:inline">退出</span>
            </Button>
          </div>
        </div>
      </header>
      {/* Tab 内容 */}
      <main className="container mx-auto px-4 md:px-8 py-6">
        {activeTab === 'home' && (
          <HomeTab credentialCount={data?.total || 0} availableCount={data?.available || 0} />
        )}
        {activeTab === 'credentials' && <CredentialsTab />}
        {activeTab === 'keys' && <KeysTab />}
        {activeTab === 'logs' && <LogsTab />}
        {activeTab === 'strategy' && <StrategyTab />}
        {activeTab === 'plugins' && <PluginsTab />}
        {activeTab === 'claude' && <ClaudeTab />}
      </main>

      {/* 重启确认对话框 */}
      <Dialog open={confirmAction !== null} onOpenChange={open => { if (!open) setConfirmAction(null) }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <div className="mx-auto mb-2 flex h-12 w-12 items-center justify-center rounded-full bg-orange-100 dark:bg-orange-900/30">
              <AlertTriangle className="h-6 w-6 text-orange-500" />
            </div>
            <DialogTitle className="text-center">确认重启服务器</DialogTitle>
            <DialogDescription className="text-center">
              重启期间所有连接将中断，正在进行的请求会丢失。确定继续吗？
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="flex gap-2 sm:justify-center">
            <Button variant="outline" onClick={() => setConfirmAction(null)}>取消</Button>
            <Button variant="destructive" onClick={handleRestart}>重启</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

    </div>
  )
}
