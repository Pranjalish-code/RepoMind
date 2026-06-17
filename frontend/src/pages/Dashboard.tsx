import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { Github, Loader2, Search, CheckCircle2, AlertCircle } from 'lucide-react'
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
    <div className="flex-1 overflow-y-auto p-8 max-w-5xl mx-auto w-full">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-white mb-2">Repositories</h1>
        <p className="text-slate-400">Import GitHub repositories to analyze and review.</p>
      </div>

      {/* Import Form */}
      <div className="card mb-8">
        <form onSubmit={handleImport} className="flex gap-3">
          <div className="flex-1 relative">
            <Github className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
            <input
              type="text"
              value={repoUrl}
              onChange={e => setRepoUrl(e.target.value)}
              placeholder="https://github.com/owner/repo"
              className="input-field pl-10"
              disabled={importing}
            />
          </div>
          <button type="submit" className="btn-primary" disabled={!repoUrl.trim() || importing}>
            {importing ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Import'}
          </button>
        </form>
        {error && <p className="text-red-400 text-sm mt-2">{error}</p>}
      </div>

      {/* Repo List */}
      <div className="space-y-4">
        {loading && repos.length === 0 ? (
          <div className="flex justify-center py-8">
            <Loader2 className="w-6 h-6 animate-spin text-brand-500" />
          </div>
        ) : repos.length === 0 ? (
          <div className="text-center py-12 card border-dashed">
            <Search className="w-8 h-8 text-slate-500 mx-auto mb-3" />
            <h3 className="text-white font-medium">No repositories yet</h3>
            <p className="text-slate-400 text-sm mt-1">Import a repository to get started.</p>
          </div>
        ) : (
          repos.map(repo => (
            <div key={repo.id} className="card hover:border-surface-500 transition-colors flex items-center justify-between group">
              <div>
                <Link to={`/repo/${repo.id}/chat`} className="text-lg font-medium text-brand-300 hover:text-brand-200">
                  {repo.repo_name}
                </Link>
                <div className="flex items-center gap-4 mt-1.5 text-sm">
                  <span className="text-slate-500 font-mono text-xs">{repo.repo_url}</span>
                  <div className="flex items-center gap-1.5">
                    {repo.status === 'ready' && <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />}
                    {repo.status === 'indexing' && <Loader2 className="w-3.5 h-3.5 text-brand-400 animate-spin" />}
                    {repo.status === 'error' && <AlertCircle className="w-3.5 h-3.5 text-red-400" />}
                    <span className="text-slate-400 capitalize">{repo.status}</span>
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                {repo.status === 'ready' && (
                  <button
                    onClick={() => handleIndex(repo.id)}
                    className="btn-ghost text-xs"
                  >
                    Re-index
                  </button>
                )}

                <button
                  onClick={() => handleDelete(repo.id, repo.repo_name)}
                  className="px-3 py-2 rounded-md text-xs bg-red-600 hover:bg-red-700 text-white"
                >
                  Delete
                </button>

                <Link
                  to={`/repo/${repo.id}/chat`}
                  className="btn-primary"
                >
                  Open
                </Link>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
