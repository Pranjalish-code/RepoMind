import Editor from '@monaco-editor/react'
import { X, FileCode2, Code } from 'lucide-react'

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
      <div className="flex flex-col items-center justify-center h-full text-center gap-4 bg-surface-950">
        <div className="w-16 h-16 rounded-full bg-surface-900 border border-surface-800 flex items-center justify-center shadow-inner">
          <Code className="w-8 h-8 text-surface-500" />
        </div>
        <div>
          <h3 className="text-slate-200 font-medium tracking-wide">No file selected</h3>
          <p className="text-slate-500 text-sm mt-1">Select a file from the tree or click a citation to view source code.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full bg-surface-950">
      {/* Editor Tab Header */}
      <div className="flex items-center px-4 py-2 border-b border-surface-800 bg-surface-900/40 shrink-0">
        <div className="flex items-center justify-between w-full bg-surface-800 border border-surface-700/50 rounded-md px-3 py-1.5 shadow-sm">
          <div className="flex items-center gap-2 min-w-0">
            <FileCode2 className="w-4 h-4 text-brand-400 shrink-0" />
            <span className="text-[13px] font-mono text-slate-200 truncate">{filePath}</span>
            {highlightLines && (
              <span className="badge badge-blue ml-2 opacity-80">
                L{highlightLines[0]}-{highlightLines[1]}
              </span>
            )}
          </div>
          {onClose && (
            <button
              onClick={onClose}
              className="text-slate-400 hover:text-rose-400 hover:bg-rose-500/10 transition-all p-1 rounded ml-3 shrink-0"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>

      {/* Monaco Editor */}
      <div className="flex-1 min-h-0 relative">
        <Editor
          height="100%"
          language={lang}
          value={content}
          theme="vs-dark"
          options={{
            readOnly: true,
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            fontSize: 13,
            lineHeight: 22,
            fontFamily: 'JetBrains Mono, Fira Code, monospace',
            wordWrap: 'on',
            lineNumbers: 'on',
            renderLineHighlight: highlightLines ? 'all' : 'line',
            padding: { top: 16, bottom: 16 },
            scrollbar: {
              vertical: 'auto',
              horizontal: 'auto',
              verticalScrollbarSize: 8,
              horizontalScrollbarSize: 8,
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
