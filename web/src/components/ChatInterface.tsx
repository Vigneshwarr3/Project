'use client'

import { useEffect, useRef, useState } from 'react'
import Message, { type ChatMessage } from './Message'

function uuid() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = (Math.random() * 16) | 0
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16)
  })
}

const SUGGESTIONS = [
  'How many crimes were reported in 2023?',
  'What are the top 5 crime types?',
  'Which district has the most arrests?',
]

function SendIcon() {
  return (
    <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <line x1="22" y1="2" x2="11" y2="13" />
      <polygon points="22 2 15 22 11 13 2 9 22 2" />
    </svg>
  )
}

export default function ChatInterface() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void submit()
    }
  }

  async function submit() {
    const question = input.trim()
    if (!question || busy) return
    setInput('')

    const userId = uuid()
    const botId = uuid()

    setMessages(prev => [
      ...prev,
      { id: userId, role: 'user', content: question },
      { id: botId, role: 'assistant', loading: true },
    ])
    setBusy(true)

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question }),
      })
      const data = (await res.json()) as {
        answer?: string
        sql_query?: string | null
        results?: Record<string, unknown>[] | null
        error?: string
      }

      setMessages(prev =>
        prev.map(m =>
          m.id === botId
            ? {
                id: botId,
                role: 'assistant' as const,
                loading: false,
                answer: data.answer ?? data.error ?? 'An unexpected error occurred.',
                sql_query: data.sql_query ?? null,
                results: data.results ?? null,
              }
            : m,
        ),
      )
    } catch {
      setMessages(prev =>
        prev.map(m =>
          m.id === botId
            ? { id: botId, role: 'assistant' as const, loading: false, answer: 'Network error — please try again.' }
            : m,
        ),
      )
    } finally {
      setBusy(false)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }

  return (
    <div className="flex flex-col flex-1 h-full">
      {/* Message list */}
      <div className="flex-1 overflow-y-auto px-5 py-5 space-y-4">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full gap-5 py-12 text-center">
            <p className="text-sm text-gray-600">Ask anything about Chicago crime data</p>
            <div className="flex flex-wrap gap-2 justify-center">
              {SUGGESTIONS.map(q => (
                <button
                  key={q}
                  onClick={() => { setInput(q); inputRef.current?.focus() }}
                  className="text-xs px-3 py-1.5 rounded-full text-gray-500 hover:text-gray-300 transition-colors"
                  style={{ border: '1px solid rgba(255,255,255,0.08)' }}
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map(msg => (
          <Message key={msg.id} message={msg} />
        ))}

        <div ref={bottomRef} />
      </div>

      {/* Input bar */}
      <div
        className="px-4 py-3 flex gap-3 items-end"
        style={{ borderTop: '1px solid rgba(255,255,255,0.06)' }}
      >
        <textarea
          ref={inputRef}
          rows={1}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about Chicago crime data…"
          disabled={busy}
          className="flex-1 resize-none bg-transparent text-sm text-gray-200 placeholder-gray-600 focus:outline-none disabled:opacity-40 leading-relaxed py-1"
          style={{ maxHeight: '120px' }}
        />
        <button
          onClick={() => void submit()}
          disabled={!input.trim() || busy}
          className="flex-shrink-0 w-8 h-8 rounded-lg flex items-center justify-center bg-red-600 hover:bg-red-500 text-white transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
        >
          <SendIcon />
        </button>
      </div>
    </div>
  )
}
