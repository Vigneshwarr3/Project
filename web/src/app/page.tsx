import ChatInterface from '@/components/ChatInterface'

export default function Home() {
  return (
    <main
      className="min-h-screen flex flex-col"
      style={{ background: 'var(--bg)' }}
    >
      {/* Subtle dot-grid texture */}
      <div
        className="fixed inset-0 pointer-events-none z-0"
        style={{
          backgroundImage:
            'radial-gradient(circle at 1px 1px, rgba(255,255,255,0.035) 1px, transparent 0)',
          backgroundSize: '32px 32px',
        }}
      />

      <div className="relative z-10 flex flex-col flex-1">
        {/* ── Hero ── */}
        <header className="text-center px-6 pt-14 pb-8 select-none">
          {/* Status pill */}
          <div className="inline-flex items-center gap-2 mb-6 px-3 py-1 rounded-full border border-red-900/40 bg-red-950/20">
            <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
            <span className="text-[11px] font-semibold tracking-[0.18em] text-red-400 uppercase">
              Live Crime Database
            </span>
          </div>

          {/* Title */}
          <h1 className="font-black tracking-tighter leading-none mb-4">
            <span
              className="block text-white"
              style={{ fontSize: 'clamp(3.5rem, 12vw, 9rem)' }}
            >
              CHICAGO
            </span>
            <span
              className="block text-red-600"
              style={{
                fontSize: 'clamp(3.5rem, 12vw, 9rem)',
                textShadow: '0 0 120px rgba(220,38,38,0.35)',
              }}
            >
              CRIME
            </span>
          </h1>

          {/* Tagline — under 10 words */}
          <p className="text-gray-500 text-base tracking-wide">
            Query crime data with natural language.
          </p>
        </header>

        {/* ── Chat container ── */}
        <div className="flex-1 w-full max-w-2xl mx-auto px-4 pb-8 flex flex-col">
          <div
            className="flex flex-col flex-1 rounded-2xl overflow-hidden"
            style={{
              background: 'var(--card)',
              border: '1px solid var(--border)',
              minHeight: '520px',
            }}
          >
            <ChatInterface />
          </div>
        </div>
      </div>
    </main>
  )
}
