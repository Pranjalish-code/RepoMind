import { useState, useRef, useEffect, useCallback } from 'react'
import { Send, Bot, User, Loader2, Sparkles } from 'lucide-react'
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

    const userMsg: Message = { id: `u-${Date.now()}`, role: 'user', content: query }
    setMessages(prev => [...prev, userMsg])

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
    <div className="flex flex-col h-full bg-transparent">
      {/* Message list */}
      <div className="flex-1 overflow-y-auto px-4 py-6 space-y-6 custom-scrollbar">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center gap-4 animate-fade-in-up">
            <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-brand-500/20 to-brand-700/20 border border-brand-500/30 flex items-center justify-center shadow-[0_0_15px_rgba(99,102,241,0.15)]">
              <Sparkles className="w-8 h-8 text-brand-400" />
            </div>
            <div className="max-w-md">
              <h3 className="text-slate-100 font-semibold text-lg tracking-wide">Ask about your codebase</h3>
              <p className="text-slate-400 text-sm mt-2 leading-relaxed">
                Need to understand the architecture, find a bug, or write a new feature? I've indexed this entire repository and can help.
              </p>
            </div>
          </div>
        )}

        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex gap-4 animate-fade-in-up ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}
          >
            <div className={`w-8 h-8 rounded-lg shrink-0 flex items-center justify-center text-xs font-bold shadow-sm
              ${msg.role === 'user'
                ? 'bg-gradient-to-br from-brand-500 to-brand-700 text-white'
                : 'bg-surface-800 border border-surface-700 text-slate-300'}`}
            >
              {msg.role === 'user' ? <User className="w-4 h-4" /> : <Bot className="w-4 h-4 text-brand-400" />}
            </div>

            <div className={`max-w-[85%] ${msg.role === 'user' ? 'items-end' : 'items-start'} flex flex-col gap-2`}>
              <div className={`px-5 py-3.5 text-[14px] leading-relaxed whitespace-pre-wrap shadow-sm
                ${msg.role === 'user'
                  ? 'bg-surface-800 border border-surface-700 text-slate-200 rounded-2xl rounded-tr-sm'
                  : 'bg-transparent text-slate-200 prose'
                }
                ${msg.isStreaming ? 'typing-cursor' : ''}`}
              >
                {msg.content || (msg.isStreaming ? '' : '…')}
              </div>

              {/* Citations */}
              {msg.citations && msg.citations.length > 0 && (
                <div className="flex flex-wrap gap-2 mt-2">
                  {msg.citations.slice(0, 5).map((c, i) => (
                    <button
                      key={i}
                      onClick={() => onCitationClick?.(c)}
                      className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-surface-900 border border-surface-700
                                 hover:border-brand-500 hover:bg-brand-500/10 text-xs text-slate-400 hover:text-brand-300 transition-all"
                      title={`${c.file_path}:${c.start_line}`}
                    >
                      <span className="font-mono truncate max-w-[150px]">
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
      <div className="px-5 pb-5 pt-3 bg-surface-950">
        <div className="flex gap-3 items-end bg-surface-900 border border-surface-700 rounded-xl px-4 py-3
                        focus-within:ring-1 focus-within:ring-brand-500 focus-within:border-brand-500 transition-all shadow-sm">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask a question about the code..."
            rows={1}
            className="flex-1 bg-transparent text-slate-200 placeholder-slate-500 text-[14px] resize-none
                       focus:outline-none min-h-[24px] max-h-[160px] overflow-y-auto leading-relaxed py-0.5"
            style={{ height: 'auto' }}
            onInput={e => {
              const el = e.currentTarget
              el.style.height = 'auto'
              el.style.height = `${Math.min(el.scrollHeight, 160)}px`
            }}
            disabled={isStreaming}
          />
          <button
            onClick={isStreaming ? () => abortRef.current?.() : send}
            disabled={!input.trim() && !isStreaming}
            className={`shrink-0 w-8 h-8 rounded-lg flex items-center justify-center transition-all ${
              input.trim() || isStreaming 
                ? 'bg-brand-600 hover:bg-brand-500 text-white shadow-sm' 
                : 'bg-surface-800 text-surface-500 cursor-not-allowed'
            }`}
          >
            {isStreaming
              ? <Loader2 className="w-4 h-4 animate-spin" />
              : <Send className="w-4 h-4" />
            }
          </button>
        </div>
        <div className="text-center mt-2">
          <span className="text-[10px] text-slate-500 font-medium">RepoMind AI can make mistakes. Check its work.</span>
        </div>
      </div>
    </div>
  )
}
