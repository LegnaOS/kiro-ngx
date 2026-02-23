import { useState, useEffect, useMemo } from 'react'
import { toast } from 'sonner'
import { Loader2 } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { getRawCredentials, saveRawCredentials, restartServer } from '@/api/credentials'
import { extractErrorMessage } from '@/lib/utils'

interface CredentialsEditorDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function CredentialsEditorDialog({ open, onOpenChange }: CredentialsEditorDialogProps) {
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  // 打开时加载当前内容
  useEffect(() => {
    if (!open) return
    setLoading(true)
    getRawCredentials()
      .then(res => {
        // 尝试格式化显示
        try {
          const parsed = JSON.parse(res.content)
          setContent(JSON.stringify(parsed, null, 2))
        } catch {
          setContent(res.content)
        }
      })
      .catch(err => toast.error('加载失败: ' + extractErrorMessage(err)))
      .finally(() => setLoading(false))
  }, [open])

  // 实时校验 JSON
  const jsonError = useMemo(() => {
    if (!content.trim()) return ''
    try {
      const parsed = JSON.parse(content)
      if (!Array.isArray(parsed)) return '内容必须是 JSON 数组'
      return ''
    } catch (e) {
      return (e as Error).message
    }
  }, [content])
  const handleSave = async () => {
    setSaving(true)
    try {
      const result = await saveRawCredentials(content)
      toast.success(result.message)

      // 自动重启服务
      try {
        toast.success('正在重启服务以加载新凭据...')
        const restartResult = await restartServer()
        if (restartResult.success) {
          toast.success('服务已重启成功')
        } else {
          toast.warning(restartResult.message)
        }
      } catch {
        toast.warning('凭据已写入，但自动重启失败，请手动重启')
      }

      onOpenChange(false)
    } catch (err) {
      toast.error('保存失败: ' + extractErrorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(newOpen) => {
        if (!newOpen && saving) return
        onOpenChange(newOpen)
      }}
    >
      <DialogContent className="sm:max-w-2xl max-h-[80vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>编辑凭据文件</DialogTitle>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto space-y-4 py-4">
          <div className="text-sm text-yellow-600 dark:text-yellow-400 bg-yellow-50 dark:bg-yellow-900/20 p-3 rounded-md">
            直接编辑 credentials.json 内容，保存后将重启服务生效。
          </div>

          {loading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-6 w-6 animate-spin" />
              <span className="ml-2 text-muted-foreground">加载中...</span>
            </div>
          ) : (
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              disabled={saving}
              className="flex min-h-[300px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 font-mono"
            />
          )}

          {jsonError && (
            <div className="text-sm text-red-600 dark:text-red-400">JSON 错误: {jsonError}</div>
          )}
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={saving}
          >
            取消
          </Button>
          <Button
            type="button"
            onClick={handleSave}
            disabled={saving || loading || !content.trim() || !!jsonError}
          >
            {saving ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                保存中...
              </>
            ) : (
              '保存并重启'
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
