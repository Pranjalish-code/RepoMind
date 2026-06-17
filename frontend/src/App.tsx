import { BrowserRouter, Routes, Route } from 'react-router-dom'
import Dashboard from './pages/Dashboard'
import RepoChat from './pages/RepoChat'
import PRReview from './pages/PRReview'
import Architecture from './pages/Architecture'
import Sidebar from './components/Sidebar'
import Header from './components/Header'

export default function App() {
  return (
    <BrowserRouter>
      <div className="flex h-screen w-full overflow-hidden bg-surface-950 text-slate-200">
        <Sidebar />
        <div className="flex-1 flex flex-col min-w-0 bg-surface-950">
          <Header />
          <main className="flex-1 overflow-hidden relative">
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/repo/:repoId/*" element={
                <div className="h-full w-full">
                  <Routes>
                    <Route path="chat" element={<RepoChat />} />
                    <Route path="pr-review" element={<PRReview />} />
                    <Route path="architecture" element={<Architecture />} />
                  </Routes>
                </div>
              } />
            </Routes>
          </main>
        </div>
      </div>
    </BrowserRouter>
  )
}
