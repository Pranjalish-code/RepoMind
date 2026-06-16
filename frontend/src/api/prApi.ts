import { API_BASE } from './repoApi'

// ── Types ─────────────────────────────────────────────────────────────────────

export interface PRListItem {
  number: number
  title: string
  state: string
  draft: boolean
  user_login: string
  head_ref: string
  base_ref: string
  html_url: string
  created_at: string
  updated_at: string
  body_preview: string
  additions: number
  deletions: number
  changed_files: number
}

export interface PRListResponse {
  repo_id: string
  repo_url: string
  state_filter: string
  page: number
  per_page: number
  pull_requests: PRListItem[]
  total: number
}

export interface PRReviewIssue {
  title: string
  file: string
  line: number | null
  severity: 'Low' | 'Medium' | 'High'
  evidence: string
  problem: string
  impact: string
  suggested_fix: string
}

export interface PRReviewResult {
  repo_id: string
  pr_number: number
  status: 'Safe to merge' | 'Needs changes' | 'Risky PR'
  risk_score: number
  summary: string
  issues: PRReviewIssue[]
  issue_count: number
  severity_counts: { High: number; Medium: number; Low: number }
  final_recommendation: string
  formatted_review: string
  review_db_id: string | null
}

// ── API calls ─────────────────────────────────────────────────────────────────

/** List pull requests for a repository */
export async function listPRs(
  repoId: string,
  state: 'open' | 'closed' | 'all' = 'open',
  page = 1,
  perPage = 30,
): Promise<PRListResponse> {
  const res = await fetch(
    `${API_BASE}/repos/${repoId}/pulls?state=${state}&page=${page}&per_page=${perPage}`
  )
  if (!res.ok) {
    const body = await res.text()
    throw new Error(body || `HTTP ${res.status}`)
  }
  return res.json()
}

/** Run AI review on a pull request */
export async function reviewPR(
  repoId: string,
  prNumber: number,
  query = 'Review this PR for bugs, security issues, and breaking changes.',
): Promise<PRReviewResult> {
  const res = await fetch(`${API_BASE}/repos/${repoId}/pulls/${prNumber}/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query }),
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(body || `HTTP ${res.status}`)
  }
  return res.json()
}
