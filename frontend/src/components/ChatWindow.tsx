import { useState, useRef, useEffect, useCallback } from 'react'
import { Send, Bot, User, Loader2 } from 'lucide-react'
import { streamChat, Citation, SSEMetadataEvent, ChatMessage } from '../api/chatApi'

interface ChatWindowProps {
  repoId: string
  history: ChatMessage[]
  onCitationClick?: (citation: Citation) => void
}

interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations?: Citation[]
  isStreaming?: boolean
}

export default function ChatWindow({ repoId, history, onCitationClick }: ChatWindowProps) {
  const [messages, setMessages] = useState<Message[]>(() =>
    history.map(m => ({ id: m.id, role: m.role, content: m.content, citations: m.citations ?? undefined }))
  )
  const [input, setInput] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const [_metadata, setMetadata] = useState<SSEMetadataEvent | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<(() => void) | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const send = useCallback(async () => {
    const query = input.trim()
    if (!query || isStreaming) return

    setInput('')
    setIsStreaming(true)

    // Add user message
    const userMsg: Message = { id: `u-${Date.now()}`, role: 'user', content: query }
    setMessages(prev => [...prev, userMsg])

    // Add placeholder assistant message
    const assistantId = `a-${Date.now()}`
    setMessages(prev => [...prev, { id: assistantId, role: 'assistant', content: '', isStreaming: true }])

    abortRef.current = streamChat(repoId, query, {
      onToken: (chunk) => {
        setMessages(prev =>
          prev.map(m =>
            m.id === assistantId ? { ...m, content: m.content + chunk } : m
          )
        )
      },
      onMetadata: (evt) => {
        setMetadata(evt)
      },
      onCitations: (citations) => {
        setMessages(prev =>
          prev.map(m => m.id === assistantId ? { ...m, citations } : m)
        )
      },
      onDone: (messageId) => {
        setMessages(prev =>
          prev.map(m =>
            m.id === assistantId ? { ...m, id: messageId, isStreaming: false } : m
          )
        )
        setIsStreaming(false)
      },
      onError: (detail) => {
        setMessages(prev =>
          prev.map(m =>
            m.id === assistantId
              ? { ...m, content: `⚠️ Error: ${detail}`, isStreaming: false }
              : m
          )
        )
        setIsStreaming(false)
      },
    })
  }, [input, isStreaming, repoId])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Message list */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center gap-4 animate-fade-in">
            <div className="w-14 h-14 rounded-2xl bg-brand-600/20 border border-brand-500/30 flex items-center justify-center">
              <Bot className="w-7 h-7 text-brand-400" />
            </div>
            <div>
              <h3 className="text-white font-semibold text-lg">Ask about your codebase</h3>
              <p className="text-slate-400 text-sm mt-1 max-w-xs">
                Ask anything — architecture, bugs, how a function works, or request a file review.
              </p>
            </div>
          </div>
        )}

        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex gap-3 animate-slide-up ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}
          >
            <div className={`w-7 h-7 rounded-full shrink-0 flex items-center justify-center text-xs font-bold
              ${msg.role === 'user'
                ? 'bg-brand-600 text-white'
                : 'bg-surface-600 border border-surface-500 text-slate-300'}`}
            >
              {msg.role === 'user' ? <User className="w-3.5 h-3.5" /> : <Bot className="w-3.5 h-3.5" />}
            </div>

            <div className={`max-w-[80%] ${msg.role === 'user' ? 'items-end' : 'items-start'} flex flex-col gap-1.5`}>
              <div className={`px-4 py-3 rounded-2xl text-sm leading-relaxed whitespace-pre-wrap
                ${msg.role === 'user'
                  ? 'bg-brand-600 text-white rounded-tr-sm'
                  : 'bg-surface-700 border border-surface-600 text-slate-200 rounded-tl-sm'
                }
                ${msg.isStreaming ? 'typing-cursor' : ''}`}
              >
                {msg.content || (msg.isStreaming ? '' : '…')}
              </div>

              {/* Citations */}
              {msg.citations && msg.citations.length > 0 && (
                <div className="flex flex-wrap gap-1.5 mt-1">
                  {msg.citations.slice(0, 5).map((c, i) => (
                    <button
                      key={i}
                      onClick={() => onCitationClick?.(c)}
                      className="flex items-center gap-1 px-2 py-0.5 rounded-md bg-surface-700 border border-surface-500
                                 hover:border-brand-500 text-xs text-slate-400 hover:text-brand-300 transition-colors"
                      title={`${c.file_path}:${c.start_line}`}
                    >
                      <span className="font-mono truncate max-w-[120px]">
                        {c.file_path.split('/').pop()}:{c.start_line}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Input area */}
      <div className="px-4 pb-4 pt-2 border-t border-surface-700">
        <div className="flex gap-2 items-end bg-surface-700 border border-surface-500 rounded-xl px-3 py-2
                        focus-within:ring-2 focus-within:ring-brand-500 focus-within:border-transparent transition-all">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask about this codebase… (Enter to send, Shift+Enter for newline)"
            rows={1}
            className="flex-1 bg-transparent text-slate-100 placeholder-slate-500 text-sm resize-none
                       focus:outline-none min-h-[24px] max-h-[120px] overflow-y-auto leading-6"
            style={{ height: 'auto' }}
            onInput={e => {
              const el = e.currentTarget
              el.style.height = 'auto'
              el.style.height = `${Math.min(el.scrollHeight, 120)}px`
            }}
            disabled={isStreaming}
          />
          <button
            onClick={isStreaming ? () => abortRef.current?.() : send}
            disabled={!input.trim() && !isStreaming}
            className="shrink-0 w-8 h-8 rounded-lg flex items-center justify-center transition-all
                       bg-brand-600 hover:bg-brand-500 disabled:opacity-40 disabled:cursor-not-allowed text-white"
          >
            {isStreaming
              ? <Loader2 className="w-4 h-4 animate-spin" />
              : <Send className="w-4 h-4" />
            }
          </button>
        </div>
      </div>
    </div>
  )
}
