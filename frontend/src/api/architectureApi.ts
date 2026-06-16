import { API_BASE } from './repoApi'

// ── Types ─────────────────────────────────────────────────────────────────────

export interface DetectedComponent {
  name: string
  kind: string
  label: string
  evidence: string[]
}

export interface ArchitectureDiagramResponse {
  repo_id: string
  repo_name: string
  confidence: number
  mermaid_code: string
  explanation: string
  formatted_output: string
  detected_components: DetectedComponent[]
  component_count: number
  diagram_db_id: string | null
  note: string
}

export interface ArchitectureListItem {
  id: string
  repo_id: string
  confidence_score: number | null
  component_count: number
  created_at: string
  mermaid_preview: string
}

// ── API calls ─────────────────────────────────────────────────────────────────

/** Generate an architecture diagram for a repository */
export async function generateArchitecture(
  repoId: string,
  query = 'Generate an architecture diagram for this repository.',
): Promise<ArchitectureDiagramResponse> {
  const res = await fetch(`${API_BASE}/repos/${repoId}/architecture/generate`, {
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

/** List previously generated architecture diagrams */
export async function listArchitectures(
  repoId: string,
  skip = 0,
  limit = 10,
): Promise<ArchitectureListItem[]> {
  const res = await fetch(
    `${API_BASE}/repos/${repoId}/architecture?skip=${skip}&limit=${limit}`
  )
  if (!res.ok) return []
  return res.json()
}
