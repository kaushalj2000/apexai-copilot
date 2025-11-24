import json
import os
import re
from pathlib import Path
from typing import Dict, Any, Optional
import duckdb
import pandas as pd
from google import genai

# --------------------------------------------------------------------
# CONFIGURATION
# --------------------------------------------------------------------
DB_PATH = "apex_copilot.duckdb"
INSIGHTS_PATH = Path("data_processed/driver_insights.json")
GEMINI_MODEL = "gemini-2.0-flash"
API_KEY_PATH = "config/openai_key.txt"

def load_api_key() -> str:
    if not os.path.exists(API_KEY_PATH):
        raise FileNotFoundError(f"Missing API key file at {API_KEY_PATH}")
    with open(API_KEY_PATH, "r") as f:
        return f.read().strip()

_gemini_client: genai.Client | None = None

def get_gemini_client() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=load_api_key())
    return _gemini_client

SCHEMA_SUMMARY_CACHE = None

# ------------------------------------------------------------------------------
# DATABASE FUNCTIONS
# ------------------------------------------------------------------------------
def get_duckdb_connection() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(DB_PATH, read_only=True)

def run_sql(query: str) -> pd.DataFrame:
    q = query.strip().lower()
    if not q.startswith("select") and not q.startswith("with"):
        raise ValueError("Only SELECT queries are allowed.")
    con = get_duckdb_connection()
    try:
        df = con.execute(query).df()
    except Exception as e:
        print(f"SQL Execution Error: {e}")
        df = pd.DataFrame()
    finally:
        con.close()
    return df

def build_schema_summary() -> str:
    con = get_duckdb_connection()
    try:
        tables = con.execute("SHOW TABLES").df()
        lines = []
        for _, row in tables.iterrows():
            table_name = row["name"]
            cols = con.execute(f"PRAGMA table_info('{table_name}')").df()
            col_list = ", ".join(cols["name"].tolist())
            lines.append(f"- {table_name}({col_list})")
        return "\n".join(lines)
    except Exception as e:
        return f"Error getting schema: {e}"
    finally:
        con.close()

def _get_schema_cached() -> str:
    global SCHEMA_SUMMARY_CACHE
    if SCHEMA_SUMMARY_CACHE is None:
        SCHEMA_SUMMARY_CACHE = build_schema_summary()
    return SCHEMA_SUMMARY_CACHE

# ------------------------------------------------------------------------------
# LLM / CHAT LOGIC
# ------------------------------------------------------------------------------
CHAT_SYSTEM_PROMPT = """You are ApexAI Co-Pilot, a data-savvy race engineer for the Toyota GR Cup.
You answer questions about laps, sectors, and telemetry.

RULES:
1. Prefer simple, safe SELECT queries.
2. LIMIT results to 20 rows.
3. **CRITICAL**: When querying for "best" or "fastest" laps, YOU MUST filter out bad data by adding `WHERE lap_time_s > 30` (or similar). Never show a 0-second lap.
4. If the user says "my" or "I", use the CURRENT DRIVER CONTEXT provided below.

Table Context:
- 'drivers': driver_id, car_no, class
- 'laps': lap_time_s, is_valid, lap_no
- 'sectors': sector_time_s, main_sector (S1/S2/S3)
- 'driver_opportunities': avg_loss_s, main_sector
- 'physics_sector_metrics': detailed diffs vs class leader
"""

def _build_user_message(question: str, driver_id: str = None, car_no: int = None) -> str:
    schema = _get_schema_cached()
    
    # --- NEW: Context Injection ---
    context_str = ""
    if driver_id and car_no:
        context_str = (
            f"CURRENT DRIVER CONTEXT:\n"
            f"- User is analyzing Driver ID: '{driver_id}' (Car #{car_no}).\n"
            f"- Interpret 'my', 'me', 'this driver' as applying to '{driver_id}' / Car {car_no}.\n"
        )

    examples = """
    Example 1: "Rank my sectors by time loss" (Assuming current driver D_7)
    SQL: SELECT main_sector, avg_loss_s FROM driver_opportunities WHERE driver_id = 'D_7' ORDER BY avg_loss_s DESC;

    Example 2: "Best lap for Car 16"
    SQL: SELECT lap_time_s FROM laps WHERE car_no = 16 AND lap_time_s > 30 ORDER BY lap_time_s ASC LIMIT 1;
    """

    return f"""
    User question: {question}

    {context_str}

    Database tables:
    {schema}

    {examples}

    Instructions:
    Propose ONE SQL query in a ```sql ... ``` block.
    Then explain the answer in plain English.
    """


def _strip_sql_block(text: str) -> str:
    """
    Remove any ```sql ... ``` block from the LLM response, leaving
    just the natural-language explanation.
    """
    return re.sub(r"```sql.*?```", "", text, flags=re.DOTALL | re.IGNORECASE).strip()


# ------------------------------------------------------------------------------
# LLM / CHAT LOGIC (result explanation)
# ------------------------------------------------------------------------------

EXPLAIN_SYSTEM_PROMPT = """You are ApexAI Co-Pilot, explaining telemetry query RESULTS to a Toyota GR Cup race engineer.

Your job:
- Read the user's question and a small result table.
- Answer the question directly using at most TWO short sentences.
- Focus on what the numbers mean for the driver (e.g., consistent / inconsistent, faster / slower).
- DO NOT mention SQL, databases, tables, or how the query was built.
- DO NOT restate the question or describe the query; just give the conclusion with key numbers.
"""


def _extract_sql_from_text(text: str) -> Optional[str]:
    """Pull the first ```sql ... ``` block from the LLM response, if any."""
    m = re.search(r"```sql(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if not m:
        return None
    return m.group(1).strip()


def _build_explanation_from_result(
    client,
    question: str,
    sql: Optional[str],
    result_markdown: str,
) -> str:
    """Second LLM call: explain the numeric result in <= 2 sentences."""
    if not sql:
        prompt = (
            f"{EXPLAIN_SYSTEM_PROMPT}\n\n"
            f"Question: {question}\n\n"
            f"Result table:\n{result_markdown}"
        )
    else:
        prompt = (
            f"{EXPLAIN_SYSTEM_PROMPT}\n\n"
            f"Question: {question}\n\n"
            f"SQL that was run (for context only, do NOT describe it):\n"
            f"```sql\n{sql}\n```\n\n"
            f"Result table (Markdown):\n{result_markdown}"
        )

    try:
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        explanation = (resp.text or "").strip()
    except Exception as e:
        explanation = f"Error generating explanation: {e}"

    return explanation


def chat_with_apex_ai(
    question: str,
    current_driver_id: str = None,
    current_car_no: int = None,
) -> Dict[str, Any]:
    """
    Entry point. Now accepts current_driver_id/car_no to fix "my sectors" queries.

    Flow:
      1) LLM generates SQL.
      2) We run the SQL against DuckDB.
      3) We call the LLM again with the result table to get a short explanation.
    """
    user_msg = _build_user_message(question, current_driver_id, current_car_no)
    client = get_gemini_client()

    # --- 1) NL -> SQL (we only keep SQL; ignore this explanation) ---
    prompt = f"{CHAT_SYSTEM_PROMPT}\n\n{user_msg}"

    try:
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        content = (resp.text or "").strip()
    except Exception as e:
        content = f"Error calling Gemini: {e}"

    sql = _extract_sql_from_text(content)

    # --- 2) Run SQL against DuckDB ---
    if sql:
        try:
            df = run_sql(sql)
            if not df.empty:
                sql_result_md = df.to_markdown(index=False)
            else:
                sql_result_md = "(Query returned no results)"
        except Exception as e:
            sql_result_md = f"Error running SQL: {e}"
            df = pd.DataFrame()
    else:
        sql_result_md = "(No SQL query was generated)"
        df = pd.DataFrame()

    # --- 3) Ask LLM to explain the RESULT, not the query ---
    explanation = _build_explanation_from_result(
        client=client,
        question=question,
        sql=sql,
        result_markdown=sql_result_md,
    )

    return {
        "answer_text": explanation,          # <= this is what you show in section 3
        "sql": sql,                          # section 1
        "sql_result_markdown": sql_result_md # section 2
    }

