import { useState, useEffect } from 'react'
import { RefreshCw, LogOut, Moon, Sun, Server, Power, Download, Home, KeyRound, Settings, ScrollText, AlertTriangle } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { storage } from '@/lib/storage'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  Dialog, DialogContent, DialogHeader, DialogFooter,
  DialogTitle, DialogDescription,
} from '@/components/ui/dialog'
import { useCredentials } from '@/hooks/use-credentials'
import { restartServer, updateAndRestart, getVersionInfo, getLogStatus, setLogStatus, type VersionInfo } from '@/api/credentials'
import { HomeTab } from '@/components/tabs/home-tab'
import { CredentialsTab } from '@/components/tabs/credentials-tab'
import { StrategyTab } from '@/components/tabs/strategy-tab'

interface DashboardProps {
  onLogout: () => void
}

type TabId = 'home' | 'credentials' | 'strategy'

const TABS: { id: TabId; label: string; icon: React.ReactNode }[] = [
  { id: 'home', label: '首页', icon: <Home className="h-4 w-4" /> },
  { id: 'credentials', label: '凭据管理', icon: <KeyRound className="h-4 w-4" /> },
  { id: 'strategy', label: '策略配置', icon: <Settings className="h-4 w-4" /> },
]

export function Dashboard({ onLogout }: DashboardProps) {
  const [activeTab, setActiveTab] = useState<TabId>('home')
  const [darkMode, setDarkMode] = useState(() => document.documentElement.classList.contains('dark'))
  const [restarting, setRestarting] = useState(false)
  const [updating, setUpdating] = useState(false)
  const [versionInfo, setVersionInfo] = useState<VersionInfo | null>(null)
  const [logEnabled, setLogEnabled] = useState(false)
  const [confirmAction, setConfirmAction] = useState<'restart' | 'update' | null>(null)

  const queryClient = useQueryClient()
  const { data, isLoading, error, refetch } = useCredentials()

  useEffect(() => {
    const check = () => getVersionInfo().then(setVersionInfo).catch(() => {})
    check()
    const t = setInterval(check, 60000)
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

  const handleUpdate = async () => {
    setConfirmAction(null)
    setUpdating(true)
    toast.info('正在更新...')
    const r = await updateAndRestart()
    setUpdating(false)
    r.success ? (toast.success(r.message), window.location.reload()) : toast.error(r.message)
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
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <Server className="h-5 w-5" />
              <span className="font-semibold">Kiro Admin</span>
              {versionInfo && <span className="text-xs text-muted-foreground">v{versionInfo.current}</span>}
              {versionInfo?.hasUpdate && (
                <Badge variant="outline" className="text-xs cursor-pointer border-orange-400 text-orange-500 hover:bg-orange-50 dark:hover:bg-orange-950" onClick={() => setConfirmAction('update')}>
                  v{versionInfo.latest} 可用
                </Badge>
              )}
            </div>
            {/* Tab 切换 */}
            <nav className="flex items-center gap-1 ml-4">
              {TABS.map(tab => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm transition-colors ${
                    activeTab === tab.id
                      ? 'bg-primary text-primary-foreground'
                      : 'text-muted-foreground hover:text-foreground hover:bg-muted'
                  }`}
                >
                  {tab.icon}
                  {tab.label}
                </button>
              ))}
            </nav>
          </div>
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="icon" onClick={handleToggleLog} title={logEnabled ? '关闭消息日志' : '开启消息日志'}>
              <ScrollText className={`h-5 w-5 ${logEnabled ? 'text-green-500' : ''}`} />
            </Button>
            <Button variant="ghost" size="icon" onClick={toggleDarkMode}>
              {darkMode ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
            </Button>
            <Button variant="ghost" size="icon" onClick={() => setConfirmAction('update')} disabled={updating || restarting} title="更新">
              <Download className={`h-5 w-5 ${updating ? 'animate-bounce' : versionInfo?.hasUpdate ? 'text-orange-500' : ''}`} />
            </Button>
            <Button variant="ghost" size="icon" onClick={() => setConfirmAction('restart')} disabled={restarting || updating} title="重启">
              <Power className={`h-5 w-5 ${restarting ? 'animate-spin' : ''}`} />
            </Button>
            <Button variant="ghost" size="icon" onClick={handleRefresh}>
              <RefreshCw className="h-5 w-5" />
            </Button>
            <Button variant="ghost" size="icon" onClick={handleLogout}>
              <LogOut className="h-5 w-5" />
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
        {activeTab === 'strategy' && <StrategyTab />}
      </main>

      {/* 重启/更新确认对话框 */}
      <Dialog open={confirmAction !== null} onOpenChange={open => { if (!open) setConfirmAction(null) }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <div className="mx-auto mb-2 flex h-12 w-12 items-center justify-center rounded-full bg-orange-100 dark:bg-orange-900/30">
              <AlertTriangle className="h-6 w-6 text-orange-500" />
            </div>
            <DialogTitle className="text-center">
              {confirmAction === 'restart' ? '确认重启服务器' : '确认更新并重启'}
            </DialogTitle>
            <DialogDescription className="text-center">
              {confirmAction === 'restart'
                ? '重启期间所有连接将中断，正在进行的请求会丢失。确定继续吗？'
                : '将从远程拉取最新版本并重启服务器，期间服务不可用。确定继续吗？'}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="flex gap-2 sm:justify-center">
            <Button variant="outline" onClick={() => setConfirmAction(null)}>取消</Button>
            <Button
              variant="destructive"
              onClick={confirmAction === 'restart' ? handleRestart : handleUpdate}
            >
              {confirmAction === 'restart' ? '重启' : '更新并重启'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
