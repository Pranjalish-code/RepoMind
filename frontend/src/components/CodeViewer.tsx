import Editor from '@monaco-editor/react'
import { X, FileCode2 } from 'lucide-react'

interface CodeViewerProps {
  filePath: string | null
  content: string
  language?: string
  highlightLines?: [number, number]
  onClose?: () => void
}

const EXT_LANG: Record<string, string> = {
  py: 'python', js: 'javascript', jsx: 'javascript',
  ts: 'typescript', tsx: 'typescript', json: 'json',
  md: 'markdown', css: 'css', html: 'html',
  yaml: 'yaml', yml: 'yaml', toml: 'toml',
  rs: 'rust', go: 'go', java: 'java',
  cpp: 'cpp', c: 'c', rb: 'ruby',
  sh: 'shell', bash: 'shell', sql: 'sql',
}

function detectLanguage(path: string): string {
  const ext = path.split('.').pop()?.toLowerCase() ?? ''
  return EXT_LANG[ext] ?? 'plaintext'
}

export default function CodeViewer({ filePath, content, language, highlightLines, onClose }: CodeViewerProps) {
  const lang = language ?? (filePath ? detectLanguage(filePath) : 'plaintext')

  if (!filePath) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center gap-3">
        <FileCode2 className="w-10 h-10 text-slate-600" />
        <p className="text-slate-500 text-sm">Click a citation to view source code</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-surface-600 shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <FileCode2 className="w-3.5 h-3.5 text-brand-400 shrink-0" />
          <span className="text-xs font-mono text-slate-300 truncate">{filePath}</span>
          {highlightLines && (
            <span className="text-xs text-slate-500">
              L{highlightLines[0]}–{highlightLines[1]}
            </span>
          )}
        </div>
        {onClose && (
          <button
            onClick={onClose}
            className="text-slate-500 hover:text-white transition-colors p-0.5 rounded"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {/* Monaco Editor */}
      <div className="flex-1 min-h-0">
        <Editor
          height="100%"
          language={lang}
          value={content}
          theme="vs-dark"
          options={{
            readOnly: true,
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            fontSize: 12,
            lineHeight: 18,
            fontFamily: 'JetBrains Mono, Fira Code, monospace',
            wordWrap: 'on',
            lineNumbers: 'on',
            renderLineHighlight: highlightLines ? 'all' : 'line',
            scrollbar: {
              vertical: 'auto',
              horizontal: 'auto',
              verticalScrollbarSize: 6,
              horizontalScrollbarSize: 6,
            },
          }}
          onMount={(editor) => {
            if (highlightLines) {
              editor.revealLineInCenter(highlightLines[0])
              editor.setSelection({
                startLineNumber: highlightLines[0],
                startColumn: 1,
                endLineNumber: highlightLines[1],
                endColumn: 999,
              })
            }
          }}
        />
      </div>
    </div>
  )
}
