import { AlertTriangle, ShieldCheck, ShieldAlert, ShieldX, ChevronDown, ChevronUp } from 'lucide-react'
import { useState } from 'react'
import { PRReviewResult, PRReviewIssue } from '../api/prApi'

interface ReviewPanelProps {
  review: PRReviewResult
  onFileClick?: (file: string, line: number | null) => void
}

function StatusBadge({ status }: { status: PRReviewResult['status'] }) {
  if (status === 'Safe to merge') {
    return (
      <span className="flex items-center gap-1.5 px-3 py-1 rounded-full bg-emerald-900/40 border border-emerald-600/40 text-emerald-300 text-sm font-medium">
        <ShieldCheck className="w-4 h-4" />
        Safe to merge
      </span>
    )
  }
  if (status === 'Needs changes') {
    return (
      <span className="flex items-center gap-1.5 px-3 py-1 rounded-full bg-amber-900/40 border border-amber-600/40 text-amber-300 text-sm font-medium">
        <ShieldAlert className="w-4 h-4" />
        Needs changes
      </span>
    )
  }
  return (
    <span className="flex items-center gap-1.5 px-3 py-1 rounded-full bg-red-900/40 border border-red-600/40 text-red-300 text-sm font-medium">
      <ShieldX className="w-4 h-4" />
      Risky PR
    </span>
  )
}

function SeverityBadge({ severity }: { severity: PRReviewIssue['severity'] }) {
  const cls = {
    High: 'badge-red',
    Medium: 'badge-yellow',
    Low: 'badge-blue',
  }[severity]
  return <span className={`badge ${cls}`}>{severity}</span>
}

function IssueCard({ issue, onFileClick }: { issue: PRReviewIssue; onFileClick?: ReviewPanelProps['onFileClick'] }) {
  const [open, setOpen] = useState(false)

  return (
    <div className="card !p-0 overflow-hidden">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-surface-700/50 transition-colors"
      >
        <div className="flex items-center gap-2 min-w-0">
          <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" />
          <span className="text-sm font-medium text-slate-200 truncate">{issue.title}</span>
          <SeverityBadge severity={issue.severity} />
        </div>
        {open ? <ChevronUp className="w-4 h-4 text-slate-500 shrink-0" /> : <ChevronDown className="w-4 h-4 text-slate-500 shrink-0" />}
      </button>

      {open && (
        <div className="px-4 pb-4 space-y-3 border-t border-surface-600">
          {/* File location */}
          <div className="flex items-center gap-2 mt-3">
            <button
              onClick={() => onFileClick?.(issue.file, issue.line)}
              className="flex items-center gap-1.5 px-2 py-1 rounded bg-surface-700 hover:bg-surface-600
                         text-xs font-mono text-slate-300 hover:text-white transition-colors"
            >
              {issue.file}{issue.line ? `:${issue.line}` : ''}
            </button>
          </div>

          <div className="grid grid-cols-1 gap-2 text-sm">
            <div>
              <p className="text-xs font-medium text-slate-400 mb-0.5">Problem</p>
              <p className="text-slate-300">{issue.problem}</p>
            </div>
            <div>
              <p className="text-xs font-medium text-slate-400 mb-0.5">Evidence</p>
              <p className="text-slate-400 text-xs font-mono bg-surface-700 rounded px-2 py-1.5">{issue.evidence}</p>
            </div>
            <div>
              <p className="text-xs font-medium text-slate-400 mb-0.5">Impact</p>
              <p className="text-slate-300">{issue.impact}</p>
            </div>
            {issue.suggested_fix && (
              <div>
                <p className="text-xs font-medium text-emerald-400 mb-0.5">Suggested fix</p>
                <p className="text-slate-300 text-sm">{issue.suggested_fix}</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export default function ReviewPanel({ review, onFileClick }: ReviewPanelProps) {
  const riskColor = review.risk_score >= 70 ? 'bg-red-500' : review.risk_score >= 40 ? 'bg-amber-500' : 'bg-emerald-500'

  return (
    <div className="space-y-4 overflow-y-auto h-full px-4 py-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-white font-semibold">PR #{review.pr_number} Review</h3>
          <p className="text-slate-400 text-sm mt-0.5">{review.summary}</p>
        </div>
        <StatusBadge status={review.status} />
      </div>

      {/* Risk score */}
      <div className="card">
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm font-medium text-slate-300">Risk Score</span>
          <span className={`text-lg font-bold ${review.risk_score >= 70 ? 'text-red-400' : review.risk_score >= 40 ? 'text-amber-400' : 'text-emerald-400'}`}>
            {review.risk_score}/100
          </span>
        </div>
        <div className="h-2 rounded-full bg-surface-600 overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-700 ${riskColor}`}
            style={{ width: `${review.risk_score}%` }}
          />
        </div>

        {/* Severity counts */}
        <div className="flex gap-3 mt-3">
          <div className="flex items-center gap-1">
            <span className="badge-red badge">{review.severity_counts.High} High</span>
          </div>
          <div className="flex items-center gap-1">
            <span className="badge-yellow badge">{review.severity_counts.Medium} Medium</span>
          </div>
          <div className="flex items-center gap-1">
            <span className="badge-blue badge">{review.severity_counts.Low} Low</span>
          </div>
        </div>
      </div>

      {/* Recommendation */}
      <div className="card border-brand-700/40">
        <p className="text-xs font-medium text-brand-400 mb-1">AI Recommendation</p>
        <p className="text-sm text-slate-300">{review.final_recommendation}</p>
      </div>

      {/* Issues */}
      {review.issues.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-sm font-medium text-slate-300">
            Issues ({review.issue_count})
          </h4>
          {review.issues.map((issue, i) => (
            <IssueCard key={i} issue={issue} onFileClick={onFileClick} />
          ))}
        </div>
      )}

      {review.issues.length === 0 && (
        <div className="flex flex-col items-center gap-2 py-6 text-center">
          <ShieldCheck className="w-8 h-8 text-emerald-400" />
          <p className="text-emerald-300 font-medium text-sm">No issues detected</p>
          <p className="text-slate-500 text-xs">This PR looks clean.</p>
        </div>
      )}
    </div>
  )
}
