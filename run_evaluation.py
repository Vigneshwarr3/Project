#!/opt/miniconda3/envs/nlp/bin/python
"""
run_evaluation.py
=================
Runs every question in evaluation_dataset.csv through the sql_agent_v2
LangGraph pipeline and writes results to evaluation_results.csv.

Output columns
--------------
id, complexity, question, ground_truth_sql, agent_sql_query, agent_answer, error_log

Usage
-----
    # Use the nlp conda environment which has all required packages:
    /opt/miniconda3/envs/nlp/bin/python run_evaluation.py              # all 100 questions
    /opt/miniconda3/envs/nlp/bin/python run_evaluation.py --start 1 --end 30   # Easy only
    /opt/miniconda3/envs/nlp/bin/python run_evaluation.py --resume              # skip done rows
"""

import csv
import json
import os
import re
import time
import argparse
from pathlib import Path
from typing import Optional, Literal

from dotenv import load_dotenv

from langchain_groq import ChatGroq
from langchain_community.utilities import SQLDatabase
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import StateGraph, END, START
from typing import TypedDict

load_dotenv()

# ── Paths ─────────────────────────────────────────────────────────────────────

BASE_DIR     = Path(__file__).resolve().parent
INPUT_CSV    = BASE_DIR / "evaluation_dataset_v1.csv"
OUTPUT_CSV   = BASE_DIR / "evaluation_results.csv"

# ── DB / LLM config (mirrors sql_agent_v2.ipynb) ─────────────────────────────

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DB_USER      = "admin"
DB_PASSWORD  = "admin%40123"
DB_HOST      = "100.90.162.48"
DB_PORT      = "5432"
DB_NAME      = "chicago_crime"
DB_URI       = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
MODEL_NAME   = "openai/gpt-oss-120b"

# Seconds to wait between API calls to respect Groq rate limits
REQUEST_DELAY = 2.0


# ── Agent state ───────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    question:    str
    sql_query:   Optional[str]
    schema:      Optional[str]
    results:     Optional[list]
    answer:      Optional[str]
    error_log:   list
    retry_count: int
    is_relevant: bool


# ── Agent node definitions (verbatim from sql_agent_v2.ipynb) ─────────────────

def build_agent(llm: ChatGroq, db: SQLDatabase):

    def relevance_checker(state: AgentState) -> AgentState:
        question = state["question"].lower().strip()

        query_action_verbs = {
            "how many", "how often", "what are the", "what is the", "what percentage",
            "list", "show", "find", "get", "count", "total", "sum", "average",
            "top", "most", "least", "rank", "filter", "between", "after", "before",
            "during", "in the year", "which", "for each", "compare", "distribution",
            "percentage", "median", "calculate", "identify", "breakdown",
        }
        data_context_words = {
            "crime", "chicago", "crimes", "report", "reported", "database",
            "table", "records", "data", "district", "year", "date", "type",
            "arrest", "victim", "incidents", "cases", "statistics",
            "breakdown", "distribution",
        }
        irrelevant_patterns = [
            (r"what\s+is\s+\d+\s*[\+\-\*/]\s*\d+", "math expression"),
            (r"what is the meaning", "meaning of life"),
            (r"^what is\s+\w+\s*\?*\s*$", "generic what is"),
            (r"^define\s+", "define"),
            (r"^explain\s+", "explain"),
            (r"^tell me about\s+", "tell me about"),
            (r"who (are|is|was|were)", "who question"),
            (r"when (are|is|was|were)", "when question"),
            (r"why (are|is|was|were)", "why question"),
        ]
        invalid_data_questions = [
            (r"what\s+is\s+(chicago_crime|the chicago crime|the crimes|the database|the table)(?:\s|$|\?)",
             "non-specific data"),
        ]

        for pattern, reason in invalid_data_questions:
            if re.search(pattern, question, re.IGNORECASE):
                state["is_relevant"] = False
                state["error_log"].append(f"Relevance: {reason}")
                break
        else:
            for pattern, reason in irrelevant_patterns:
                if re.search(pattern, question, re.IGNORECASE):
                    state["is_relevant"] = False
                    state["error_log"].append(f"Relevance: {reason}")
                    break
            else:
                has_action_verb   = any(v in question for v in query_action_verbs)
                has_data_context  = any(w in question for w in data_context_words)

                if has_action_verb and has_data_context:
                    state["is_relevant"] = True
                    state["error_log"].append("Relevance: Approved")
                    return state

                state["is_relevant"] = False
                state["error_log"].append("Relevance: Rejected - missing action verb or data context")

        if not state["is_relevant"]:
            prompt = ChatPromptTemplate.from_messages([
                ("system",
                 "You are a helpful assistant. The user's question is not related to "
                 "a database or data analysis. Politely explain that their question is "
                 "outside your scope. Be concise but friendly."),
                ("human", f"User question: {state['question']}"),
            ])
            state["answer"] = (prompt | llm).invoke({}).content

        return state

    def schema_fetcher(state: AgentState) -> AgentState:
        try:
            state["schema"] = db.get_table_info()
        except Exception as e:
            state["error_log"].append(f"Schema fetch error: {e}")
            state["schema"] = ""
        return state

    system_prompt = """You are a PostgreSQL expert. Write a precise SELECT query for the question.
    Use only column names that exist in the provided schema.
    Never modify data (no INSERT / UPDATE / DELETE / DROP).
    Use proper joins, group by, order by, and limit clauses as needed.

    CRITICAL RULES:
    - This database is case-sensitive for identifiers.
    - Enclose ALL column names in double quotes exactly as they appear in the schema (e.g. "YEAR", "CRIME_TYPE").
    - String literals use single quotes (e.g. WHERE "CRIME_TYPE" = 'THEFT').
    - Do NOT add a LIMIT clause unless the question explicitly asks for top-N or a specific number of rows.
    - Use CTEs (WITH clauses) for multi-step aggregations, self-comparisons, or window function pipelines.
    - For percentage calculations use: ROUND(100.0 * numerator / NULLIF(denominator, 0), 2).
    - For window functions, always specify explicit PARTITION BY and ORDER BY clauses.
    - For comparing two time periods in the same table, use two subqueries or CTEs rather than a JOIN to a second table.
    - Return ONLY the SQL query — no explanations or markdown.
    - If the question cannot be answered with SELECT, respond with: INVALID_REQUEST"""

    def query_generator(state: AgentState) -> AgentState:
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("human", f"Schema:\n{state.get('schema', '')}\n\nQuestion: {state['question']}"),
        ])
        response = (prompt | llm).invoke({}).content.strip()

        if "INVALID_REQUEST" in response:
            state["error_log"].append("Generator: cannot fulfil with SELECT")
            state["sql_query"] = None
        else:
            # Strip accidental markdown fences
            response = re.sub(r"^```(?:sql)?\n?|```\s*$", "", response, flags=re.IGNORECASE).strip()
            state["sql_query"] = response

        return state

    def query_validator(state: AgentState) -> AgentState:
        if not state.get("sql_query"):
            state["error_log"].append("Validator: no SQL to validate")
            return state
        if re.search(r"\b(UPDATE|DELETE|DROP|INSERT|ALTER)\b", state["sql_query"], re.IGNORECASE):
            state["error_log"].append("Validator: DML detected — rejected")
            state["sql_query"] = None
            return state
        try:
            db.run(f"EXPLAIN {state['sql_query']}", fetch="all")
            state["error_log"].append("Validator: syntax OK")
        except Exception as e:
            state["error_log"].append(f"Validator: syntax error — {e}")
            state["sql_query"] = None
        return state

    def should_retry_query(state: AgentState) -> Literal["query_generator", "query_runner", "answer_synthesizer"]:
        if not state.get("sql_query"):
            state["retry_count"] += 1
            if state["retry_count"] >= 3:
                state["error_log"].append("Max retries (3) reached.")
                state["answer"] = "Unable to generate a valid SQL query after 3 attempts."
                return "answer_synthesizer"
            return "query_generator"
        return "query_runner"

    def query_runner(state: AgentState) -> AgentState:
        try:
            result = db.run(state["sql_query"], fetch="all")
            state["results"] = result if isinstance(result, list) else [result]
            state["error_log"].append(f"Runner: {len(state['results'])} row(s) returned")
        except Exception as e:
            state["error_log"].append(f"Runner: execution error — {e}")
            state["results"] = []
        return state

    def answer_synthesizer(state: AgentState) -> AgentState:
        if state.get("answer"):
            return state
        if not state.get("results"):
            state["answer"] = "No results found."
            return state
        results_str = json.dumps(state["results"][:10], indent=2)
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are a data analyst. Give a clear, concise natural-language "
                       "summary of the SQL query results."),
            ("human", f"Question: {state['question']}\n\nSQL Results:\n{results_str}\n\nSummarise."),
        ])
        state["answer"] = (prompt | llm).invoke({}).content
        return state

    def should_continue_after_relevance(state: AgentState) -> Literal["schema_fetcher", "__end__"]:
        return "schema_fetcher" if state.get("is_relevant") else END

    g = StateGraph(AgentState)
    g.add_node("relevance_checker",  relevance_checker)
    g.add_node("schema_fetcher",     schema_fetcher)
    g.add_node("query_generator",    query_generator)
    g.add_node("query_validator",    query_validator)
    g.add_node("query_runner",       query_runner)
    g.add_node("answer_synthesizer", answer_synthesizer)

    g.add_edge(START, "relevance_checker")
    g.add_conditional_edges("relevance_checker", should_continue_after_relevance)
    g.add_edge("schema_fetcher",  "query_generator")
    g.add_edge("query_generator", "query_validator")
    g.add_conditional_edges("query_validator", should_retry_query)
    g.add_edge("query_runner",       "answer_synthesizer")
    g.add_edge("answer_synthesizer", END)

    return g.compile()


def invoke_agent(app, question: str) -> dict:
    """Run one question and return the final AgentState dict."""
    initial: AgentState = {
        "question":    question,
        "sql_query":   None,
        "schema":      None,
        "results":     None,
        "answer":      None,
        "error_log":   [],
        "retry_count": 0,
        "is_relevant": False,
    }
    return app.invoke(initial)


# ── CSV helpers ───────────────────────────────────────────────────────────────

OUTPUT_FIELDS = [
    "id", "complexity", "question",
    "ground_truth_sql", "agent_sql_query", "agent_answer", "error_log",
]


def load_completed_ids(path: Path) -> set[int]:
    """Return the set of ids already written to the output CSV."""
    if not path.exists():
        return set()
    with open(path, newline="", encoding="utf-8") as f:
        return {int(row["id"]) for row in csv.DictReader(f) if row.get("id", "").isdigit()}


def read_evaluation_dataset(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Evaluate SQL agent against evaluation_dataset.csv")
    parser.add_argument("--start",  type=int, default=1,   help="First question id to run (inclusive)")
    parser.add_argument("--end",    type=int, default=100, help="Last question id to run (inclusive)")
    parser.add_argument("--resume", action="store_true",   help="Skip rows already in the output file")
    parser.add_argument("--ids",    type=int, nargs="+",   help="Re-run only these specific question ids (overrides --start/--end)")
    args = parser.parse_args()

    # ── Setup ─────────────────────────────────────────────────────────────────
    print("Connecting to database …")
    db  = SQLDatabase.from_uri(DB_URI)
    llm = ChatGroq(model=MODEL_NAME, temperature=0, api_key=GROQ_API_KEY)
    app = build_agent(llm, db)
    print(f"Agent ready. DB dialect: {db.dialect}\n")

    # ── Load dataset ──────────────────────────────────────────────────────────
    all_rows = read_evaluation_dataset(INPUT_CSV)

    if args.ids:
        id_set = set(args.ids)
        rows   = [r for r in all_rows if int(r["id"]) in id_set]
    else:
        rows   = [r for r in all_rows if args.start <= int(r["id"]) <= args.end]

    completed_ids = load_completed_ids(OUTPUT_CSV) if args.resume else set()

    # ── When --ids is used, patch the existing output file in place ───────────
    # Load any existing results so we can merge new rows back in.
    existing_results: dict[int, dict] = {}
    if args.ids and OUTPUT_CSV.exists():
        with open(OUTPUT_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("id", "").isdigit():
                    existing_results[int(row["id"])] = row

    # ── Open output CSV (append mode so --resume works) ───────────────────────
    # When --ids is given we write a temp buffer and merge at the end.
    use_patch_mode = bool(args.ids)
    if use_patch_mode:
        import io
        out_f  = io.StringIO()
    else:
        write_header = not OUTPUT_CSV.exists() or not args.resume
        out_f  = open(OUTPUT_CSV, "a" if args.resume else "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(out_f, fieldnames=OUTPUT_FIELDS, quoting=csv.QUOTE_ALL)
    if use_patch_mode:
        writer.writeheader()   # header required so DictReader can parse the buffer later
    elif not OUTPUT_CSV.exists() or not args.resume:
        writer.writeheader()

    total   = len(rows)
    success = 0
    failed  = 0

    try:
        for i, row in enumerate(rows, start=1):
            qid        = int(row["id"])
            complexity = row["complexity"]
            question   = row["question"]
            gt_sql     = row["sql_query"]

            if args.resume and qid in completed_ids:
                print(f"[{i}/{total}] id={qid} — already done, skipping.")
                continue

            print(f"[{i}/{total}] id={qid} ({complexity}) — {question[:70]}…")

            try:
                result = invoke_agent(app, question)

                agent_sql    = result.get("sql_query") or ""
                agent_answer = result.get("answer")    or ""
                error_log    = " | ".join(result.get("error_log") or [])

                writer.writerow({
                    "id":               qid,
                    "complexity":       complexity,
                    "question":         question,
                    "ground_truth_sql": gt_sql,
                    "agent_sql_query":  agent_sql,
                    "agent_answer":     agent_answer,
                    "error_log":        error_log,
                })
                out_f.flush()

                status = "OK" if agent_sql else "NO_SQL"
                print(f"         → {status} | answer preview: {agent_answer[:80]!r}")
                success += 1

            except Exception as exc:
                failed += 1
                print(f"         → ERROR: {exc}")
                writer.writerow({
                    "id":               qid,
                    "complexity":       complexity,
                    "question":         question,
                    "ground_truth_sql": gt_sql,
                    "agent_sql_query":  "",
                    "agent_answer":     "",
                    "error_log":        f"Script exception: {exc}",
                })
                out_f.flush()

            # Respect Groq rate limits between calls
            if i < total:
                time.sleep(REQUEST_DELAY)

    finally:
        if not use_patch_mode:
            out_f.close()

    # ── Patch mode: merge new rows back into the existing CSV ─────────────────
    if use_patch_mode:
        out_f.seek(0)
        for new_row in csv.DictReader(out_f):
            if new_row.get("id", "").isdigit():
                existing_results[int(new_row["id"])] = new_row

        # Reconstruct full file in original id order
        full_rows = []
        id_order  = [int(r["id"]) for r in all_rows]
        for qid in id_order:
            if qid in existing_results:
                full_rows.append(existing_results[qid])

        with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
            fieldnames = list(full_rows[0].keys()) if full_rows else OUTPUT_FIELDS
            w = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_ALL,
                               extrasaction="ignore")
            w.writeheader()
            w.writerows(full_rows)

    print(f"\n{'='*60}")
    print(f"Done. Total={total}  Success={success}  Failed={failed}")
    print(f"Results saved to: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
