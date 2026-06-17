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
      <span className="flex items-center gap-1.5 px-3 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-[13px] font-medium shadow-sm">
        <ShieldCheck className="w-4 h-4" />
        Safe to merge
      </span>
    )
  }
  if (status === 'Needs changes') {
    return (
      <span className="flex items-center gap-1.5 px-3 py-1 rounded-full bg-amber-500/10 border border-amber-500/20 text-amber-400 text-[13px] font-medium shadow-sm">
        <ShieldAlert className="w-4 h-4" />
        Needs changes
      </span>
    )
  }
  return (
    <span className="flex items-center gap-1.5 px-3 py-1 rounded-full bg-rose-500/10 border border-rose-500/20 text-rose-400 text-[13px] font-medium shadow-sm">
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
    <div className="card !p-0 overflow-hidden border-surface-800 bg-surface-900/50 hover:bg-surface-800 transition-colors shadow-sm">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-4 py-3 text-left transition-colors"
      >
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-8 h-8 rounded-full bg-surface-800 flex items-center justify-center shrink-0 border border-surface-700/50 shadow-inner">
            <AlertTriangle className="w-4 h-4 text-amber-400" />
          </div>
          <div className="flex flex-col min-w-0">
            <span className="text-[14px] font-medium text-slate-200 truncate">{issue.title}</span>
            <div className="flex items-center gap-2 mt-1">
              <SeverityBadge severity={issue.severity} />
            </div>
          </div>
        </div>
        {open ? <ChevronUp className="w-4 h-4 text-slate-500 shrink-0 ml-2" /> : <ChevronDown className="w-4 h-4 text-slate-500 shrink-0 ml-2" />}
      </button>

      {open && (
        <div className="px-5 pb-5 space-y-4 border-t border-surface-800 bg-surface-900/30 pt-4">
          {/* File location */}
          <div className="flex items-center gap-2">
            <button
              onClick={() => onFileClick?.(issue.file, issue.line)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-surface-800 border border-surface-700 hover:border-brand-500/50 hover:bg-surface-700
                         text-[12px] font-mono text-brand-300 transition-all shadow-sm"
            >
              {issue.file}{issue.line ? `:${issue.line}` : ''}
            </button>
          </div>

          <div className="grid grid-cols-1 gap-3 text-[13px] leading-relaxed">
            <div className="bg-surface-800/50 rounded-lg p-3 border border-surface-700/50">
              <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1.5">Problem</p>
              <p className="text-slate-300">{issue.problem}</p>
            </div>
            
            {issue.evidence && (
              <div>
                <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1.5 pl-1">Evidence</p>
                <div className="bg-surface-950 border border-surface-800 rounded-lg p-3 overflow-x-auto custom-scrollbar">
                  <p className="text-slate-400 text-[12px] font-mono whitespace-pre-wrap">{issue.evidence}</p>
                </div>
              </div>
            )}
            
            <div className="bg-surface-800/50 rounded-lg p-3 border border-surface-700/50">
              <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1.5">Impact</p>
              <p className="text-slate-300">{issue.impact}</p>
            </div>
            
            {issue.suggested_fix && (
              <div className="bg-emerald-500/5 border border-emerald-500/10 rounded-lg p-3">
                <p className="text-[11px] font-semibold text-emerald-500 uppercase tracking-wider mb-1.5">Suggested fix</p>
                <p className="text-slate-300 text-[13px]">{issue.suggested_fix}</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export default function ReviewPanel({ review, onFileClick }: ReviewPanelProps) {
  const riskColor = review.risk_score >= 70 ? 'bg-rose-500' : review.risk_score >= 40 ? 'bg-amber-500' : 'bg-emerald-500'

  return (
    <div className="space-y-5 overflow-y-auto h-full px-5 py-6 custom-scrollbar animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <div>
          <h3 className="text-xl font-bold text-slate-100 tracking-tight">PR #{review.pr_number} Review</h3>
          <p className="text-slate-400 text-[13px] mt-1">{review.summary}</p>
        </div>
        <StatusBadge status={review.status} />
      </div>

      {/* Risk score */}
      <div className="glass-panel p-4">
        <div className="flex items-center justify-between mb-3">
          <span className="text-[14px] font-medium text-slate-300 tracking-wide">Risk Score</span>
          <span className={`text-2xl font-bold ${review.risk_score >= 70 ? 'text-rose-400' : review.risk_score >= 40 ? 'text-amber-400' : 'text-emerald-400'}`}>
            {review.risk_score}/100
          </span>
        </div>
        <div className="h-2.5 rounded-full bg-surface-800 overflow-hidden shadow-inner border border-surface-700/50">
          <div
            className={`h-full rounded-full transition-all duration-1000 ease-out ${riskColor}`}
            style={{ width: `${review.risk_score}%` }}
          />
        </div>

        {/* Severity counts */}
        <div className="flex gap-2.5 mt-4">
          <div className="flex items-center">
            <span className="badge-red badge px-2.5 py-1">{review.severity_counts.High} High</span>
          </div>
          <div className="flex items-center">
            <span className="badge-yellow badge px-2.5 py-1">{review.severity_counts.Medium} Medium</span>
          </div>
          <div className="flex items-center">
            <span className="badge-blue badge px-2.5 py-1">{review.severity_counts.Low} Low</span>
          </div>
        </div>
      </div>

      {/* Recommendation */}
      <div className="glass-panel border-brand-500/20 bg-gradient-to-br from-brand-500/5 to-transparent p-4">
        <div className="flex items-center gap-2 mb-2">
          <div className="w-2 h-2 rounded-full bg-brand-500 animate-pulse" />
          <p className="text-[12px] font-semibold tracking-wider text-brand-400 uppercase">AI Recommendation</p>
        </div>
        <p className="text-[14px] text-slate-200 leading-relaxed">{review.final_recommendation}</p>
      </div>

      {/* Issues */}
      {review.issues.length > 0 && (
        <div className="space-y-3 pt-2">
          <h4 className="text-[14px] font-semibold text-slate-200 tracking-wide flex items-center justify-between">
            Detected Issues
            <span className="bg-surface-800 text-slate-400 py-0.5 px-2 rounded text-xs font-medium">{review.issue_count}</span>
          </h4>
          {review.issues.map((issue, i) => (
            <IssueCard key={i} issue={issue} onFileClick={onFileClick} />
          ))}
        </div>
      )}

      {review.issues.length === 0 && (
        <div className="flex flex-col items-center justify-center gap-3 py-10 text-center glass-panel">
          <div className="w-16 h-16 rounded-full bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center mb-2">
            <ShieldCheck className="w-8 h-8 text-emerald-400" />
          </div>
          <div>
            <p className="text-emerald-300 font-semibold text-base">No issues detected</p>
            <p className="text-slate-400 text-[13px] mt-1">This PR looks clean and safe to merge.</p>
          </div>
        </div>
      )}
    </div>
  )
}
