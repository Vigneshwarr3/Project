import { NextRequest, NextResponse } from 'next/server'
import Groq from 'groq-sdk'
import { Pool } from 'pg'

// ── Singletons (reused across warm serverless invocations) ────────────────────

let groqClient: Groq | null = null
function getGroq(): Groq {
  if (!groqClient) groqClient = new Groq({ apiKey: process.env.GROQ_API_KEY })
  return groqClient
}

let pgPool: Pool | null = null
function getPool(): Pool {
  if (!pgPool) {
    pgPool = new Pool({
      connectionString: process.env.DATABASE_URL,
      ssl: process.env.DATABASE_SSL === 'true' ? { rejectUnauthorized: false } : undefined,
      max: 3,
      idleTimeoutMillis: 20000,
      connectionTimeoutMillis: 5000,
    })
  }
  return pgPool
}

const MODEL = process.env.GROQ_MODEL ?? 'llama-3.3-70b-versatile'

// ── Relevance checker (mirrors Python agent) ──────────────────────────────────

const ACTION_VERBS = [
  'how many', 'how often', 'what are the', 'what is the', 'what percentage',
  'list', 'show', 'find', 'get', 'count', 'total', 'sum', 'average',
  'top', 'most', 'least', 'rank', 'filter', 'between', 'after', 'before',
  'during', 'in the year', 'which', 'for each', 'compare', 'distribution',
  'percentage', 'median', 'calculate', 'identify', 'breakdown',
]

const DATA_WORDS = [
  'crime', 'chicago', 'crimes', 'report', 'reported', 'database',
  'table', 'records', 'data', 'district', 'year', 'date', 'type',
  'arrest', 'victim', 'incidents', 'cases', 'statistics',
  'breakdown', 'distribution',
]

const IRRELEVANT_PATTERNS = [
  /what\s+is\s+\d+\s*[\+\-\*/]\s*\d+/,
  /what is the meaning/,
  /^what is\s+\w+\s*\?*\s*$/,
  /^define\s+/,
  /^explain\s+/,
  /^tell me about\s+/,
  /who (are|is|was|were)/,
  /when (are|is|was|were)/,
  /why (are|is|was|were)/,
]

function isRelevant(question: string): boolean {
  const q = question.toLowerCase().trim()
  for (const p of IRRELEVANT_PATTERNS) if (p.test(q)) return false
  return ACTION_VERBS.some(v => q.includes(v)) && DATA_WORDS.some(w => q.includes(w))
}

// ── Schema fetcher ────────────────────────────────────────────────────────────

async function fetchSchema(): Promise<string> {
  const client = await getPool().connect()
  try {
    const { rows } = await client.query(`
      SELECT table_name, column_name, data_type, character_maximum_length
      FROM information_schema.columns
      WHERE table_schema = 'public'
      ORDER BY table_name, ordinal_position
    `)
    const tables: Record<string, string[]> = {}
    for (const r of rows) {
      if (!tables[r.table_name]) tables[r.table_name] = []
      const type = r.character_maximum_length
        ? `${r.data_type}(${r.character_maximum_length})`
        : r.data_type
      tables[r.table_name].push(`  "${r.column_name}" ${type}`)
    }
    return Object.entries(tables)
      .map(([t, cols]) => `CREATE TABLE ${t} (\n${cols.join(',\n')}\n)`)
      .join('\n\n')
  } finally {
    client.release()
  }
}

// ── SQL generator ─────────────────────────────────────────────────────────────

const SYSTEM_PROMPT = `You are a PostgreSQL expert. Write a precise SELECT query for the question.
Use only column names that exist in the provided schema.
Never modify data (no INSERT / UPDATE / DELETE / DROP).

CRITICAL RULES:
- Enclose ALL column names in double quotes exactly as they appear in the schema (e.g. "YEAR", "CRIME_TYPE").
- String literals use single quotes (e.g. WHERE "CRIME_TYPE" = 'THEFT').
- Do NOT add a LIMIT clause unless the question explicitly asks for top-N or a specific number.
- Use CTEs (WITH clauses) for multi-step aggregations or window function pipelines.
- For percentage calculations use: ROUND(100.0 * numerator / NULLIF(denominator, 0), 2).
- Return ONLY the SQL query — no explanations, no markdown fences.
- If the question cannot be answered with SELECT, respond with exactly: INVALID_REQUEST`

async function generateSQL(question: string, schema: string): Promise<string | null> {
  const res = await getGroq().chat.completions.create({
    model: MODEL,
    temperature: 0,
    messages: [
      { role: 'system', content: SYSTEM_PROMPT },
      { role: 'user', content: `Schema:\n${schema}\n\nQuestion: ${question}` },
    ],
  })
  let sql = res.choices[0].message.content?.trim() ?? ''
  if (sql.includes('INVALID_REQUEST')) return null
  sql = sql.replace(/^```(?:sql)?\n?|```\s*$/gi, '').trim()
  return sql || null
}

// ── SQL validator ─────────────────────────────────────────────────────────────

async function validateSQL(sql: string): Promise<boolean> {
  if (/\b(UPDATE|DELETE|DROP|INSERT|ALTER)\b/i.test(sql)) return false
  const client = await getPool().connect()
  try {
    await client.query(`EXPLAIN ${sql}`)
    return true
  } catch {
    return false
  } finally {
    client.release()
  }
}

// ── Query runner ──────────────────────────────────────────────────────────────

async function runQuery(sql: string): Promise<Record<string, unknown>[]> {
  const client = await getPool().connect()
  try {
    const { rows } = await client.query(sql)
    return rows
  } finally {
    client.release()
  }
}

// ── Answer synthesizer ────────────────────────────────────────────────────────

async function synthesizeAnswer(
  question: string,
  results: Record<string, unknown>[],
): Promise<string> {
  const sample = JSON.stringify(results.slice(0, 10), null, 2)
  const res = await getGroq().chat.completions.create({
    model: MODEL,
    temperature: 0.3,
    messages: [
      {
        role: 'system',
        content:
          'You are a data analyst. Give a clear, concise natural-language summary of the SQL query results. Be direct and informative.',
      },
      {
        role: 'user',
        content: `Question: ${question}\n\nSQL Results (${results.length} rows total):\n${sample}\n\nSummarise.`,
      },
    ],
  })
  return res.choices[0].message.content ?? 'No summary available.'
}

async function irrelevantResponse(question: string): Promise<string> {
  const res = await getGroq().chat.completions.create({
    model: MODEL,
    temperature: 0.3,
    messages: [
      {
        role: 'system',
        content:
          "You are a helpful assistant focused on Chicago crime data analysis. The user's question is not related to the database or data analysis. Politely explain that their question is outside your scope. Be concise but friendly.",
      },
      { role: 'user', content: `User question: ${question}` },
    ],
  })
  return res.choices[0].message.content ?? 'I can only answer questions about Chicago crime data.'
}

// ── Route handler ─────────────────────────────────────────────────────────────

export async function POST(req: NextRequest) {
  try {
    const { question } = (await req.json()) as { question?: string }
    if (!question?.trim()) {
      return NextResponse.json({ error: 'Question is required.' }, { status: 400 })
    }

    if (!isRelevant(question)) {
      const answer = await irrelevantResponse(question)
      return NextResponse.json({ answer, sql_query: null, results: null })
    }

    let schema: string
    try {
      schema = await fetchSchema()
    } catch {
      return NextResponse.json(
        { error: 'Database connection failed. Check your DATABASE_URL environment variable.' },
        { status: 503 },
      )
    }

    // Generate + validate SQL with up to 3 retries
    let sql: string | null = null
    for (let attempt = 0; attempt < 3 && !sql; attempt++) {
      const candidate = await generateSQL(question, schema)
      if (candidate && (await validateSQL(candidate))) {
        sql = candidate
      }
    }

    if (!sql) {
      return NextResponse.json({
        answer: 'Unable to generate a valid SQL query after 3 attempts.',
        sql_query: null,
        results: null,
      })
    }

    let results: Record<string, unknown>[] = []
    try {
      results = await runQuery(sql)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      return NextResponse.json({
        answer: `Query ran but returned an error: ${msg}`,
        sql_query: sql,
        results: null,
      })
    }

    const answer = await synthesizeAnswer(question, results)
    return NextResponse.json({ answer, sql_query: sql, results })
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    console.error('[chat] error:', msg)
    return NextResponse.json({ error: 'Internal server error.', details: msg }, { status: 500 })
  }
}
