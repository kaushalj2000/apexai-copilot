import os
import duckdb

# Config – keep consistent with other pipelines
PROCESSED_DIR = "data_processed"
DB_PATH = "apex_copilot.duckdb"

# Hard-coded sector bounds in lap_progress (0–1)
# You can tweak these numbers later if you refine the map.
SECTOR_BOUNDS = [
    ("S1a", 0.00, 0.17),
    ("S1b", 0.17, 0.33),
    ("S2a", 0.33, 0.50),
    ("S2b", 0.50, 0.67),
    ("S3a", 0.67, 0.83),
    ("S3b", 0.83, 1.01),   # a bit over 1.0 to catch edge samples
]


def _sector_case_expr(progress_col: str = "lap_progress") -> str:
    """
    Build a CASE expression that maps lap_progress → sector_id.
    """
    parts = ["CASE"]
    for name, lo, hi in SECTOR_BOUNDS:
        parts.append(
            f" WHEN {progress_col} >= {lo:.3f} AND {progress_col} < {hi:.3f} "
            f"THEN '{name}'"
        )
    parts.append(" END")
    return " ".join(parts)


def run_telemetry_features_pipeline():
    print("📊 Building TELEMETRY FEATURES (per driver / lap / sector)...")

    os.makedirs(PROCESSED_DIR, exist_ok=True)
    con = duckdb.connect(DB_PATH)

    # ------------------------------------------------------------------
    # 1) Enrich telemetry with race_id, session_id, car_no
    #    - race_id: from drivers table
    #    - session_id: reuse meta_session from telemetry
    # ------------------------------------------------------------------
    sector_case = _sector_case_expr("lap_progress")

    con.execute("""
        CREATE OR REPLACE TEMP VIEW telemetry_enriched AS
        SELECT
            t.timestamp,
            t.driver_id,
            d.car_no,
            t.lap        AS lap_no,
            t.meta_session AS session_id,
            COALESCE(d.race_id, 'sebring_R1') AS race_id,
            t.lap_progress,
            t.throttle_pct,
            t.brake_f_bar,
            t.steering_deg,
            t.accx_g,
            t.accy_g,
            t.speed_kph
        FROM telemetry t
        LEFT JOIN drivers d
          ON t.driver_id = d.driver_id
        WHERE t.lap_progress IS NOT NULL
          AND t.lap_progress >= 0
          AND t.lap_progress <= 1;
    """)

    # ------------------------------------------------------------------
    # 2) Attach sector_id based on lap_progress
    # ------------------------------------------------------------------
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW telemetry_with_sector AS
        SELECT
            race_id,
            session_id,
            driver_id,
            car_no,
            lap_no,
            {sector_case} AS sector_id,
            lap_progress,
            timestamp,
            throttle_pct,
            brake_f_bar,
            steering_deg,
            accx_g,
            accy_g,
            speed_kph
        FROM telemetry_enriched;
    """)

    # ------------------------------------------------------------------
    # 3) Add row-level flags (mid-throttle band + hesitation events)
    # ------------------------------------------------------------------
    con.execute("""
        CREATE OR REPLACE TEMP VIEW telemetry_row_level AS
        WITH mid AS (
            SELECT
                *,
                (throttle_pct BETWEEN 40 AND 70) AS mid_throttle
            FROM telemetry_with_sector
        )
        SELECT
            *,
            CASE
                -- "Hesitation" = entering the mid-throttle band
                WHEN mid_throttle
                 AND NOT lag(mid_throttle, 1, FALSE) OVER (
                        PARTITION BY race_id, session_id,
                                     driver_id, car_no, lap_no, sector_id
                        ORDER BY lap_progress
                    )
                THEN 1
                ELSE 0
            END AS throttle_hesitation_flag
        FROM mid;
    """)

    # ------------------------------------------------------------------
    # 4) Aggregate per (race, session, driver, car, lap, sector)
    # ------------------------------------------------------------------
    con.execute("""
        CREATE OR REPLACE TABLE telemetry_features AS
        SELECT
            race_id,
            session_id,
            driver_id,
            car_no,
            lap_no,
            sector_id,

            -- first time we see meaningful brake pressure
            MIN(
                CASE WHEN brake_f_bar > 5 THEN lap_progress ELSE NULL END
            ) AS brake_start_progress,

            MAX(brake_f_bar) AS brake_max_bar,

            -- % of samples at (almost) full throttle
            AVG(
                CASE WHEN throttle_pct > 95 THEN 1.0 ELSE 0.0 END
            ) AS throttle_full_ratio,

            -- count of throttle "hesitations"
            SUM(throttle_hesitation_flag) AS throttle_hesitation_count,

            -- steering stability
            STDDEV_POP(steering_deg) AS steering_std_deg,

            -- G-forces
            MIN(accx_g) AS accx_min_g,
            MAX(accy_g) AS accy_max_g

        FROM telemetry_row_level
        WHERE sector_id IS NOT NULL
        GROUP BY
            race_id,
            session_id,
            driver_id,
            car_no,
            lap_no,
            sector_id
        ORDER BY
            race_id,
            session_id,
            driver_id,
            car_no,
            lap_no,
            sector_id;
    """)

    # ------------------------------------------------------------------
    # 5) Save to parquet + register table
    # ------------------------------------------------------------------
    out_path = os.path.join(PROCESSED_DIR, "telemetry_features.parquet")
    con.execute(f"""
        COPY telemetry_features
        TO '{out_path}'
        (FORMAT PARQUET, OVERWRITE TRUE);
    """)

    # Ensure the table is backed by the parquet file
    con.execute(f"CREATE OR REPLACE TABLE telemetry_features AS SELECT * FROM '{out_path}'")

    con.close()
    print(f"✅ telemetry_features created and saved to {out_path}")


if __name__ == "__main__":
    run_telemetry_features_pipeline()