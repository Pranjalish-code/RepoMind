import { useState, useEffect } from 'react'
import { useParams } from 'react-router-dom'
import { PanelLeftClose, PanelLeftOpen, Loader2 } from 'lucide-react'
import { getRepo, IndexedFile } from '../api/repoApi'
import { getChatHistory, Citation } from '../api/chatApi'
import FileTree from '../components/FileTree'
import ChatWindow from '../components/ChatWindow'
import CodeViewer from '../components/CodeViewer'

export default function RepoChat() {
  const { repoId } = useParams<{ repoId: string }>()
  const [files, setFiles] = useState<IndexedFile[]>([])
  const [history, setHistory] = useState<any[]>([])
  const [loading, setLoading] = useState(true)

  // Layout state
  const [treeOpen, setTreeOpen] = useState(true)
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [codeContent, setCodeContent] = useState<string>('')
  const [codeLanguage, setCodeLanguage] = useState<string | undefined>()
  const [highlightLines, setHighlightLines] = useState<[number, number] | undefined>()

  useEffect(() => {
    if (!repoId) return
    Promise.all([
      getRepo(repoId),
      getChatHistory(repoId),
    ])
      .then(([repo, hist]) => {
        setFiles(repo.indexed_files || [])
        setHistory(hist)
      })
      .catch(err => console.error(err))
      .finally(() => setLoading(false))
  }, [repoId])

  // Mock fetching file content since we don't have a direct raw file API yet
  // In a real app, you'd add a GET /repos/{id}/files/{path} endpoint
  const handleFileSelect = (path: string) => {
    setSelectedFile(path)
    setCodeContent(`// Viewing: ${path}\n// Content fetching requires a backend endpoint\n// which is currently mocked in the UI.`)
    setHighlightLines(undefined)
    const file = files.find(f => f.file_path === path)
    setCodeLanguage(file?.language)
  }

  const handleCitationClick = (citation: Citation) => {
    setSelectedFile(citation.file_path)
    setCodeContent(`// Source from citation: ${citation.file_path}\n// Lines: ${citation.start_line}-${citation.end_line}\n\n// Imagine actual code here.`)
    setHighlightLines([citation.start_line, citation.end_line])
    const file = files.find(f => f.file_path === citation.file_path)
    setCodeLanguage(file?.language)
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="w-8 h-8 text-brand-500 animate-spin" />
      </div>
    )
  }

  return (
    <div className="flex h-full w-full">
      {/* File Tree Sidebar */}
      <div
        className={`shrink-0 border-r border-surface-600 transition-all duration-300 ease-in-out flex flex-col bg-surface-800 ${
          treeOpen ? 'w-64 opacity-100' : 'w-0 opacity-0 overflow-hidden'
        }`}
      >
        <FileTree
          files={files}
          onFileSelect={handleFileSelect}
          selectedFile={selectedFile ?? undefined}
        />
      </div>

      {/* Main Content Area */}
      <div className="flex-1 flex min-w-0">
        {/* Chat Window */}
        <div className="flex-1 flex flex-col min-w-0 border-r border-surface-600">
          <div className="px-3 py-2 border-b border-surface-600 shrink-0 flex items-center gap-2">
            <button
              onClick={() => setTreeOpen(o => !o)}
              className="text-slate-500 hover:text-white transition-colors"
              title="Toggle sidebar"
            >
              {treeOpen ? <PanelLeftClose className="w-4 h-4" /> : <PanelLeftOpen className="w-4 h-4" />}
            </button>
            <span className="text-sm font-medium text-slate-300">Repository QA</span>
          </div>
          <div className="flex-1 min-h-0">
            <ChatWindow
              repoId={repoId!}
              history={history}
              onCitationClick={handleCitationClick}
            />
          </div>
        </div>

        {/* Code Viewer */}
        <div className="w-1/2 shrink-0 bg-surface-900 flex flex-col">
          <CodeViewer
            filePath={selectedFile}
            content={codeContent}
            language={codeLanguage}
            highlightLines={highlightLines}
            onClose={() => setSelectedFile(null)}
          />
        </div>
      </div>
    </div>
  )
}
