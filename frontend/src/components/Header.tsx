import { useLocation, useMatch } from 'react-router-dom'
import { ChevronRight, Github } from 'lucide-react'
import { useEffect, useState } from 'react'
import { getRepo, Repository } from '../api/repoApi'

export default function Header() {
  const match = useMatch('/repo/:repoId/*')
  const repoId = match?.params.repoId
  const location = useLocation()
  const [repo, setRepo] = useState<Repository | null>(null)

  useEffect(() => {
    if (repoId) {
      getRepo(repoId).then(setRepo).catch(console.error)
    } else {
      setRepo(null)
    }
  }, [repoId])

  let title = "Overview"
  if (location.pathname.includes('/chat')) title = "Repository Chat"
  if (location.pathname.includes('/pr-review')) title = "Pull Request Review"
  if (location.pathname.includes('/architecture')) title = "Architecture"

  return (
    <header className="h-14 border-b border-surface-800 bg-surface-900/80 backdrop-blur-md flex items-center justify-between px-6 shrink-0 z-10">
      <div className="flex items-center gap-2.5 text-sm text-slate-400">
        {repo ? (
          <>
            <span className="flex items-center gap-1.5 hover:text-slate-200 transition-colors cursor-pointer">
              <Github className="w-4 h-4" /> 
              {repo.repo_name}
            </span>
            <ChevronRight className="w-4 h-4 text-surface-600" />
            <span className="text-slate-200 font-medium tracking-wide">{title}</span>
          </>
        ) : (
          <span className="text-slate-200 font-medium tracking-wide">Dashboard</span>
        )}
      </div>
      <div className="flex items-center gap-3">
        {repo && repo.status === 'indexing' && (
          <span className="badge badge-yellow animate-pulse-slow shadow-sm shadow-amber-500/10">Indexing...</span>
        )}
        {repo && repo.status === 'ready' && (
          <span className="badge badge-green shadow-sm shadow-emerald-500/10">Indexed</span>
        )}
        {repo && repo.status === 'error' && (
          <span className="badge badge-red shadow-sm shadow-rose-500/10">Error</span>
        )}
      </div>
    </header>
  )
}
