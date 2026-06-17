import { NavLink, useMatch } from 'react-router-dom'
import { Bot, LayoutDashboard, GitPullRequest, Share2, Code2 } from 'lucide-react'

export default function Sidebar() {
  const match = useMatch('/repo/:repoId/*')
  const repoId = match?.params.repoId

  return (
    <aside className="w-64 shrink-0 bg-surface-900 border-r border-surface-800 flex flex-col transition-all duration-300">
      {/* Logo */}
      <div className="px-5 py-5 border-b border-surface-800 flex items-center gap-3">
        <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-brand-500 to-brand-700 flex items-center justify-center shadow-lg shadow-brand-500/20">
          <Bot className="w-5 h-5 text-white" />
        </div>
        <div>
          <p className="text-slate-100 font-semibold text-sm tracking-wide">RepoMind AI</p>
          <p className="text-slate-500 text-xs font-medium">Codebase Intelligence</p>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-6 space-y-1 overflow-y-auto">
        <NavLink to="/" end className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
          <LayoutDashboard className="w-4 h-4 opacity-80" />
          Dashboard
        </NavLink>

        {repoId && (
          <div className="pt-6 pb-2 px-3">
            <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-2">Workspace</p>
            <div className="space-y-1 animate-fade-in">
              <NavLink to={`/repo/${repoId}/chat`} className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
                <Code2 className="w-4 h-4 opacity-80" />
                Repo Chat
              </NavLink>
              <NavLink to={`/repo/${repoId}/pr-review`} className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
                <GitPullRequest className="w-4 h-4 opacity-80" />
                PR Review
              </NavLink>
              <NavLink to={`/repo/${repoId}/architecture`} className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
                <Share2 className="w-4 h-4 opacity-80" />
                Architecture
              </NavLink>
            </div>
          </div>
        )}
      </nav>

      {/* Footer */}
      <div className="px-5 py-4 border-t border-surface-800 bg-surface-900/50">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.5)]"></div>
          <p className="text-xs text-slate-400 font-medium tracking-wide">System Online</p>
        </div>
      </div>
    </aside>
  )
}
