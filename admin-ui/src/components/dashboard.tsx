import { useState, useEffect, useRef } from 'react'
import { RefreshCw, LogOut, Moon, Sun, Server, Power, Home, KeyRound, Settings, ScrollText, AlertTriangle, GitBranch, Check } from 'lucide-react'
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
import {
  restartServer, updateAndRestart, getVersionInfo, getGitStatus,
  getGitLog, getLogStatus, setLogStatus,
  type VersionInfo, type GitCommit,
} from '@/api/credentials'
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
  const [localChangesWarning, setLocalChangesWarning] = useState<string[] | null>(null)
  // commit 列表面板
  const [showCommitPanel, setShowCommitPanel] = useState(false)
  const [commits, setCommits] = useState<GitCommit[]>([])
  const [loadingCommits, setLoadingCommits] = useState(false)
  // 选中的目标 commit（用于确认弹窗）
  const [targetCommit, setTargetCommit] = useState<string | undefined>(undefined)
  const panelRef = useRef<HTMLDivElement>(null)

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

  // 点击面板外部关闭
  useEffect(() => {
    if (!showCommitPanel) return
    const handler = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setShowCommitPanel(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showCommitPanel])

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

  // 打开 commit 列表面板
  const handleToggleCommitPanel = async () => {
    if (showCommitPanel) {
      setShowCommitPanel(false)
      return
    }
    setShowCommitPanel(true)
    setLoadingCommits(true)
    try {
      const { commits: list } = await getGitLog()
      setCommits(list)
    } catch {
      toast.error('获取版本列表失败')
    }
    setLoadingCommits(false)
  }

  // 选择 commit 后检测本地改动
  const handleSelectCommit = async (hash?: string) => {
    setShowCommitPanel(false)
    setTargetCommit(hash)
    try {
      const git = await getGitStatus()
      if (git.hasLocalChanges) {
        setLocalChangesWarning(git.changedFiles)
        return
      }
    } catch { /* 检测失败不阻塞 */ }
    setConfirmAction('update')
  }

  // badge 点击（更新到最新）也走确认

  // 本地改动警告确认后继续
  const handleForceUpdate = () => {
    setLocalChangesWarning(null)
    setConfirmAction('update')
  }

  const handleUpdate = async () => {
    setConfirmAction(null)
    setUpdating(true)
    toast.info('正在更新...')
    const r = await updateAndRestart((step) => { toast.info(step) }, targetCommit)
    setUpdating(false)
    r.success ? (toast.success(r.message), window.location.reload()) : toast.error(r.message)
    setTargetCommit(undefined)
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
            {/* 版本号 + commit 面板触发 */}
            <div className="relative flex items-center gap-1.5 flex-shrink-0">
              <Server className="h-5 w-5" />
              <span className="font-semibold">Kiro Admin</span>
              {versionInfo && <span className="text-xs text-muted-foreground">v{versionInfo.current}</span>}
              {versionInfo?.hasUpdate && (
                <Badge variant="outline" className="text-xs cursor-pointer border-orange-400 text-orange-500 hover:bg-orange-50 dark:hover:bg-orange-950" onClick={() => handleSelectCommit(undefined)}>
                  {versionInfo.latest !== 'unknown' && versionInfo.latest !== versionInfo.current
                    ? `v${versionInfo.latest} 可用`
                    : `${versionInfo.behindCount} 个新提交`}
                </Badge>
              )}
              <button
                onClick={handleToggleCommitPanel}
                disabled={updating || restarting}
                className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                title="版本列表"
              >
                <GitBranch className={`h-4 w-4 ${loadingCommits ? 'animate-spin' : ''}`} />
              </button>
              {/* Commit 列表面板 */}
              {showCommitPanel && (
                <div ref={panelRef} className="absolute top-full left-0 mt-2 z-[60] w-[calc(100vw-2rem)] md:w-96 max-h-80 overflow-y-auto rounded-lg border bg-popover shadow-lg">
                  {loadingCommits ? (
                    <div className="flex items-center justify-center py-8">
                      <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-primary" />
                    </div>
                  ) : commits.length === 0 ? (
                    <div className="py-6 text-center text-sm text-muted-foreground">无法获取版本列表</div>
                  ) : (
                    <div className="py-1">
                      {commits.map(c => (
                        <button
                          key={c.hash}
                          onClick={() => c.isCurrent ? setShowCommitPanel(false) : handleSelectCommit(c.hash)}
                          className={`w-full text-left px-3 py-2 text-sm hover:bg-muted transition-colors flex items-start gap-2 ${c.isCurrent ? 'bg-primary/5' : ''}`}
                        >
                          <div className="flex-shrink-0 mt-0.5">
                            {c.isCurrent
                              ? <Check className="h-3.5 w-3.5 text-green-500" />
                              : <GitBranch className="h-3.5 w-3.5 text-muted-foreground" />}
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2">
                              <span className="font-mono text-xs text-muted-foreground">{c.short}</span>
                              {c.isCurrent && <span className="text-[10px] text-green-600 font-medium">当前</span>}
                            </div>
                            <div className="truncate text-foreground">{c.message}</div>
                            <div className="text-xs text-muted-foreground">{c.date.split(' ').slice(0, 2).join(' ')}</div>
                          </div>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
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
            <Button variant="ghost" size="sm" className="gap-1" onClick={() => setConfirmAction('restart')} disabled={restarting || updating} title="重启">
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
                : targetCommit
                  ? `将切换到 commit ${targetCommit.slice(0, 8)} 并重启服务器，期间服务不可用。确定继续吗？`
                  : '将从远程拉取最新版本并重启服务器，期间服务不可用。确定继续吗？'}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="flex gap-2 sm:justify-center">
            <Button variant="outline" onClick={() => { setConfirmAction(null); setTargetCommit(undefined) }}>取消</Button>
            <Button variant="destructive" onClick={confirmAction === 'restart' ? handleRestart : handleUpdate}>
              {confirmAction === 'restart' ? '重启' : '更新并重启'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 本地改动警告弹窗 */}
      <Dialog open={localChangesWarning !== null} onOpenChange={open => { if (!open) { setLocalChangesWarning(null); setTargetCommit(undefined) } }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <div className="mx-auto mb-2 flex h-12 w-12 items-center justify-center rounded-full bg-orange-100 dark:bg-orange-900/30">
              <AlertTriangle className="h-6 w-6 text-orange-500" />
            </div>
            <DialogTitle className="text-center">检测到本地改动</DialogTitle>
            <DialogDescription className="text-center">
              更新会丢弃以下本地改动，此操作不可恢复。
            </DialogDescription>
          </DialogHeader>
          {localChangesWarning && localChangesWarning.length > 0 && (
            <div className="max-h-40 overflow-y-auto rounded-md bg-muted p-2 text-xs font-mono">
              {localChangesWarning.map((f, i) => <div key={i}>{f}</div>)}
            </div>
          )}
          <DialogFooter className="flex gap-2 sm:justify-center">
            <Button variant="outline" onClick={() => { setLocalChangesWarning(null); setTargetCommit(undefined) }}>取消</Button>
            <Button variant="destructive" onClick={handleForceUpdate}>丢弃并更新</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

    </div>
  )
}