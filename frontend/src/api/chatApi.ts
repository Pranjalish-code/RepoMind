import { API_BASE } from './repoApi'

// ── Types ─────────────────────────────────────────────────────────────────────

export interface Citation {
  file_path: string
  start_line: number
  end_line: number
  symbol_name: string | null
  score: number
}

export interface SSETokenEvent {
  type: 'token'
  content: string
}

export interface SSEMetadataEvent {
  type: 'metadata'
  intent: string
  related_files: string[]
  guardrail: {
    input_passed: boolean
    output_passed: boolean
    redactions: number
  }
}

export interface SSECitationsEvent {
  type: 'citations'
  citations: Citation[]
}

export interface SSEDoneEvent {
  type: 'done'
  message_id: string
}

export interface SSEErrorEvent {
  type: 'error'
  detail: string
}

export type SSEEvent =
  | SSETokenEvent
  | SSEMetadataEvent
  | SSECitationsEvent
  | SSEDoneEvent
  | SSEErrorEvent

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations: Citation[] | null
  created_at: string
}

// ── Stream chat ───────────────────────────────────────────────────────────────

/**
 * Opens an SSE stream to POST /chat/stream and calls handlers for each event.
 * Returns a cleanup function to abort the stream.
 */
export function streamChat(
  repoId: string,
  query: string,
  handlers: {
    onToken: (chunk: string) => void
    onMetadata: (evt: SSEMetadataEvent) => void
    onCitations: (citations: Citation[]) => void
    onDone: (messageId: string) => void
    onError: (detail: string) => void
  }
): () => void {
  const controller = new AbortController()

  ;(async () => {
    try {
      const res = await fetch(`${API_BASE}/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo_id: repoId, query, user_id: '' }),
        signal: controller.signal,
      })

      if (!res.ok) {
        handlers.onError(`Server error: HTTP ${res.status}`)
        return
      }

      const reader = res.body?.getReader()
      if (!reader) {
        handlers.onError('No response body')
        return
      }

      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          try {
            const evt = JSON.parse(line.slice(6)) as SSEEvent
            switch (evt.type) {
              case 'token':
                handlers.onToken(evt.content)
                break
              case 'metadata':
                handlers.onMetadata(evt)
                break
              case 'citations':
                handlers.onCitations(evt.citations)
                break
              case 'done':
                handlers.onDone(evt.message_id)
                break
              case 'error':
                handlers.onError(evt.detail)
                break
            }
          } catch {
            // malformed JSON — skip
          }
        }
      }
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        handlers.onError((err as Error).message)
      }
    }
  })()

  return () => controller.abort()
}

/** Get chat history for a repository */
export async function getChatHistory(repoId: string): Promise<ChatMessage[]> {
  const res = await fetch(`${API_BASE}/chat/history/${repoId}`)
  if (!res.ok) return []
  return res.json()
}
