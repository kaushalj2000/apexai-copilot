import duckdb
import pandas as pd
import json
import os

DB_PATH = "apex_copilot.duckdb"
OUTPUT_FILE = "data_processed/driver_insights.json"

# Rough mapping from main sector -> key corner group at Sebring
SECTOR_TURN_LABELS = {
    "S1": "Opening sector (Turns 1–3)",
    "S2": "Middle sector (Hairpin / Fangio)",
    "S3": "Final sector (Sunset Bend, Turns 13–17)",
}


def generate_insights():
    print("🧠 Generating AI Coaching Insights...")

    # Make sure output folder exists
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

    con = duckdb.connect(DB_PATH)

    # ------------------------------------------------------------
    # 1) Driver opportunities (S1/S2/S3 average losses per driver)
    # ------------------------------------------------------------
    opportunities_df = con.execute("""
        SELECT *
        FROM driver_opportunities
        ORDER BY driver_id, avg_loss_s DESC
    """).df()

    if opportunities_df.empty:
        print("⚠️ No rows in driver_opportunities – did you run deltas.py?")
        con.close()
        return

    # ------------------------------------------------------------
    # 2) Driver lap summary (best lap vs ideal lap) + race_id
    # ------------------------------------------------------------
    driver_summary_df = con.execute("""
        SELECT
            l.driver_id,
            MIN(d.car_no)      AS car_no,
            MIN(d.class)       AS class,
            MIN(d.race_id)     AS race_id,
            MIN(l.lap_time_s)        AS best_lap_s,
            MIN(l.ideal_lap_time_s)  AS ideal_lap_s,
            MIN(l.delta_lap_s)       AS best_delta_s
        FROM lap_deltas l
        JOIN drivers d ON l.driver_id = d.driver_id
        GROUP BY l.driver_id
    """).df()

    # ------------------------------------------------------------
    # 3) Physics averages per driver (lap-level "style")
    # ------------------------------------------------------------
    physics_df = con.execute("""
        SELECT
            driver_id,
            AVG(avg_brake_pressure)      AS brake_pressure,
            AVG(avg_throttle)            AS throttle,
            AVG(full_throttle_ratio)     AS full_throttle_pct,
            AVG(max_cornering_g)         AS lat_g
        FROM valid_lap_physics
        GROUP BY driver_id
    """).df()

    # ------------------------------------------------------------
    # 4) Sector + class physics (fallback if we lack driver physics)
    # ------------------------------------------------------------
    sector_class_physics_df = con.execute("""
        SELECT
            d.class,
            ms.main_sector,
            AVG(v.avg_brake_pressure)  AS brake_pressure,
            AVG(v.avg_throttle)        AS throttle,
            AVG(v.full_throttle_ratio) AS full_throttle_pct,
            AVG(v.max_cornering_g)     AS lat_g
        FROM main_sector_deltas ms
        JOIN drivers d ON ms.driver_id = d.driver_id
        JOIN valid_lap_physics v
              ON v.driver_id = ms.driver_id
             AND v.lap = ms.lap_no
        GROUP BY d.class, ms.main_sector
    """).df()

    # ------------------------------------------------------------
    # 5) NEW: sector-level physics deltas vs class-best
    #    Aggregate per driver + main_sector to attach to insights
    # ------------------------------------------------------------
    sector_physics_deltas_df = con.execute("""
        SELECT
            driver_id,
            main_sector,
            AVG(brake_point_diff_vs_ref)         AS brake_point_diff_vs_ref,
            AVG(brake_max_bar_diff_vs_ref)       AS brake_max_bar_diff_vs_ref,
            AVG(throttle_full_ratio_diff_vs_ref) AS throttle_full_ratio_diff_vs_ref,
            AVG(steering_std_diff_vs_ref)        AS steering_std_diff_vs_ref,
            AVG(delta_class_s)                   AS avg_delta_class_s
        FROM physics_sector_metrics
        GROUP BY driver_id, main_sector
    """).df()

    con.close()

    insights_map: dict[str, dict] = {}
    drivers = opportunities_df["driver_id"].unique()

    for driver_id in drivers:
        # ---- Driver-level summary ----
        summary_row = driver_summary_df[driver_summary_df["driver_id"] == driver_id]
        if summary_row.empty:
            # Shouldn't happen, but be safe
            continue
        summary_row = summary_row.iloc[0]

        car_no = int(summary_row["car_no"])
        driver_class = summary_row["class"]
        race_id = summary_row.get("race_id", "sebring_R1")
        # For this hackathon dataset we only have one session: R1
        session_id = "R1"

        best_lap_s = float(summary_row["best_lap_s"])
        ideal_lap_s = float(summary_row["ideal_lap_s"])
        best_delta_s = float(summary_row["best_delta_s"])

        # ---- Driver-level physics (if available) ----
        phys_row = physics_df[physics_df["driver_id"] == driver_id]
        has_driver_physics = not phys_row.empty
        base_physics = None
        if has_driver_physics:
            phys_row = phys_row.iloc[0]
            base_physics = {
                "brake_bar": round(float(phys_row["brake_pressure"]), 1),
                "throttle_pct": round(float(phys_row["throttle"]), 1),
                "full_throttle": round(float(phys_row["full_throttle_pct"]), 2),
                "cornering_g": round(float(phys_row["lat_g"]), 2),
            }

        # ---- Top 3 worst sectors for this driver ----
        driver_ops = (
            opportunities_df[opportunities_df["driver_id"] == driver_id]
            .sort_values("avg_loss_s", ascending=False)
            .head(3)
        )

        driver_data: dict = {
            "race_id": race_id,
            "session_id": session_id,
            "driver_id": driver_id,
            "car_no": car_no,
            "class": driver_class,
            "best_lap_s": round(best_lap_s, 3),
            "ideal_lap_s": round(ideal_lap_s, 3),
            # Alias: total time opportunity is the gap to ideal
            "delta_to_ideal_s": round(best_delta_s, 3),
            "total_time_opportunity_s": round(best_delta_s, 3),
            "opportunities": [],
        }

        # Pre-filter physics sector deltas for this driver
        driver_sector_phys = sector_physics_deltas_df[
            sector_physics_deltas_df["driver_id"] == driver_id
        ]

        for _, row in driver_ops.iterrows():
            sector = row["main_sector"]
            avg_loss = float(row["avg_loss_s"])
            consistency = float(row["consistency_s"])
            best_gain = float(row["best_gain_s"])

            # --- Sector-specific physics (average style) ---
            if has_driver_physics:
                # Use the driver's own style metrics for all sectors
                physics_avg = base_physics
            else:
                # Fallback: use CLASS + SECTOR average physics
                fallback = sector_class_physics_df[
                    (sector_class_physics_df["class"] == driver_class)
                    & (sector_class_physics_df["main_sector"] == sector)
                ]
                if not fallback.empty:
                    f = fallback.iloc[0]
                    physics_avg = {
                        "brake_bar": round(float(f["brake_pressure"]), 1),
                        "throttle_pct": round(float(f["throttle"]), 1),
                        "full_throttle": round(float(f["full_throttle_pct"]), 2),
                        "cornering_g": round(float(f["lat_g"]), 2),
                    }
                else:
                    # Last resort: no physics for this class/sector
                    physics_avg = {
                        "brake_bar": None,
                        "throttle_pct": None,
                        "full_throttle": None,
                        "cornering_g": None,
                    }

            # --- NEW: sector physics deltas vs class-best ---
            phys_delta_row = driver_sector_phys[
                driver_sector_phys["main_sector"] == sector
            ]
            if not phys_delta_row.empty:
                pd_row = phys_delta_row.iloc[0]
                physics_vs_class = {
                    "brake_point_diff_vs_ref": round(
                        float(pd_row["brake_point_diff_vs_ref"]), 3
                    ),
                    "brake_max_bar_diff_vs_ref": round(
                        float(pd_row["brake_max_bar_diff_vs_ref"]), 2
                    ),
                    "throttle_full_ratio_diff_vs_ref": round(
                        float(pd_row["throttle_full_ratio_diff_vs_ref"]), 3
                    ),
                    "steering_std_diff_vs_ref": round(
                        float(pd_row["steering_std_diff_vs_ref"]), 3
                    ),
                    "avg_delta_class_s": round(
                        float(pd_row["avg_delta_class_s"]), 3
                    ),
                }
            else:
                physics_vs_class = {
                    "brake_point_diff_vs_ref": None,
                    "brake_max_bar_diff_vs_ref": None,
                    "throttle_full_ratio_diff_vs_ref": None,
                    "steering_std_diff_vs_ref": None,
                    "avg_delta_class_s": None,
                }

            # Optional label for the corner group in this sector
            turn_name = SECTOR_TURN_LABELS.get(sector)

            insight = {
                # Keep original key for Streamlit compatibility
                "sector": sector,
                # Also expose explicitly as main_sector for the LLM
                "main_sector": sector,
                "turn_name": turn_name,
                # Timing
                "time_loss_avg": round(avg_loss, 3),
                "avg_sector_delta_s": round(avg_loss, 3),
                "consistency": round(consistency, 3),
                "sector_consistency_s": round(consistency, 3),
                "best_gain_s": round(best_gain, 3),
                # Physics (style + deltas vs reference)
                "physics_avg": physics_avg,
                "physics_vs_class": physics_vs_class,
            }

            driver_data["opportunities"].append(insight)

        insights_map[driver_id] = driver_data

    # ------------------------------------------------------------
    # 6) Save to JSON
    # ------------------------------------------------------------
    with open(OUTPUT_FILE, "w") as f:
        json.dump(insights_map, f, indent=2)

    print(f"✅ Insights generated for {len(drivers)} drivers. Saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    generate_insights()
