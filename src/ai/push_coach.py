# src/ai/push_coach.py

import json
import os
from pathlib import Path
from typing import Optional, Dict, Any, List
import time


from google import genai 

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------
INSIGHTS_PATH = Path("data_processed/driver_insights.json")
COACHING_OUTPUT_PATH = Path("data_processed/driver_coaching.json")

# --- simple rate limiting / retry config for LLM calls ---
MAX_LLM_RETRIES = 3          # how many times to retry on rate limit
MIN_CALL_SPACING_SEC = 5.0   # minimum seconds between calls
RATE_LIMIT_BACKOFF_SEC = 5.0 # base backoff when we see rate-limit
_last_llm_call_ts: float = 3.0


# Gemini model (pick the one you want; this is a good default)
GEMINI_MODEL = "gemini-2.5-flash"  # or "gemini-2.0-pro" if your key allows

API_KEY_PATH = "config/openai_key.txt"  # file now holds your GEMINI key


def load_api_key() -> str:
    if not os.path.exists(API_KEY_PATH):
        raise FileNotFoundError(
            f"Missing Gemini API key file at {API_KEY_PATH}. "
            "Create it and put your API key inside."
        )
    with open(API_KEY_PATH, "r") as f:
        return f.read().strip()


# Single global Gemini client
_gemini_client: genai.Client | None = None


def get_gemini_client() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=load_api_key())
    return _gemini_client




SYSTEM_PROMPT = """You are a professional race engineer in the Toyota GR Cup.
You coach drivers using timing and telemetry data (brake pressure, throttle, steering, G-forces).
You never guess exact pedal percentages. You speak in practical racing language.

You will be given JSON describing:
- One driver (class, car number, best lap, ideal lap, total time opportunity).
- One sector or corner (average time loss, consistency, best gain).
- Physics vs class-best (brake point, brake pressure, throttle usage, steering stability).

Your job:
1) Explain in 3–5 sentences:
   - How much time they are losing in this sector vs ideal/class-best.
   - What the braking, throttle, and steering data suggest about their technique
     (e.g. braking too early, too soft, hesitating on throttle, unstable steering).
   - One or two very specific things they should try differently next session.
2) Avoid made-up numbers. Use relative language: "earlier", "later", "harder", "softer", "more committed", etc.
3) Write like a calm but direct race engineer talking to a club-level driver.

Respond ONLY in valid JSON with the following keys:
- "short_title": a short descriptive title for this coaching point (max ~8 words).
- "emoji_tag": a single emoji that matches the theme (for example: "🔥", "⏱️", "🛑", "⚡").
- "coaching_text": the full coaching explanation as a single string.
"""


def _clean_json_from_model(text: str) -> Dict[str, Any]:
    """
    Robustly extract a JSON object from the model response.
    Handles Markdown fences, raw text wrapping, and partial failures.
    """
    import re
    import json

    # 1. Try to find JSON inside ```json ... ``` or just ``` ... ```
    # This regex looks for the content *inside* the fences
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    
    # 2. Cleanup: sometimes models add "Here is the JSON:" prefixes even outside fences
    # We look for the first '{' and the last '}'
    start_idx = text.find("{")
    end_idx = text.rfind("}")
    
    if start_idx != -1 and end_idx != -1:
        text = text[start_idx : end_idx + 1]

    # 3. Try to parse
    try:
        data = json.loads(text)
        
        # Ensure we have the keys we expect. If the model returned a list or something else, force it.
        if not isinstance(data, dict):
            raise ValueError("Parsed content is not a dict")
            
        return {
            "short_title": data.get("short_title", "Coaching Tip"),
            "emoji_tag": data.get("emoji_tag", "💡"),
            "coaching_text": data.get("coaching_text", "No text provided"),
        }
        
    except Exception:
        # 4. Fallback: If parsing fails completely, treat the whole raw text as the advice
        # Clean up the raw text a bit so it doesn't look like code
        clean_text = text.replace("```json", "").replace("```", "").strip()
        return {
            "short_title": "Coaching Tip",
            "emoji_tag": "🏁",
            "coaching_text": clean_text,
        }


def _build_sector_payload(driver: Dict[str, Any], sector_insight: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a compact JSON payload for one driver + one sector that we send to the LLM.
    """
    return {
        "driver_summary": {
            "race_id": driver.get("race_id"),
            "session_id": driver.get("session_id"),
            "driver_id": driver.get("driver_id"),
            "car_no": driver.get("car_no"),
            "class": driver.get("class"),
            "best_lap_s": driver.get("best_lap_s"),
            "ideal_lap_s": driver.get("ideal_lap_s"),
            "total_time_opportunity_s": driver.get("total_time_opportunity_s"),
        },
        "sector_insight": sector_insight,
    }


def _call_llm_for_sector(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Call the LLM with simple rate limiting + retry.

    We:
    - enforce a small delay between calls (MIN_CALL_SPACING_SEC)
    - retry a few times on rate-limit / 429 style errors
    - always return a dict with short_title / emoji_tag / coaching_text
    """
    global _last_llm_call_ts

    # ----- build prompt (reuse your existing structure) -----
    prompt = f"""
You are an experienced race engineer coaching a Toyota GR86 driver.

Use this JSON describing the driver, their lap, and one problematic sector
to give actionable coaching advice. Focus on braking point, brake pressure,
throttle usage, and where to gain time safely.

Return STRICT JSON with this schema:

{{
  "short_title": "very short title for this sector",
  "emoji_tag": "one emoji",
  "coaching_text": "2-4 sentences of clear coaching"
}}

Here is the sector data (JSON):

{json.dumps(payload, indent=2)}
"""

    # ----- helper that actually calls Gemini -----
    def _do_call():
        client = get_gemini_client()
        return client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt
        )

    last_error: Exception | None = None

    for attempt in range(MAX_LLM_RETRIES):
        # 1) enforce spacing between calls
        elapsed = time.time() - _last_llm_call_ts
        if elapsed < MIN_CALL_SPACING_SEC:
            time.sleep(MIN_CALL_SPACING_SEC - elapsed)

        try:
            resp = _do_call()
            _last_llm_call_ts = time.time()

            txt = (resp.text or "").strip()

            # Try to parse JSON first
            try:
                data = json.loads(txt)
                # basic sanity check
                if not isinstance(data, dict):
                    raise ValueError("LLM JSON is not an object")

                return {
                    "short_title": data.get("short_title", "Coaching"),
                    "emoji_tag": data.get("emoji_tag", "💡"),
                    "coaching_text": data.get("coaching_text", txt),
                }

            except Exception:
                # If parsing fails, just wrap the raw text
                return {
                    "short_title": "Coaching",
                    "emoji_tag": "💡",
                    "coaching_text": txt,
                }

        except Exception as e:
            last_error = e
            msg = str(e).lower()

            # 2) if it's a rate-limit style error, back off and retry
            if "rate limit" in msg or "429" in msg or "quota" in msg:
                sleep_for = RATE_LIMIT_BACKOFF_SEC * (attempt + 1)
                time.sleep(sleep_for)
                continue

            # 3) other error: don't retry, just raise
            raise

    # If we get here, all retries failed – return a graceful "error" coaching
    return {
        "short_title": "Coaching Error",
        "emoji_tag": "⚠️",
        "coaching_text": f"Error calling LLM after {MAX_LLM_RETRIES} attempts: {last_error}",
    }



def generate_push_coaching(
    driver_ids: Optional[List[str]] = None,
    max_sectors_per_driver: int = 3,
) -> None:
    """
    Main entry point: read driver_insights.json, generate coaching per sector,
    and write driver_coaching.json.
    """
    if not INSIGHTS_PATH.exists():
        raise FileNotFoundError(f"Insights JSON not found at {INSIGHTS_PATH}")

    with open(INSIGHTS_PATH, "r") as f:
        insights = json.load(f)

    if driver_ids is None:
        driver_ids = sorted(insights.keys())

    coaching_output: Dict[str, Any] = {}

    for driver_id in driver_ids:
        driver = insights.get(driver_id)
        if not driver:
            continue

        opportunities = driver.get("opportunities", [])
        # Sort by largest time loss, just in case
        opportunities = sorted(
            opportunities, key=lambda o: o.get("avg_sector_delta_s", 0.0), reverse=True
        )[:max_sectors_per_driver]

        driver_coaching_entries = []

        for opp in opportunities:
            payload = _build_sector_payload(driver, opp)
            try:
                result = _call_llm_for_sector(payload)
            except Exception as e:
                # Fail gracefully; store the error as text
                result = {
                    "short_title": "Coaching Error",
                    "emoji_tag": "⚠️",
                    "coaching_text": f"Error calling LLM: {e}",
                }

            entry = {
                "sector": opp.get("sector"),
                "main_sector": opp.get("main_sector"),
                "turn_name": opp.get("turn_name"),
                "time_loss_avg": opp.get("time_loss_avg"),
                "avg_sector_delta_s": opp.get("avg_sector_delta_s"),
                "physics_avg": opp.get("physics_avg"),
                "physics_vs_class": opp.get("physics_vs_class"),
                "short_title": result.get("short_title"),
                "emoji_tag": result.get("emoji_tag"),
                "coaching_text": result.get("coaching_text"),
            }
            driver_coaching_entries.append(entry)

        coaching_output[driver_id] = {
            "race_id": driver.get("race_id"),
            "session_id": driver.get("session_id"),
            "driver_id": driver_id,
            "car_no": driver.get("car_no"),
            "class": driver.get("class"),
            "total_time_opportunity_s": driver.get("total_time_opportunity_s"),
            "coaching": driver_coaching_entries,
        }

    COACHING_OUTPUT_PATH.parent.mkdir(exist_ok=True, parents=True)
    with open(COACHING_OUTPUT_PATH, "w") as f:
        json.dump(coaching_output, f, indent=2)

    print(f"✅ Driver coaching written to {COACHING_OUTPUT_PATH}")


if __name__ == "__main__":
    # Example: generate coaching for all drivers
    generate_push_coaching()
