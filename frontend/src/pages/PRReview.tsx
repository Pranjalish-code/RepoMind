import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { GitPullRequest, Loader2, Play } from 'lucide-react'
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
    <div className="flex h-full w-full">
      {/* Left panel: Form & Results */}
      <div className="w-1/3 shrink-0 flex flex-col border-r border-surface-600 bg-surface-800">
        <div className="p-4 border-b border-surface-600">
          <form onSubmit={handleReview} className="flex gap-2">
            <div className="flex-1 relative">
              <GitPullRequest className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
              <input
                type="number"
                value={prNumber}
                onChange={e => setPrNumber(e.target.value)}
                placeholder="PR Number (e.g. 1)"
                className="input-field pl-9"
                disabled={loading}
              />
            </div>
            <button type="submit" className="btn-primary" disabled={!prNumber || loading}>
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
            </button>
          </form>
          {error && <p className="text-red-400 text-sm mt-2">{error}</p>}
        </div>

        <div className="flex-1 overflow-hidden">
          {loading ? (
            <div className="flex flex-col items-center justify-center h-full gap-3 text-slate-400">
              <Loader2 className="w-8 h-8 animate-spin text-brand-500" />
              <p className="text-sm">Analyzing PR #{prNumber}…</p>
            </div>
          ) : review ? (
            <ReviewPanel review={review} onFileClick={handleFileClick} />
          ) : (
            <div className="flex flex-col items-center justify-center h-full gap-3 text-slate-500 text-center px-6">
              <GitPullRequest className="w-12 h-12 text-surface-600" />
              <p className="text-sm">Enter a Pull Request number to run an AI review.</p>
            </div>
          )}
        </div>
      </div>

      {/* Right panel: Diff Viewer */}
      <div className="flex-1 bg-surface-900 min-w-0">
        {selectedFile ? (
          <DiffViewer
            filePath={selectedFile}
            original={`// Mock original content for ${selectedFile}\n\nfunction example() {\n  return false;\n}`}
            modified={`// Mock modified content for ${selectedFile}\n\nfunction example() {\n  return true;\n}`}
          />
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-center gap-3 text-slate-500">
            <GitPullRequest className="w-10 h-10 text-surface-600" />
            <p className="text-sm">Select a file from the review to view its diff</p>
          </div>
        )}
      </div>
    </div>
  )
}
