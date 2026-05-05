/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  experimental: {
    // Keep pg and groq-sdk as external deps so standalone tracing includes them
    serverExternalPackages: ['pg', 'groq-sdk'],
  },
  async rewrites() {
    // When BACKEND_URL is set (Vercel/remote proxy mode), forward /api/chat there.
    // When unset (self-hosted Docker), the built-in API route handles it directly.
    const backendUrl = process.env.BACKEND_URL
    if (!backendUrl) return []
    return [
      {
        source: '/api/chat',
        destination: `${backendUrl}/api/chat`,
      },
    ]
  },
}

export default nextConfig
