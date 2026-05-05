'use client'

import { useState } from 'react'

interface BotMessage {
  role: 'assistant'
  answer?: string
  sql_query?: string | null
  results?: Record<string, unknown>[] | null
  loading?: boolean
}

interface UserMessage {
  role: 'user'
  content: string
}

export type ChatMessage = (BotMessage | UserMessage) & { id: string }

function ChevronIcon({ open }: { open: boolean }) {
  return (
    <svg
      className={`w-3.5 h-3.5 transition-transform duration-200 ${open ? 'rotate-180' : ''}`}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <polyline points="6 9 12 15 18 9" />
    </svg>
  )
}

function CodeIcon() {
  return (
    <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="16 18 22 12 16 6" />
      <polyline points="8 6 2 12 8 18" />
    </svg>
  )
}

function ResultsTable({ results }: { results: Record<string, unknown>[] }) {
  if (!results.length) {
    return <p className="text-sm text-gray-600 italic">No rows returned.</p>
  }
  const keys = Object.keys(results[0])
  const shown = results.slice(0, 20)

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr>
            {keys.map(k => (
              <th
                key={k}
                className="text-left px-3 py-2 font-mono font-semibold text-gray-500 whitespace-nowrap border-b"
                style={{ borderColor: 'rgba(255,255,255,0.06)' }}
              >
                {k}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((row, i) => (
            <tr
              key={i}
              className="hover:bg-white/[0.02] transition-colors"
              style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}
            >
              {keys.map(k => (
                <td key={k} className="px-3 py-2 text-gray-300 whitespace-nowrap font-mono">
                  {String(row[k] ?? '')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {results.length > 20 && (
        <p className="text-xs text-gray-600 px-3 pt-2">
          Showing 20 of {results.length} rows
        </p>
      )}
    </div>
  )
}

function LoadingDots() {
  return (
    <div className="flex items-center gap-1.5 py-1">
      {[0, 150, 300].map(delay => (
        <span
          key={delay}
          className="w-1.5 h-1.5 rounded-full bg-red-600 animate-bounce"
          style={{ animationDelay: `${delay}ms` }}
        />
      ))}
    </div>
  )
}

export default function Message({ message }: { message: ChatMessage }) {
  const [showSQL, setShowSQL] = useState(false)

  if (message.role === 'user') {
    return (
      <div className="flex justify-end msg-enter">
        <div
          className="max-w-[75%] rounded-2xl rounded-tr-sm px-4 py-3"
          style={{
            background: 'rgba(220, 38, 38, 0.12)',
            border: '1px solid rgba(220, 38, 38, 0.2)',
          }}
        >
          <p className="text-gray-100 text-sm leading-relaxed">{message.content}</p>
        </div>
      </div>
    )
  }

  // Assistant bubble
  const bot = message as BotMessage & { id: string }
  const hasSql = !!bot.sql_query

  return (
    <div className="flex justify-start msg-enter">
      <div
        className="max-w-[85%] rounded-2xl rounded-tl-sm overflow-hidden"
        style={{
          background: 'rgba(255,255,255,0.03)',
          border: '1px solid rgba(255,255,255,0.07)',
        }}
      >
        {/* Answer — always visible */}
        <div className="px-4 py-3">
          {bot.loading ? (
            <LoadingDots />
          ) : (
            <p className="text-gray-200 text-sm leading-relaxed whitespace-pre-wrap">
              {bot.answer}
            </p>
          )}
        </div>

        {/* SQL toggle — only shown if there's a query */}
        {!bot.loading && hasSql && (
          <div style={{ borderTop: '1px solid rgba(255,255,255,0.06)' }}>
            <button
              onClick={() => setShowSQL(v => !v)}
              className="w-full flex items-center justify-between px-4 py-2.5 text-xs text-gray-500 hover:text-gray-300 transition-colors group"
            >
              <span className="flex items-center gap-2">
                <CodeIcon />
                <span className="font-mono tracking-wide">
                  {showSQL ? 'Hide' : 'Show'} SQL query &amp; results
                </span>
              </span>
              <ChevronIcon open={showSQL} />
            </button>

            {showSQL && (
              <div style={{ borderTop: '1px solid rgba(255,255,255,0.05)' }}>
                {/* SQL block */}
                <div className="p-4" style={{ background: 'var(--code)' }}>
                  <p className="text-[10px] font-mono font-semibold text-gray-600 tracking-widest mb-2 uppercase">
                    SQL Query
                  </p>
                  <pre className="text-xs font-mono text-gray-300 overflow-x-auto whitespace-pre-wrap leading-relaxed">
                    {bot.sql_query}
                  </pre>
                </div>

                {/* Results table */}
                {bot.results != null && (
                  <div className="p-4" style={{ borderTop: '1px solid rgba(255,255,255,0.05)' }}>
                    <p className="text-[10px] font-mono font-semibold text-gray-600 tracking-widest mb-3 uppercase">
                      Results — {bot.results.length} row{bot.results.length !== 1 ? 's' : ''}
                    </p>
                    <ResultsTable results={bot.results} />
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
