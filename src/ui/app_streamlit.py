import json
from pathlib import Path
import sys
import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------
# Make sure project root is on sys.path so we can import src.ai.chat_agent
# --------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    # If your chat agent is at src/ai/chat_agent.py relative to PROJECT_ROOT
    from src.ai.chat_agent import chat_with_apex_ai  # type: ignore
except Exception as e:
    chat_with_apex_ai = None
    # We suppress the error here to keep the UI clean, but log it to console
    print(f"Chat module import failed: {e}")


# -------------------------------------------------------------------
# Config
# -------------------------------------------------------------------
DB_PATH = "apex_copilot.duckdb"
INSIGHTS_PATH = Path("data_processed/driver_insights.json")
COACHING_PATH = Path("data_processed/driver_coaching.json")

# Set this to your track map image (PNG/JPG)
# e.g. put the file at assets/sebring_track_map.png
TRACK_MAP_PATH = Path("data_raw/sebring_track_map.png")

# Map Sectors to Lap Progress Ranges (Approximate from Track Map)
# S1: 0.0 - 0.33, S2: 0.33 - 0.66, S3: 0.66 - 1.0
SECTOR_RANGES = {
    "Full Lap": (0.0, 1.0),
    "Sector 1 (Turns 1-6)": (0.0, 0.33),
    "Sector 2 (Hairpin/Fangio)": (0.33, 0.67),
    "Sector 3 (Sunset Bend)": (0.67, 1.0),
}

# -------------------------------------------------------------------
# DuckDB Connection + Loaders
# -------------------------------------------------------------------
@st.cache_resource
def get_con():
    """Cached DuckDB connection."""
    return duckdb.connect(DB_PATH, read_only=True)


@st.cache_data
def load_drivers():
    con = get_con()
    try:
        df = con.execute(
            "SELECT driver_id, car_no, class FROM drivers ORDER BY car_no"
        ).df()
    except Exception as e:
        st.error(f"Error loading drivers: {e}")
        return pd.DataFrame()
    return df


@st.cache_data
def load_lap_deltas(driver_id: str):
    con = get_con()
    try:
        df = con.execute(
            """
            SELECT lap_no, lap_time_s, ideal_lap_time_s, delta_lap_s
            FROM lap_deltas
            WHERE driver_id = ?
            ORDER BY lap_no
            """,
            [driver_id],
        ).df()
    except Exception:
        df = pd.DataFrame()
    return df


@st.cache_data
def load_main_sector_deltas(driver_id: str):
    con = get_con()
    try:
        df = con.execute(
            """
            SELECT main_sector, AVG(delta_main_s) AS avg_delta_s
            FROM main_sector_deltas
            WHERE driver_id = ?
            GROUP BY main_sector
            ORDER BY main_sector
            """,
            [driver_id],
        ).df()
    except Exception:
        df = pd.DataFrame()
    return df


@st.cache_data
def load_sector_deltas(driver_id: str):
    con = get_con()
    try:
        df = con.execute(
            """
            SELECT sector_id, main_sector, avg_delta_s, consistency_s
            FROM sector_deltas
            WHERE driver_id = ?
            ORDER BY main_sector, sector_id
            """,
            [driver_id],
        ).df()
    except Exception:
        df = pd.DataFrame()
    return df


@st.cache_data
def load_valid_lap_physics(driver_id: str):
    con = get_con()
    try:
        df = con.execute(
            """
            SELECT 
                lap,
                samples,
                avg_speed_kph,
                avg_throttle,
                full_throttle_ratio,
                coasting_ratio,
                max_brake_pressure,
                avg_brake_pressure,
                braking_ratio,
                steering_variability,
                max_braking_g,
                max_accel_g,
                max_cornering_g,
                lap_time_s
            FROM lap_physics_valid
            WHERE driver_id = ?
            ORDER BY lap
            """,
            [driver_id],
        ).df()
    except Exception:
        df = pd.DataFrame()
    return df


@st.cache_data
def load_lap_summary(driver_id: str):
    """Get best lap, ideal lap and delta directly from lap_deltas (source of truth)."""
    con = get_con()
    try:
        row = con.execute(
            """
            SELECT
                MIN(lap_time_s)       AS best_lap_s,
                MIN(ideal_lap_time_s) AS ideal_lap_s
            FROM lap_deltas
            WHERE driver_id = ?
            """,
            [driver_id],
        ).fetchone()
    except Exception:
        return None

    if not row:
        return None

    best_lap_s, ideal_lap_s = row
    if best_lap_s is None or ideal_lap_s is None:
        return None

    delta = best_lap_s - ideal_lap_s
    return {
        "best_lap_s": float(best_lap_s),
        "ideal_lap_s": float(ideal_lap_s),
        "delta_to_ideal_s": float(delta),
        "total_time_opportunity_s": float(delta),
    }


@st.cache_data
def load_driver_insights_json():
    if not INSIGHTS_PATH.exists():
        return {}
    try:
        data = json.loads(INSIGHTS_PATH.read_text())
    except Exception as e:
        st.error(f"Error reading driver_insights.json: {e}")
        return {}
    return data


@st.cache_data
def load_driver_coaching_json():
    if not COACHING_PATH.exists():
        return {}
    try:
        data = json.loads(COACHING_PATH.read_text())
    except Exception:
        return {}
    return data


# -------------------------------------------------------------------
# UI Helpers
# -------------------------------------------------------------------
def get_driver_label(row):
    return f"#{int(row['car_no'])} - {row['driver_id']} ({row['class']})"


def layout_metrics_for_driver(summary: dict | None):
    """Top summary cards for a driver."""
    if not summary:
        st.warning("No lap summary found for this driver.")
        return

    best = summary.get("best_lap_s")
    ideal = summary.get("ideal_lap_s")

    with st.container():
        col1, col2, col3 = st.columns(3)
        if best is not None:
            col1.metric("Best Lap", f"{best:.3f} s")
        else:
            col1.metric("Best Lap", "–")
        if ideal is not None:
            col2.metric("Ideal Lap", f"{ideal:.3f} s")
        else:
            col2.metric("Ideal Lap", "–")

        if best is not None and ideal is not None:
            opp = best - ideal
            col3.metric("Time on Table", f"{opp:.3f} s", delta_color="inverse")
        else:
            col3.metric("Time on Table", "–")


def layout_overview_ai_coach(
    insight: Optional[Dict[str, Any]],
    sector_main_df: Optional[pd.DataFrame],
    lap_df: Optional[pd.DataFrame],
) -> None:
    """
    High-level ApexAI summary for the Overview tab.
    Uses overall lap metrics + sector deltas + lap consistency.
    """
    st.markdown("### 🤖 ApexAI Coach – Session Summary")

    with st.container(border=True):
        if not insight:
            st.write("Not enough data for this driver yet.")
            return

        best = insight.get("best_lap_s")
        ideal = insight.get("ideal_lap_s")
        time_on_table = insight.get(
            "total_time_opportunity_s", insight.get("delta_to_ideal_s", None)
        )
        class_best = insight.get("class_best_lap_s")
        class_avg = insight.get("class_avg_lap_s")

        # ---------- Top metrics row ----------
        m1, m2, m3 = st.columns(3)

        with m1:
            if best is not None and ideal is not None:
                gap_to_ideal = best - ideal
                st.markdown("**Pace vs Ideal**")
                st.markdown(
                    f"<span style='font-size:1.3rem;'>+{gap_to_ideal:.3f}s</span>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown("**Pace vs Ideal**")
                st.write("—")

        with m2:
            if sector_main_df is not None and not sector_main_df.empty:
                worst_row = sector_main_df.sort_values(
                    "avg_delta_s", ascending=False
                ).iloc[0]
                worst_sector = str(worst_row["main_sector"])
                worst_loss = float(worst_row["avg_delta_s"])
                st.markdown("**Biggest Opportunity**")
                st.markdown(
                    f"<span style='font-size:1.3rem;'>S{worst_sector[-1]} · +{worst_loss:.3f}s</span>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown("**Biggest Opportunity**")
                st.write("—")

        with m3:
            consistency_text = "—"
            if lap_df is not None and not lap_df.empty and "lap_time_s" in lap_df.columns:
                best_lap_time = float(lap_df["lap_time_s"].min())
                window = 0.7
                within = int(
                    (lap_df["lap_time_s"] <= best_lap_time + window).sum()
                )
                total = int(len(lap_df))
                pct = (within / total * 100) if total > 0 else 0
                consistency_text = f"{within}/{total} laps ({pct:.0f}%)"
            st.markdown("**Consistency Window**")
            st.markdown(
                f"<span style='font-size:1.3rem;'>{consistency_text}</span>",
                unsafe_allow_html=True,
            )

        st.markdown("---")

        # ---------- Narrative bullets ----------
        bullet_lines: List[str] = []

        if best is not None and ideal is not None and time_on_table is not None:
            bullet_lines.append(
                f"- **Pace:** Your best lap is **{best:.3f}s** vs an ideal of **{ideal:.3f}s**, "
                f"leaving about **{time_on_table:.3f}s** on the table."
            )

        if class_best is not None or class_avg is not None:
            pieces = []
            if class_best is not None:
                diff_best = best - class_best if best is not None else None
                if diff_best is not None:
                    sign = "ahead of" if diff_best < 0 else "behind"
                    pieces.append(f"~{abs(diff_best):.3f}s {sign} class best")
            if class_avg is not None and best is not None:
                diff_avg = best - class_avg
                sign = "ahead of" if diff_avg < 0 else "slower than"
                pieces.append(f"~{abs(diff_avg):.3f}s {sign} class average")
            if pieces:
                bullet_lines.append(f"- **Class context:** " + ", ".join(pieces) + ".")

        if sector_main_df is not None and not sector_main_df.empty:
            worst_row = sector_main_df.sort_values(
                "avg_delta_s", ascending=False
            ).iloc[0]
            worst_sector = str(worst_row["main_sector"])
            worst_loss = float(worst_row["avg_delta_s"])
            bullet_lines.append(
                f"- **Focus corner:** Most time is leaking in **{worst_sector}** "
                f"(~**+{worst_loss:.3f}s** per lap). Start your work there before fine-tuning other sectors."
            )

        if lap_df is not None and not lap_df.empty and "lap_time_s" in lap_df.columns:
            best_lap_time = float(lap_df["lap_time_s"].min())
            window = 0.7
            within = int(
                (lap_df["lap_time_s"] <= best_lap_time + window).sum()
            )
            total = int(len(lap_df))
            pct = (within / total * 100) if total > 0 else 0
            consistency_label = "very consistent" if pct >= 75 else "a bit up-and-down"
            bullet_lines.append(
                f"- **Consistency:** {within} of {total} laps (≈**{pct:.0f}%**) are within "
                f"{window:.1f}s of your best, so your pace is **{consistency_label}**. "
                "Aim to grow the number of laps in this window."
            )

        if bullet_lines:
            st.markdown("\n".join(bullet_lines))
        else:
            st.write("Telemetry is still loading for this driver – try another selection.")

        st.caption(
            "Use this as your high-level briefing, then dive into the Track Map tab and Ask ApexAI for corner-specific coaching."
        )


def layout_coaching_insights(insight: dict | None, coaching: dict | None):
    """Grid of sector coaching cards."""
    if not insight:
        st.info("No coaching insights available for this driver.")
        return

    driver_id = insight.get("driver_id")
    ai_entries = []

    # Robustly find the coaching list
    if coaching and driver_id in coaching:
        driver_data = coaching[driver_id]
        ai_entries = driver_data.get("coaching", [])

    # Index AI coaching by sector
    ai_by_sector = {}
    for c in ai_entries:
        sector_key = c.get("sector") or c.get("main_sector")
        if sector_key:
            ai_by_sector[sector_key] = c

    opportunities = insight.get("opportunities", [])
    if not opportunities:
        st.info("No sector opportunity data found.")
        return

    # Sort in track order: S1 -> S2 -> S3, then by time loss (largest first) within each sector
    sector_order = {"S1": 1, "S2": 2, "S3": 3}

    def sort_key(o: dict):
        sector = o.get("main_sector") or o.get("sector")
        order_val = sector_order.get(sector, 99)
        time_loss = o.get("time_loss_avg", 0.0) or o.get("avg_sector_delta_s", 0.0)
        return (order_val, -time_loss)

    opportunities = sorted(opportunities, key=sort_key)


    # Render as cards, 3 per row
    for i in range(0, len(opportunities), 3):
        cols = st.columns(3)
        for col, opp in zip(cols, opportunities[i: i + 3]):
            sector = opp.get("main_sector") or opp.get("sector") or "Sector"
            turn_name = opp.get("turn_name", "")
            loss = opp.get("time_loss_avg") or opp.get("avg_sector_delta_s") or 0.0
            consistency = opp.get("consistency") or opp.get("sector_consistency_s") or 0.0
            phys = opp.get("physics_avg", {}) or {}

            ai = ai_by_sector.get(sector)
            if ai and isinstance(ai.get("coaching_text"), str):
                # Skip API error noise if present
                if "Rate limit exceeded" in ai["coaching_text"]:
                    ai = None

            emoji = (ai or {}).get("emoji_tag", "⚠️")
            title = (ai or {}).get("short_title", f"{sector} Focus")

            severity_color = "#FF4B4B" if loss >= 0.4 else "#FFA62B" if loss >= 0.2 else "#21BA45"

            with col:
                st.markdown(
                    f"""
                    <div class="coach-card">
                      <div class="coach-badge">{sector}</div>
                      <div style="display:flex; justify-content:space-between; align-items:center; margin-top:0.35rem;">
                        <div style="font-weight:600;">{emoji} {title}</div>
                        <div style="font-size:0.9rem; color:{severity_color};">
                          +{loss:.3f}s
                        </div>
                      </div>
                      <div style="font-size:0.8rem; opacity:0.8; margin-top:0.2rem;">
                        {turn_name}
                      </div>
                      <div style="font-size:0.8rem; opacity:0.75; margin-top:0.35rem;">
                        Consistency: {consistency:.3f}s
                      </div>
                      <div style="font-size:0.8rem; opacity:0.75; margin-top:0.15rem;">
                        Brake: {phys.get('brake_bar', '–')} bar · Throttle: {phys.get('throttle_pct', '–')}%
                      </div>
                      <div style="font-size:0.85rem; margin-top:0.55rem;">
                        {(ai or {}).get('coaching_text', 'AI coaching not available yet for this sector.')}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )


# -------------------------------------------------------------------
# Streamlit App Main
# -------------------------------------------------------------------
def main():
    st.set_page_config(
        page_title="ApexAI - Toyota GR Cup",
        page_icon="🏎️",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # Initialize chat history for the Ask ApexAI tab
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    # --- Global styling ---
    st.markdown(
        """
        <style>
        .main {
            background-color: #05070A;
        }
        section[data-testid="stSidebar"] {
            background-color: #111318;
        }
        .metric-card {
            padding: 0.75rem 1rem;
            border-radius: 0.75rem;
            border: 1px solid rgba(255,255,255,0.08);
            background: radial-gradient(circle at top left, #23252d 0, #111318 55%);
        }
        .coach-card {
            border-radius: 0.75rem;
            border: 1px solid rgba(255,255,255,0.07);
            padding: 0.75rem 1rem;
            background-color: #151823;
        }
        .coach-badge {
            font-size: 0.75rem;
            letter-spacing: .08em;
            text-transform: uppercase;
            color: #f5f5f5;
            opacity: 0.65;
        }
        .stTabs [data-baseweb="tab-list"] {
            gap: 0.4rem;
        }
        .stTabs [data-baseweb="tab"] {
            border-radius: 999px;
            padding: 0.35rem 0.9rem;
        }
        .track-card {
            border-radius: 0.75rem;
            border: 1px solid rgba(255,255,255,0.07);
            padding: 0.75rem 1rem;
            background-color: #151823;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # --- Sidebar ---
    with st.sidebar:
        st.image("data_raw/logo.jpeg", width=150)
        st.title("ApexAI Co-Pilot")
        st.caption("Sebring Race 1 Analysis")

        st.markdown("---")

        drivers_df = load_drivers()
        if drivers_df.empty:
            st.error("No drivers found in DB.")
            return

        driver_options = {get_driver_label(row): row for _, row in drivers_df.iterrows()}
        selected_label = st.selectbox("Select Driver", list(driver_options.keys()))
        sel_row = driver_options[selected_label]
        selected_driver_id = sel_row["driver_id"]
        selected_class = sel_row["class"]

        # Sidebar driver card
        st.markdown(
            f"""
            <div class="metric-card">
              <div class="coach-badge">Current Driver</div>
              <div style="display:flex; justify-content:space-between; align-items:flex-end; margin-top:0.35rem;">
                <div>
                  <div style="font-size:1.6rem; font-weight:700;">#{int(sel_row['car_no'])}</div>
                  <div style="font-size:0.9rem; opacity:0.8;">ID: {selected_driver_id}</div>
                </div>
                <div style="text-align:right; font-size:0.85rem; opacity:0.8;">
                  Class<br/><strong>{selected_class}</strong>
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # --- Load insights & coaching JSON ---
    insights_json = load_driver_insights_json()
    coaching_json = load_driver_coaching_json()
    insight = insights_json.get(selected_driver_id)
    lap_summary = load_lap_summary(selected_driver_id)

    # --- Main Tabs ---
    tab_overview, tab_track, tab_chat = st.tabs(
        ["🏁 Overview", "🗺️ Track Map", "💬 Ask ApexAI"]
    )

    # -------------------------------------------------------------------
    # 1. Overview Tab
    # -------------------------------------------------------------------
    with tab_overview:
        st.subheader("Sector Analysis & Coaching")

        # --------------------
        # ROW 1 – Hero metrics
        # --------------------
        layout_metrics_for_driver(insight)

        # --------------------------------------------------
        # ROW 2 – Two visualizations side by side
        #   Left  : Time Loss by Main Sector (bar chart)
        #   Right : Lap Time Consistency (line chart)
        # --------------------------------------------------

        sector_main_df = None
        lap_df = None
        col_left, col_right = st.columns(2)

        # Left: Sector time loss bar
        with col_left:
            st.markdown("#### ⏱️ Time Loss by Main Sector")

            sector_main_df = load_main_sector_deltas(selected_driver_id)

            if sector_main_df is None or sector_main_df.empty:
                st.info("No sector delta data available yet for this driver.")
            else:
                # Make sure sectors appear in S1 → S2 → S3 order
                sector_order = {"S1": 1, "S2": 2, "S3": 3}
                sector_main_df["sort_key"] = sector_main_df["main_sector"].map(sector_order)
                sector_main_df = sector_main_df.sort_values("sort_key")

                # Color: more time loss = warmer color
                colors = sector_main_df["avg_delta_s"].apply(
                    lambda v: "#f973a0" if v > 0 else "#4ade80"
                )

                fig_sectors = px.bar(
                    sector_main_df,
                    x="main_sector",
                    y="avg_delta_s",
                    labels={"main_sector": "Sector", "avg_delta_s": "Avg Δ vs Ideal (s)"},
                    text_auto=".3f",
                )
                fig_sectors.update_traces(
                    marker_color=colors,
                    textposition="outside",
                    cliponaxis=False,
                )
                fig_sectors.update_layout(
                    template="plotly_dark",
                    yaxis_title="Avg Time Loss (s)",
                    xaxis_title="",
                    showlegend=False,
                    bargap=0.35,
                    margin=dict(l=10, r=10, t=10, b=10),
                )
                fig_sectors.update_yaxes(zeroline=True, zerolinewidth=1, zerolinecolor="#888")
                st.plotly_chart(fig_sectors, width='stretch')
                st.caption("Red bars = biggest time loss sectors to attack first.")


        # Right: Lap time consistency line
        with col_right:
            st.markdown("#### 🏁 Lap Time Consistency")

            lap_df = load_lap_deltas(selected_driver_id)

            if lap_df is None or lap_df.empty:
                st.info("No valid lap data available for this driver yet.")
            else:
                # Rename for nicer legend
                plot_df = lap_df.rename(
                    columns={
                        "lap_time_s": "Actual Lap",
                        "ideal_lap_time_s": "Ideal Lap",
                    }
                )

                fig_laps = px.line(
                    plot_df,
                    x="lap_no",
                    y=["Actual Lap", "Ideal Lap"],
                    labels={"lap_no": "Lap", "value": "Lap Time (s)", "variable": ""},
                )

                # Style: thicker actual line + markers, thin ideal reference line
                fig_laps.update_traces(selector=dict(name="Actual Lap"), line=dict(width=3))
                fig_laps.update_traces(
                    selector=dict(name="Actual Lap"),
                    mode="lines+markers",
                    marker=dict(size=6),
                )
                fig_laps.update_traces(
                    selector=dict(name="Ideal Lap"),
                    line=dict(width=2, dash="dot"),
                )

                fig_laps.update_layout(
                    template="plotly_dark",
                    margin=dict(l=10, r=10, t=10, b=10),
                )
                st.plotly_chart(fig_laps, width='stretch')

                best_lap_time = plot_df["Actual Lap"].min()
                worst_lap_time = plot_df["Actual Lap"].max()
                spread = worst_lap_time - best_lap_time
                st.caption(
                    f"Actual lap times range from **{best_lap_time:.3f}s** to **{worst_lap_time:.3f}s** "
                    f"({spread:.3f}s spread). A smaller spread means more consistent pace."
                )


        # --------------------------------------------------
        # ROW 3 – ApexAI Coach overall summary
        # --------------------------------------------------
        layout_overview_ai_coach(insight, sector_main_df, lap_df)


    # -------------------------------------------------------------------
    # 2. Track Map Tab
    # -------------------------------------------------------------------
    with tab_track:
        

        # Full-width track map
        st.markdown("### Sebring Track Map")
        if TRACK_MAP_PATH.exists():
            st.image(str(TRACK_MAP_PATH), width='stretch')
        else:
            st.warning(
                f"Track map image not found at `{TRACK_MAP_PATH}`. "
                "Place your PNG/JPG there or update TRACK_MAP_PATH."
            )

        # Sector summary BELOW the map
        st.markdown("### Sector Summary")
        sector_main_df = load_main_sector_deltas(selected_driver_id)
        if not sector_main_df.empty:
            st.dataframe(
                sector_main_df.rename(
                    columns={
                        "main_sector": "Sector",
                        "avg_delta_s": "Avg Δ vs Ideal (s)",
                    }
                ).style.format({"Avg Δ vs Ideal (s)": "{:.3f}"}),
                width='stretch',
            )
        else:
            st.info("No sector delta data for this driver yet.")

        # -------------------
        # Sector Attack Plan (Moved from Overview → Track Map)
        # -------------------
        st.markdown("## 🛠 Sector Attack Plan")

        layout_coaching_insights(insight, coaching_json)
        

    # -------------------------------------------------------------------
    # 3. Chat Tab
    # -------------------------------------------------------------------
    with tab_chat:
        st.subheader("💬 Ask ApexAI")
        st.markdown(f"Asking about **{selected_label}**...")

        if chat_with_apex_ai is None:
            st.error("Chat module not initialized.")
        else:
            user_q = st.text_input(
                "Your question",
                placeholder="e.g. Is Car 7 consistent in Sector 1?",
            )

            col_ask, col_example = st.columns(2)
            ask_clicked = col_ask.button("Ask")
            example_clicked = col_example.button("Try example")

            if example_clicked:
                # Simple demo question
                user_q = "Is this driver consistent in Sector 1?"
                ask_clicked = True

            if ask_clicked and user_q:
                with st.spinner("Generating SQL and running the query..."):
                    current_car = int(sel_row["car_no"])
                    res = chat_with_apex_ai(user_q, selected_driver_id, current_car)

                sql = res.get("sql")
                sql_md = res.get("sql_result_markdown")
                explanation = res.get("answer_text")

                # 1) Show generated SQL
                st.markdown("### 1️⃣ Generated SQL")
                if sql:
                    st.code(sql, language="sql")
                else:
                    st.info("No SQL query was generated for this question.")

                # 2) Show query result
                st.markdown("### 2️⃣ Query Result")
                if sql_md:
                    st.markdown(sql_md, unsafe_allow_html=True)
                else:
                    st.info("No results returned for this query.")

                # 3) Show LLM explanation
                st.markdown("### 3️⃣ ApexAI Explanation")
                if explanation:
                    st.write(explanation)
                else:
                    st.info("No explanation was generated for this answer.")


if __name__ == "__main__":
    main()
