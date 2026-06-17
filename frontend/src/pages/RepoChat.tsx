import { useState, useEffect } from 'react'
import { useParams } from 'react-router-dom'
import { PanelLeftClose, PanelLeftOpen, Loader2 } from 'lucide-react'
import { getRepo, getRepoFile, IndexedFile } from '../api/repoApi'
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
        setFiles(repo.files || [])
        setHistory(hist)
      })
      .catch(err => console.error(err))
      .finally(() => setLoading(false))
  }, [repoId])

  const handleFileSelect = async (path: string) => {
    if (!repoId) return

    setSelectedFile(path)
    setCodeContent('Loading file...')
    setHighlightLines(undefined)

    const file = files.find(f => f.file_path === path)
    setCodeLanguage(file?.language)

    try {
      const data = await getRepoFile(repoId, path)
      setCodeContent(data.content)
      setCodeLanguage(data.language || file?.language)
    } catch (err) {
      console.error(err)
      setCodeContent(`// Failed to load file: ${path}`)
    }
  }

  const handleCitationClick = async (citation: Citation) => {
    if (!repoId) return

    setSelectedFile(citation.file_path)
    setCodeContent('Loading source...')
    setHighlightLines([citation.start_line, citation.end_line])

    const file = files.find(f => f.file_path === citation.file_path)
    setCodeLanguage(file?.language)

    try {
      const data = await getRepoFile(repoId, citation.file_path)
      setCodeContent(data.content)
      setCodeLanguage(data.language || file?.language)
    } catch (err) {
      console.error(err)
      setCodeContent(`// Failed to load citation source: ${citation.file_path}`)
    }
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="w-8 h-8 text-brand-500 animate-spin" />
      </div>
    )
  }

  return (
    <div className="flex h-full w-full bg-surface-950">
      {/* File Tree Sidebar */}
      <div
        className={`shrink-0 border-r border-surface-800 transition-all duration-300 ease-in-out flex flex-col bg-surface-900/50 ${
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
        <div className="flex-1 flex flex-col min-w-0 border-r border-surface-800 bg-surface-950 relative">
          <div className="px-4 py-3 border-b border-surface-800 shrink-0 flex items-center gap-3 bg-surface-900/30 backdrop-blur z-10">
            <button
              onClick={() => setTreeOpen(o => !o)}
              className="text-slate-400 hover:text-brand-400 transition-colors p-1 rounded hover:bg-surface-800"
              title="Toggle sidebar"
            >
              {treeOpen ? <PanelLeftClose className="w-4.5 h-4.5" /> : <PanelLeftOpen className="w-4.5 h-4.5" />}
            </button>
            <span className="text-sm font-medium text-slate-200">Repository QA</span>
          </div>
          <div className="flex-1 min-h-0 relative">
            <ChatWindow
              repoId={repoId!}
              history={history}
              onCitationClick={handleCitationClick}
            />
          </div>
        </div>

        {/* Code Viewer */}
        <div className="w-[45%] shrink-0 bg-surface-950 flex flex-col">
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
