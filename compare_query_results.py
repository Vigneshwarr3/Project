#!/opt/miniconda3/envs/nlp/bin/python
"""
compare_query_results.py
========================
Runs both the ground-truth SQL and the agent-generated SQL for every row in
evaluation_results.csv directly against the database, then writes result
columns and EX scores back to the same file.

Columns written
---------------
  ground_truth_result  — JSON-serialised rows from the ground-truth query
  agent_result         — JSON-serialised rows from the agent query
  ex_hard              — 1 if exact JSON match (strict), else 0
  ex_soft              — 1 if relational set-equivalence OR LLM judge agrees, else 0

Usage
-----
    python compare_query_results.py                    # run all rows
    python compare_query_results.py --limit 10         # first N rows
    python compare_query_results.py --id 5 42          # specific ids
    python compare_query_results.py --score-only       # skip DB re-run; (re)compute EX from existing columns
    python compare_query_results.py --score-only --skip-llm   # set-equivalence only, no LLM calls
"""

import argparse
import csv
import json
import os
import time
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

# ── DB config ─────────────────────────────────────────────────────────────────

DB_CONFIG = {
    "host":     "100.90.162.48",
    "port":     5432,
    "dbname":   "chicago_crime",
    "user":     "admin",
    "password": "admin@123",
}

MAX_ROWS = 20

# ── LLM config ────────────────────────────────────────────────────────────────

JUDGE_MODEL = "llama-3.1-8b-instant"
LLM_DELAY     = 0.5   # seconds between judge calls to avoid rate-limit bursts

# ── Paths ─────────────────────────────────────────────────────────────────────

BASE_DIR    = Path(__file__).resolve().parent
RESULTS_CSV = BASE_DIR / "evaluation_results.csv"


# ── DB helpers ────────────────────────────────────────────────────────────────

def run_query(cursor, sql: str) -> str:
    if not sql or not sql.strip():
        return json.dumps({"error": "empty query"})
    try:
        cursor.execute(sql)
        rows = cursor.fetchmany(MAX_ROWS)
        colnames = [desc[0] for desc in cursor.description] if cursor.description else []
        result = [dict(zip(colnames, row)) for row in rows]
        return json.dumps(result, default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ── EX scoring ────────────────────────────────────────────────────────────────

def _parse_result(json_str: str) -> Optional[list]:
    """Return parsed list-of-dicts, or None for empty / error results."""
    if not json_str or not json_str.strip():
        return None
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list) or not data:
        return None
    if isinstance(data[0], dict) and "error" in data[0]:
        return None
    return data


def _normalize(v):
    """Normalize a scalar value for column-agnostic comparison."""
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, float):
        return round(v, 2)
    if isinstance(v, str):
        try:
            return round(float(v), 2)
        except ValueError:
            pass
        return v.strip().lower()
    return v


def _to_value_multiset(data: list) -> list:
    """
    Convert list-of-dicts to a sorted list of normalized value tuples.
    Column names are ignored — only values matter.
    """
    rows = []
    for row in data:
        if isinstance(row, dict):
            rows.append(tuple(_normalize(v) for v in row.values()))
        else:
            rows.append((_normalize(row),))
    return sorted(rows, key=lambda t: [str(x) for x in t])


def ex_hard(gt_json: str, ag_json: str) -> int:
    """Strict exact JSON match after sorting by key."""
    try:
        gt = sorted(json.loads(gt_json), key=lambda x: json.dumps(x, sort_keys=True, default=str))
        ag = sorted(json.loads(ag_json), key=lambda x: json.dumps(x, sort_keys=True, default=str))
        return 1 if gt == ag else 0
    except Exception:
        return 0


def ex_relational(gt_json: str, ag_json: str) -> bool:
    """
    Relational set-equivalence: same value multi-sets regardless of column
    names, sort order, or minor numeric precision (2 decimal places).
    """
    gt_data = _parse_result(gt_json)
    ag_data = _parse_result(ag_json)
    if gt_data is None or ag_data is None:
        return False
    return _to_value_multiset(gt_data) == _to_value_multiset(ag_data)


def ex_llm_judge(question: str, gt_json: str, ag_json: str, client: Groq) -> bool:
    """
    Ask a small LLM whether the agent result correctly answers the question.
    Used as a fallback when relational set-equivalence fails.
    """
    prompt = (
        "You are evaluating a text-to-SQL agent.\n\n"
        f"Question: {question}\n\n"
        f"Ground-truth result (first rows):\n{gt_json[:600]}\n\n"
        f"Agent result (first rows):\n{ag_json[:600]}\n\n"
        "Does the agent result correctly answer the question compared to the ground truth?\n"
        "Consider results equivalent if they contain the same key values, even if column "
        "names, row order, or minor numeric precision differ.\n"
        "Truncated results (fewer rows than ground truth) are NOT equivalent.\n"
        "Reply with exactly one word: yes or no."
    )
    try:
        resp = client.chat.completions.create(
            model=JUDGE_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=5,
            temperature=0,
        )
        return resp.choices[0].message.content.strip().lower().startswith("yes")
    except Exception as exc:
        print(f"      [LLM judge error] {exc}")
        return False


def compute_ex_soft(question: str, gt_json: str, ag_json: str,
                    client: Optional[Groq], skip_llm: bool) -> int:
    """Hybrid: relational first; escalate to LLM only if no match."""
    if ex_relational(gt_json, ag_json):
        return 1
    if skip_llm or client is None:
        return 0
    time.sleep(LLM_DELAY)
    return 1 if ex_llm_judge(question, gt_json, ag_json, client) else 0


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Run queries, compute hybrid EX scores, write back to evaluation_results.csv"
    )
    parser.add_argument("--limit",      type=int,  default=None,
                        help="Process only the first N rows")
    parser.add_argument("--id",         type=int,  nargs="+", dest="ids",
                        help="Process only rows with these ids")
    parser.add_argument("--score-only", action="store_true",
                        help="Skip DB query re-run; compute EX from existing result columns")
    parser.add_argument("--skip-llm",   action="store_true",
                        help="Use set-equivalence only; no LLM judge calls")
    args = parser.parse_args()

    # ── Load CSV ──────────────────────────────────────────────────────────────
    with open(RESULTS_CSV, newline="", encoding="utf-8") as f:
        all_rows = list(csv.DictReader(f))
    print(f"Loaded {len(all_rows)} rows from {RESULTS_CSV.name}")

    work_rows = list(all_rows)
    if args.ids:
        id_set    = set(args.ids)
        work_rows = [r for r in work_rows if int(r["id"]) in id_set]
    if args.limit:
        work_rows = work_rows[: args.limit]
    print(f"Will process {len(work_rows)} rows\n")

    # ── LLM client ────────────────────────────────────────────────────────────
    llm_client: Optional[Groq] = None
    if not args.skip_llm:
        api_key = os.getenv("GROQ_API_KEY")
        if api_key:
            llm_client = Groq(api_key=api_key)
            print(f"LLM judge enabled: {JUDGE_MODEL} via Groq")
        else:
            print("GROQ_API_KEY not set — falling back to set-equivalence only")

    # ── DB connection ─────────────────────────────────────────────────────────
    conn = cur = None
    if not args.score_only:
        print("Connecting to PostgreSQL …")
        conn = psycopg2.connect(**DB_CONFIG)
        conn.autocommit = False
        cur = conn.cursor()
        print(f"Connected to {DB_CONFIG['dbname']} on {DB_CONFIG['host']}\n")

    # ── Process rows ──────────────────────────────────────────────────────────
    update_map = {}
    for i, row in enumerate(work_rows, start=1):
        qid = row["id"]
        print(f"[{i}/{len(work_rows)}] id={qid} ({row['complexity']}) — {row['question'][:65]}…")

        if args.score_only:
            gt_result = row.get("ground_truth_result", "")
            ag_result = row.get("agent_result", "")
        else:
            try:
                conn.rollback()
                gt_result = run_query(cur, row["ground_truth_sql"])
                conn.rollback()
            except Exception as exc:
                gt_result = json.dumps({"error": f"connection-level error: {exc}"})

            try:
                conn.rollback()
                ag_result = run_query(cur, row["agent_sql_query"])
                conn.rollback()
            except Exception as exc:
                ag_result = json.dumps({"error": f"connection-level error: {exc}"})

            print(f"    GT : {gt_result[:110].replace(chr(10), ' ')}")
            print(f"    AGT: {ag_result[:110].replace(chr(10), ' ')}")

        hard = ex_hard(gt_result, ag_result)
        soft = compute_ex_soft(row["question"], gt_result, ag_result, llm_client, args.skip_llm)
        label = "relational" if ex_relational(gt_result, ag_result) else ("llm" if soft else "no-match")
        print(f"    EX hard={hard}  soft={soft}  ({label})")

        row = dict(row)
        row["ground_truth_result"] = gt_result
        row["agent_result"]        = ag_result
        row["ex_hard"]             = hard
        row["ex_soft"]             = soft
        update_map[qid]            = row

    if conn:
        cur.close()
        conn.close()

    # ── Merge and write back ──────────────────────────────────────────────────
    for row in all_rows:
        if row["id"] in update_map:
            row.update(update_map[row["id"]])
        else:
            for col in ("ground_truth_result", "agent_result", "ex_hard", "ex_soft"):
                row.setdefault(col, "")

    existing_fields = list(all_rows[0].keys()) if all_rows else []
    for col in ("ground_truth_result", "agent_result", "ex_hard", "ex_soft"):
        if col not in existing_fields:
            existing_fields.append(col)

    with open(RESULTS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=existing_fields, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(all_rows)

    # ── Summary ───────────────────────────────────────────────────────────────
    processed = list(update_map.values())
    n = len(processed)
    if n:
        n_hard = sum(int(r["ex_hard"]) for r in processed)
        n_soft = sum(int(r["ex_soft"]) for r in processed)
        print(f"\n{'─'*50}")
        print(f"Processed : {n} rows")
        print(f"EX hard   : {n_hard}/{n} = {n_hard/n:.0%}")
        print(f"EX soft   : {n_soft}/{n} = {n_soft/n:.0%}")
        print(f"Gain      : +{n_soft - n_hard} rows ({(n_soft-n_hard)/n:.0%})")
    print(f"Results written to {RESULTS_CSV}")


if __name__ == "__main__":
    main()
