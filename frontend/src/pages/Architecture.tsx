import { useState, useEffect } from 'react'
import { useParams } from 'react-router-dom'
import { Share2, Loader2, Play, Waypoints } from 'lucide-react'
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
    <div className="flex h-full w-full bg-surface-950">
      {/* Sidebar: History */}
      <div className="w-64 shrink-0 border-r border-surface-800 bg-surface-900/50 flex flex-col">
        <div className="p-4 border-b border-surface-800 bg-surface-900/30 backdrop-blur">
          <button
            onClick={handleGenerate}
            disabled={loading}
            className="btn-primary w-full justify-center py-2 shadow-sm"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
            Generate New
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-3 space-y-2 custom-scrollbar">
          {loadingHistory ? (
            <div className="flex justify-center p-6"><Loader2 className="w-5 h-5 animate-spin text-brand-500" /></div>
          ) : history.length === 0 ? (
            <div className="text-center py-8">
              <p className="text-[13px] text-slate-500 font-medium">No diagrams generated yet.</p>
            </div>
          ) : (
            history.map(h => (
              <button
                key={h.id}
                className={`w-full text-left p-3 rounded-lg transition-all duration-200 border
                  ${current?.diagram_db_id === h.id 
                    ? 'bg-brand-500/10 border-brand-500/30 shadow-[0_0_10px_rgba(99,102,241,0.05)]' 
                    : 'bg-surface-800/50 border-surface-700/50 hover:bg-surface-800 hover:border-surface-600 shadow-sm'}
                `}
                onClick={() => {
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
                <div className="flex items-center justify-between mb-1.5">
                  <span className={`text-[12px] font-semibold ${current?.diagram_db_id === h.id ? 'text-brand-300' : 'text-slate-300'}`}>
                    {new Date(h.created_at).toLocaleDateString()}
                  </span>
                  <span className={`text-[11px] font-bold px-1.5 py-0.5 rounded ${h.confidence_score && h.confidence_score >= 0.7 ? 'bg-emerald-500/10 text-emerald-400' : 'bg-amber-500/10 text-amber-400'}`}>
                    {h.confidence_score ? Math.round(h.confidence_score * 100) : 0}%
                  </span>
                </div>
                <p className="text-[12px] text-slate-500 truncate flex items-center gap-1.5">
                  <Waypoints className="w-3.5 h-3.5" />
                  {h.component_count} components
                </p>
              </button>
            ))
          )}
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 bg-surface-950 flex flex-col min-w-0">
        {error && (
          <div className="m-6 p-4 bg-rose-500/10 border border-rose-500/20 rounded-xl text-rose-300 text-[14px] shadow-sm animate-fade-in-up">
            <span className="font-semibold mr-2">Error:</span>{error}
          </div>
        )}

        {loading ? (
          <div className="flex-1 flex flex-col items-center justify-center gap-5 animate-fade-in">
            <div className="relative">
              <div className="absolute inset-0 bg-brand-500/20 blur-xl rounded-full" />
              <Loader2 className="w-12 h-12 animate-spin text-brand-500 relative z-10" />
            </div>
            <p className="text-slate-300 font-medium tracking-wide">Analyzing codebase & generating architecture…</p>
          </div>
        ) : current ? (
          <div className="flex-1 overflow-y-auto p-8 custom-scrollbar">
            <div className="max-w-5xl mx-auto space-y-6 animate-fade-in-up">
              {/* Header */}
              <div className="flex items-center justify-between glass-panel p-6 border-brand-500/20">
                <div className="flex items-center gap-4">
                  <div className="w-12 h-12 rounded-xl bg-brand-500/10 border border-brand-500/20 flex items-center justify-center">
                    <Waypoints className="w-6 h-6 text-brand-400" />
                  </div>
                  <div>
                    <h2 className="text-2xl font-bold text-slate-100 tracking-tight">Architecture Diagram</h2>
                    <p className="text-slate-400 text-[14px] mt-1">{current.component_count} components detected</p>
                  </div>
                </div>
                <div className="text-right bg-surface-950 px-4 py-2 rounded-lg border border-surface-800 shadow-inner">
                  <span className="text-[11px] font-semibold tracking-wider uppercase text-slate-500 block mb-0.5">Confidence</span>
                  <div className={`text-xl font-bold ${current.confidence >= 70 ? 'text-emerald-400' : current.confidence >= 40 ? 'text-amber-400' : 'text-rose-400'}`}>
                    {current.confidence}%
                  </div>
                </div>
              </div>

              {/* Diagram */}
              <div className="card !p-1 min-h-[500px] flex flex-col border-surface-700/50 shadow-lg">
                <div className="bg-surface-900 rounded-t-xl border-b border-surface-800 px-4 py-2.5 flex items-center gap-2">
                  <div className="flex gap-1.5">
                    <div className="w-3 h-3 rounded-full bg-surface-700" />
                    <div className="w-3 h-3 rounded-full bg-surface-700" />
                    <div className="w-3 h-3 rounded-full bg-surface-700" />
                  </div>
                  <span className="ml-2 text-[12px] font-mono text-slate-500">mermaid.js</span>
                </div>
                <div className="flex-1 bg-surface-800/30 p-6 flex flex-col rounded-b-xl">
                  <MermaidViewer code={current.mermaid_code} className="flex-1" />
                </div>
              </div>

              {/* Explanation */}
              <div className="card border-surface-700/50 shadow-md">
                <h3 className="text-lg font-semibold text-slate-200 mb-4 tracking-wide">Analysis Explanation</h3>
                <div className="prose prose-invert prose-sm max-w-none text-slate-300 leading-relaxed" dangerouslySetInnerHTML={{ __html: current.explanation }} />
              </div>

              {current.note && (
                <div className="px-4 py-3 bg-brand-500/5 rounded-lg border border-brand-500/10 text-[13px] text-brand-300/80 font-medium">
                  {current.note}
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center gap-5 text-center px-6 animate-fade-in">
            <div className="w-20 h-20 rounded-full bg-surface-900 border border-surface-800 flex items-center justify-center shadow-inner">
              <Share2 className="w-10 h-10 text-brand-400/50" />
            </div>
            <div>
              <p className="text-slate-200 font-semibold text-lg tracking-wide">No architecture diagram yet</p>
              <p className="text-slate-500 text-[14px] mt-2 max-w-md mx-auto leading-relaxed">
                Generate an AI-powered architecture diagram to visualize the codebase structure, dependencies, and core components.
              </p>
            </div>
            <button onClick={handleGenerate} className="btn-primary mt-4 py-2.5 px-6 shadow-sm">
              <Play className="w-4.5 h-4.5" /> Generate Diagram
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
