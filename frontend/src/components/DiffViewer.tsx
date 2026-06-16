import { DiffEditor } from '@monaco-editor/react'
import { GitCompare } from 'lucide-react'

interface DiffViewerProps {
  original: string
  modified: string
  filePath: string
  language?: string
}

const EXT_LANG: Record<string, string> = {
  py: 'python', js: 'javascript', jsx: 'javascript',
  ts: 'typescript', tsx: 'typescript', json: 'json',
  rs: 'rust', go: 'go', java: 'java', cpp: 'cpp',
}

function detectLang(path: string): string {
  const ext = path.split('.').pop()?.toLowerCase() ?? ''
  return EXT_LANG[ext] ?? 'plaintext'
}

export default function DiffViewer({ original, modified, filePath, language }: DiffViewerProps) {
  const lang = language ?? detectLang(filePath)

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-surface-600 shrink-0">
        <GitCompare className="w-3.5 h-3.5 text-brand-400" />
        <span className="text-xs font-mono text-slate-300 truncate">{filePath}</span>
        <span className="badge badge-blue ml-auto">diff</span>
      </div>

      {/* Monaco Diff Editor */}
      <div className="flex-1 min-h-0">
        <DiffEditor
          height="100%"
          language={lang}
          original={original}
          modified={modified}
          theme="vs-dark"
          options={{
            readOnly: true,
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            fontSize: 12,
            lineHeight: 18,
            fontFamily: 'JetBrains Mono, Fira Code, monospace',
            wordWrap: 'on',
            renderSideBySide: true,
            scrollbar: {
              vertical: 'auto',
              horizontal: 'auto',
              verticalScrollbarSize: 6,
              horizontalScrollbarSize: 6,
            },
          }}
        />
      </div>
    </div>
  )
}
