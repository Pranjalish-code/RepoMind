/** Base URL for API calls. In dev, Vite proxies /api → localhost:8000 */
export const API_BASE = '/api'

// ── Types ──────────────────────────────────────────────────────────────────────

export interface Repository {
  id: string
  repo_name: string
  repo_url: string
  status: 'pending' | 'cloning' | 'ready' | 'indexing' | 'error'
  local_path: string | null
  created_at: string
  indexed_files_count?: number
}

export interface IndexedFile {
  id: string
  file_path: string
  language: string
  chunk_count: number
}

export interface ImportRepoPayload {
  repo_url: string
}

export interface ImportRepoResponse {
  id: string
  repo_name: string
  status: string
  message: string
}

// ── API calls ─────────────────────────────────────────────────────────────────

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(body || `HTTP ${res.status}`)
  }
  return res.json() as Promise<T>
}

/** Import (clone) a GitHub repository */
export async function importRepo(payload: ImportRepoPayload): Promise<ImportRepoResponse> {
  return apiFetch<ImportRepoResponse>('/repos/import', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

/** List all imported repositories */
export async function listRepos(): Promise<Repository[]> {
  return apiFetch<Repository[]>('/repos')
}

/** Get a single repository with indexed files */
export async function getRepo(repoId: string): Promise<Repository & { indexed_files: IndexedFile[] }> {
  return apiFetch(`/repos/${repoId}`)
}

/** Trigger RAG indexing for a repository */
export async function indexRepo(
  repoId: string,
  force: boolean = true
): Promise<{ status: string; message: string }> {
  return apiFetch(`/repos/${repoId}/index`, {
    method: 'POST',
    body: JSON.stringify({ force }),
  })
}

/** List indexed files for a repository */
export async function listIndexedFiles(repoId: string): Promise<IndexedFile[]> {
  const repo = await getRepo(repoId)
  return repo.indexed_files ?? []
}
export interface RepoFileResponse {
  repo_id: string
  file_path: string
  language?: string
  content: string
}

export async function getRepoFile(
  repoId: string,
  path: string
): Promise<RepoFileResponse> {
  return apiFetch<RepoFileResponse>(
    `/repos/${repoId}/file?path=${encodeURIComponent(path)}`
  )
}