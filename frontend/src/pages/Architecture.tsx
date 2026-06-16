import { useState, useEffect } from 'react'
import { useParams } from 'react-router-dom'
import { Share2, Loader2, Play } from 'lucide-react'
import { generateArchitecture, listArchitectures, ArchitectureDiagramResponse, ArchitectureListItem } from '../api/architectureApi'
import MermaidViewer from '../components/MermaidViewer'

export default function Architecture() {
  const { repoId } = useParams<{ repoId: string }>()
  const [history, setHistory] = useState<ArchitectureListItem[]>([])
  const [loading, setLoading] = useState(false)
  const [loadingHistory, setLoadingHistory] = useState(true)
  const [current, setCurrent] = useState<ArchitectureDiagramResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const fetchHistory = async () => {
    if (!repoId) return
    try {
      const data = await listArchitectures(repoId)
      setHistory(data)
    } catch (err) {
      console.error(err)
    } finally {
      setLoadingHistory(false)
    }
  }

  useEffect(() => {
    fetchHistory()
  }, [repoId])

  const handleGenerate = async () => {
    if (!repoId || loading) return
    setLoading(true)
    setError(null)
    try {
      const result = await generateArchitecture(repoId)
      setCurrent(result)
      await fetchHistory()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex h-full w-full">
      {/* Sidebar: History */}
      <div className="w-64 shrink-0 border-r border-surface-600 bg-surface-800 flex flex-col">
        <div className="p-4 border-b border-surface-600">
          <button
            onClick={handleGenerate}
            disabled={loading}
            className="btn-primary w-full justify-center"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
            Generate New
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-2 space-y-1.5">
          {loadingHistory ? (
            <div className="flex justify-center p-4"><Loader2 className="w-4 h-4 animate-spin text-brand-500" /></div>
          ) : history.length === 0 ? (
            <p className="text-xs text-slate-500 text-center py-4">No diagrams generated yet.</p>
          ) : (
            history.map(h => (
              <button
                key={h.id}
                className={`w-full text-left p-2 rounded-lg transition-colors border
                  ${current?.diagram_db_id === h.id ? 'bg-surface-700 border-brand-500/30' : 'bg-surface-800 border-surface-600 hover:bg-surface-700'}
                `}
                onClick={() => {
                  // In a real app, you'd fetch the full diagram by ID.
                  // Here we just use the preview or a mock for MVP.
                  setCurrent({
                    repo_id: repoId!,
                    repo_name: 'History Item',
                    confidence: h.confidence_score ? h.confidence_score * 100 : 0,
                    mermaid_code: h.mermaid_preview + '\n...',
                    explanation: 'Historical diagram loaded from DB.',
                    formatted_output: '',
                    detected_components: [],
                    component_count: h.component_count,
                    diagram_db_id: h.id,
                    note: 'Historical diagram',
                  })
                }}
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-slate-300">
                    {new Date(h.created_at).toLocaleDateString()}
                  </span>
                  <span className={`text-xs ${h.confidence_score && h.confidence_score >= 0.7 ? 'text-emerald-400' : 'text-amber-400'}`}>
                    {h.confidence_score ? Math.round(h.confidence_score * 100) : 0}%
                  </span>
                </div>
                <p className="text-xs text-slate-500 mt-1 truncate">{h.component_count} components</p>
              </button>
            ))
          )}
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 bg-surface-900 flex flex-col min-w-0">
        {error && (
          <div className="m-4 p-3 bg-red-900/30 border border-red-700 rounded-lg text-red-300 text-sm">
            {error}
          </div>
        )}

        {loading ? (
          <div className="flex-1 flex flex-col items-center justify-center gap-4">
            <Loader2 className="w-10 h-10 animate-spin text-brand-500" />
            <p className="text-slate-400">Analyzing codebase & generating architecture…</p>
          </div>
        ) : current ? (
          <div className="flex-1 overflow-y-auto p-8">
            <div className="max-w-4xl mx-auto space-y-6">
              {/* Header */}
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-2xl font-bold text-white">Architecture Diagram</h2>
                  <p className="text-slate-400 mt-1">{current.component_count} components detected</p>
                </div>
                <div className="text-right">
                  <span className="text-sm text-slate-400">Confidence</span>
                  <div className={`text-2xl font-bold ${current.confidence >= 70 ? 'text-emerald-400' : current.confidence >= 40 ? 'text-amber-400' : 'text-red-400'}`}>
                    {current.confidence}%
                  </div>
                </div>
              </div>

              {/* Diagram */}
              <div className="card bg-surface-800 p-6 min-h-[400px] flex flex-col">
                <MermaidViewer code={current.mermaid_code} className="flex-1" />
              </div>

              {/* Explanation */}
              <div className="card">
                <h3 className="font-semibold text-white mb-3">Analysis Explanation</h3>
                <div className="prose prose-invert prose-sm max-w-none text-slate-300" dangerouslySetInnerHTML={{ __html: current.explanation }} />
              </div>

              <div className="p-3 bg-surface-800 rounded-lg border border-surface-600 text-xs text-slate-400">
                {current.note}
              </div>
            </div>
          </div>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center gap-4 text-center">
            <Share2 className="w-12 h-12 text-surface-600" />
            <div>
              <p className="text-slate-300 font-medium">No architecture diagram yet</p>
              <p className="text-slate-500 text-sm mt-1">Generate one to visualize the codebase structure.</p>
            </div>
            <button onClick={handleGenerate} className="btn-primary mt-2">
              <Play className="w-4 h-4" /> Generate Diagram
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
