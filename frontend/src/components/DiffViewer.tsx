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
    <div className="flex flex-col h-full bg-surface-950">
      {/* Header */}
      <div className="flex items-center px-4 py-2 border-b border-surface-800 bg-surface-900/40 shrink-0">
        <div className="flex items-center w-full bg-surface-800 border border-surface-700/50 rounded-md px-3 py-1.5 shadow-sm">
          <GitCompare className="w-4 h-4 text-brand-400 mr-2 shrink-0" />
          <span className="text-[13px] font-mono text-slate-200 truncate">{filePath}</span>
          <span className="badge badge-blue ml-auto opacity-80">diff</span>
        </div>
      </div>

      {/* Monaco Diff Editor */}
      <div className="flex-1 min-h-0 relative">
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
            fontSize: 13,
            lineHeight: 22,
            fontFamily: 'JetBrains Mono, Fira Code, monospace',
            wordWrap: 'on',
            renderSideBySide: true,
            padding: { top: 16, bottom: 16 },
            scrollbar: {
              vertical: 'auto',
              horizontal: 'auto',
              verticalScrollbarSize: 8,
              horizontalScrollbarSize: 8,
            },
          }}
        />
      </div>
    </div>
  )
}
