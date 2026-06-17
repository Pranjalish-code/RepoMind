import { useState, useEffect } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Github, Loader2, Search, CheckCircle2, AlertCircle, Trash2, RefreshCw, Plus, ArrowRight, Code2 } from 'lucide-react'
import {
  importRepo,
  listRepos,
  indexRepo,
  deleteRepo,
  Repository
} from '../api/repoApi'

export default function Dashboard() {
  const [repoUrl, setRepoUrl] = useState('')
  const [repos, setRepos] = useState<Repository[]>([])
  const [loading, setLoading] = useState(true)
  const [importing, setImporting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()

  const fetchRepos = async () => {
    try {
      const data = await listRepos()
      setRepos(data)
    } catch (err) {
      console.error(err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchRepos()
    const interval = setInterval(fetchRepos, 5000)
    return () => clearInterval(interval)
  }, [])

  const handleImport = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!repoUrl.trim() || importing) return

    setImporting(true)
    setError(null)
    try {
      await importRepo({ repo_url: repoUrl.trim() })
      setRepoUrl('')
      await fetchRepos()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setImporting(false)
    }
  }

  const handleIndex = async (repoId: string) => {
    try {
      await indexRepo(repoId)
      await fetchRepos()
    } catch (err) {
      alert((err as Error).message)
    }
  }

  const handleDelete = async (repoId: string, repoName: string) => {
    const confirmed = window.confirm(
      `Delete repository "${repoName}"?\n\nThis will remove:\n- Local clone\n- Qdrant vectors\n- Database records`
    )

    if (!confirmed) return

    try {
      await deleteRepo(repoId)
      await fetchRepos()
    } catch (err) {
      alert((err as Error).message)
    }
  }

  return (
    <div className="flex-1 overflow-y-auto p-8 max-w-6xl mx-auto w-full animate-fade-in">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-slate-100 mb-2 tracking-tight">Repositories</h1>
          <p className="text-slate-400 font-medium">Manage and index your GitHub workspaces.</p>
        </div>
      </div>

      {/* Import Form */}
      <div className="glass-panel mb-10 p-2">
        <form onSubmit={handleImport} className="flex items-center gap-3">
          <div className="flex-1 relative">
            <Github className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-500" />
            <input
              type="text"
              value={repoUrl}
              onChange={e => setRepoUrl(e.target.value)}
              placeholder="Paste GitHub repository URL..."
              className="w-full bg-transparent border-none text-slate-200 placeholder-slate-500 py-3 pl-12 pr-4 focus:outline-none focus:ring-0 text-sm"
              disabled={importing}
            />
          </div>
          <button type="submit" className="btn-primary py-2.5 px-6" disabled={!repoUrl.trim() || importing}>
            {importing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
            {importing ? 'Importing...' : 'Add Repository'}
          </button>
        </form>
        {error && <div className="px-4 pb-3"><p className="text-rose-400 text-sm font-medium">{error}</p></div>}
      </div>

      {/* Repo List */}
      <div className="space-y-4">
        {loading && repos.length === 0 ? (
          <div className="flex justify-center items-center py-20">
            <Loader2 className="w-8 h-8 animate-spin text-brand-500" />
          </div>
        ) : repos.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 px-4 text-center card border-dashed border-surface-700 bg-surface-900/50">
            <div className="w-16 h-16 bg-surface-800 rounded-full flex items-center justify-center mb-4 border border-surface-700 shadow-sm">
              <Search className="w-8 h-8 text-slate-400" />
            </div>
            <h3 className="text-xl font-semibold text-slate-200 mb-2">No repositories indexed</h3>
            <p className="text-slate-400 max-w-sm mb-6">Import your first GitHub repository above to start analyzing code, reviewing PRs, and chatting with the codebase.</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
            {repos.map(repo => (
              <div key={repo.id} className="card hover:border-surface-600 transition-all duration-300 flex flex-col group hover:shadow-lg hover:-translate-y-0.5">
                <div className="flex items-start justify-between mb-4">
                  <div className="flex items-center gap-3 w-full">
                    <div className="w-10 h-10 shrink-0 rounded-lg bg-surface-800 border border-surface-700 flex items-center justify-center shadow-sm text-brand-400">
                      <Code2 className="w-5 h-5" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <Link to={`/repo/${repo.id}/chat`} className="text-base font-semibold text-slate-200 group-hover:text-brand-400 transition-colors block truncate">
                        {repo.repo_name}
                      </Link>
                      <span className="text-slate-500 font-mono text-xs truncate block mt-0.5">{repo.repo_url.replace('https://github.com/', '')}</span>
                    </div>
                  </div>
                </div>

                <div className="mt-auto pt-4 border-t border-surface-800/50 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    {repo.status === 'ready' && <span className="badge badge-green"><CheckCircle2 className="w-3 h-3 mr-1" /> Ready</span>}
                    {repo.status === 'indexing' && <span className="badge badge-yellow"><Loader2 className="w-3 h-3 mr-1 animate-spin" /> Indexing</span>}
                    {repo.status === 'error' && <span className="badge badge-red"><AlertCircle className="w-3 h-3 mr-1" /> Error</span>}
                  </div>

                  <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    {repo.status === 'ready' && (
                      <button
                        onClick={() => handleIndex(repo.id)}
                        className="p-1.5 text-slate-400 hover:text-brand-400 hover:bg-brand-500/10 rounded-md transition-colors"
                        title="Re-index"
                      >
                        <RefreshCw className="w-4 h-4" />
                      </button>
                    )}
                    <button
                      onClick={() => handleDelete(repo.id, repo.repo_name)}
                      className="p-1.5 text-slate-400 hover:text-rose-400 hover:bg-rose-500/10 rounded-md transition-colors"
                      title="Delete"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => navigate(`/repo/${repo.id}/chat`)}
                      className="p-1.5 text-slate-400 hover:text-emerald-400 hover:bg-emerald-500/10 rounded-md transition-colors"
                      title="Open"
                    >
                      <ArrowRight className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
