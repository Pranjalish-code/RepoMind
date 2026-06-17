import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { GitPullRequest, Loader2, Play, SearchCode } from 'lucide-react'
import { reviewPR, PRReviewResult } from '../api/prApi'
import ReviewPanel from '../components/ReviewPanel'
import DiffViewer from '../components/DiffViewer'

export default function PRReview() {
  const { repoId } = useParams<{ repoId: string }>()
  const [prNumber, setPrNumber] = useState('')
  const [loading, setLoading] = useState(false)
  const [review, setReview] = useState<PRReviewResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Viewer state
  const [selectedFile, setSelectedFile] = useState<string | null>(null)

  const handleReview = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!prNumber || !repoId || loading) return

    setLoading(true)
    setError(null)
    setReview(null)
    setSelectedFile(null)

    try {
      const num = parseInt(prNumber, 10)
      if (isNaN(num)) throw new Error('PR number must be an integer')
      const result = await reviewPR(repoId, num)
      setReview(result)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  const handleFileClick = (file: string) => {
    setSelectedFile(file)
  }

  return (
    <div className="flex h-full w-full bg-surface-950">
      {/* Left panel: Form & Results */}
      <div className="w-[380px] shrink-0 flex flex-col border-r border-surface-800 bg-surface-900/50">
        <div className="p-5 border-b border-surface-800 bg-surface-900/30 backdrop-blur">
          <form onSubmit={handleReview} className="flex gap-3">
            <div className="flex-1 relative">
              <GitPullRequest className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4.5 h-4.5 text-slate-500" />
              <input
                type="number"
                value={prNumber}
                onChange={e => setPrNumber(e.target.value)}
                placeholder="PR Number (e.g. 1)"
                className="w-full bg-surface-950 border border-surface-700/50 text-slate-200 placeholder-slate-500 py-2.5 pl-10 pr-3 rounded-lg focus:outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500 text-[13px] transition-all shadow-sm"
                disabled={loading}
              />
            </div>
            <button type="submit" className="btn-primary py-2 px-4 shadow-sm" disabled={!prNumber || loading}>
              {loading ? <Loader2 className="w-4.5 h-4.5 animate-spin" /> : <Play className="w-4.5 h-4.5" />}
            </button>
          </form>
          {error && <p className="text-rose-400 text-[13px] font-medium mt-3">{error}</p>}
        </div>

        <div className="flex-1 overflow-hidden custom-scrollbar">
          {loading ? (
            <div className="flex flex-col items-center justify-center h-full gap-4 text-slate-400 animate-fade-in">
              <Loader2 className="w-8 h-8 animate-spin text-brand-500" />
              <p className="text-[13px] font-medium tracking-wide">Analyzing PR #{prNumber}…</p>
            </div>
          ) : review ? (
            <ReviewPanel review={review} onFileClick={handleFileClick} />
          ) : (
            <div className="flex flex-col items-center justify-center h-full gap-4 text-slate-500 text-center px-8 animate-fade-in">
              <div className="w-16 h-16 rounded-full bg-surface-800 border border-surface-700 flex items-center justify-center shadow-inner">
                <GitPullRequest className="w-8 h-8 text-surface-400" />
              </div>
              <div>
                <h3 className="text-slate-200 font-medium tracking-wide">AI PR Review</h3>
                <p className="text-[13px] mt-1.5 leading-relaxed">Enter a Pull Request number above to run an automated AI review and identify risks.</p>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Right panel: Diff Viewer */}
      <div className="flex-1 bg-surface-950 min-w-0">
        {selectedFile ? (
          <DiffViewer
            filePath={selectedFile}
            original={`// Mock original content for ${selectedFile}\n\nfunction example() {\n  return false;\n}`}
            modified={`// Mock modified content for ${selectedFile}\n\nfunction example() {\n  return true;\n}`}
          />
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-center gap-4 bg-surface-950 animate-fade-in">
            <div className="w-16 h-16 rounded-full bg-surface-900 border border-surface-800 flex items-center justify-center shadow-inner">
              <SearchCode className="w-8 h-8 text-surface-500" />
            </div>
            <div>
              <h3 className="text-slate-200 font-medium tracking-wide">No file selected</h3>
              <p className="text-slate-500 text-[13px] mt-1">Select a file from the review panel to view its diff.</p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
