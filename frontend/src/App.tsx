import { BrowserRouter, Routes, Route, NavLink, useParams } from 'react-router-dom'
import { Bot, LayoutDashboard, GitPullRequest, Share2, Code2 } from 'lucide-react'
import Dashboard from './pages/Dashboard'
import RepoChat from './pages/RepoChat'
import PRReview from './pages/PRReview'
import Architecture from './pages/Architecture'

function Sidebar() {
  const { repoId } = useParams<{ repoId?: string }>()

  return (
    <aside className="w-60 shrink-0 bg-surface-800 border-r border-surface-600 flex flex-col">
      {/* Logo */}
      <div className="px-4 py-5 border-b border-surface-600">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-brand-600 flex items-center justify-center">
            <Bot className="w-4 h-4 text-white" />
          </div>
          <div>
            <p className="text-white font-semibold text-sm leading-tight">RepoMind AI</p>
            <p className="text-slate-500 text-xs">Codebase Intelligence</p>
          </div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
        <NavLink to="/" end className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
          <LayoutDashboard className="w-4 h-4" />
          Dashboard
        </NavLink>

        {repoId && (
          <>
            <div className="pt-3 pb-1 px-1">
              <p className="text-xs font-medium text-slate-500 uppercase tracking-wider">Repository</p>
            </div>
            <NavLink
              to={`/repo/${repoId}/chat`}
              className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
            >
              <Code2 className="w-4 h-4" />
              Repo Chat
            </NavLink>
            <NavLink
              to={`/repo/${repoId}/pr-review`}
              className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
            >
              <GitPullRequest className="w-4 h-4" />
              PR Review
            </NavLink>
            <NavLink
              to={`/repo/${repoId}/architecture`}
              className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
            >
              <Share2 className="w-4 h-4" />
              Architecture
            </NavLink>
          </>
        )}
      </nav>

      {/* Footer */}
      <div className="px-4 py-3 border-t border-surface-600">
        <p className="text-xs text-slate-600">v0.1.0 — MVP</p>
      </div>
    </aside>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/repo/:repoId/*" element={
          <div className="flex h-full">
            <RepoLayoutWrapper />
          </div>
        } />
      </Routes>
    </BrowserRouter>
  )
}

function RepoLayoutWrapper() {
  return (
    <div className="flex h-full w-full">
      <Sidebar />
      <main className="flex-1 overflow-hidden">
        <Routes>
          <Route path="chat" element={<RepoChat />} />
          <Route path="pr-review" element={<PRReview />} />
          <Route path="architecture" element={<Architecture />} />
        </Routes>
      </main>
    </div>
  )
}
