import duckdb
import pandas as pd

DB_PATH = "apex_copilot.duckdb"

def compute_sector_physics_metrics():
    """
    Build physics_sector_metrics: sector-level physics vs a class-best reference.

    Inputs (DuckDB tables expected):
      - telemetry_features  (from telemetry_features.py)
      - sectors             (from sectors.py)
      - drivers             (from laps.py -> drivers table)

    Output:
      - physics_sector_metrics table in DuckDB
    """
    print("⚛️  Computing Sector-Level Physics Metrics (vs class-best)...")

    con = duckdb.connect(DB_PATH)

    # ---------------------------------------------------------------
    # 1️⃣ Build base table: join telemetry_features + sectors + class
    # ---------------------------------------------------------------
    # Following the style of sector_deltas in deltas.py:
    #   sectors JOIN drivers ON driver_id -> brings in d.class
    con.execute("""
        CREATE OR REPLACE TABLE sector_physics_base AS
        SELECT
            tf.race_id,
            tf.session_id,
            d.class,
            tf.driver_id,
            tf.car_no,
            tf.lap_no,
            s.sector_id,
            s.main_sector,
            s.sector_time_s,

            tf.brake_start_progress,
            tf.brake_max_bar,
            tf.throttle_full_ratio,
            tf.throttle_hesitation_count,
            tf.steering_std_deg,
            tf.accx_min_g,
            tf.accy_max_g

            -- NOTE: exit_speed_kph can be added later by extending
            --       telemetry_features to include an exit speed metric.
        FROM telemetry_features tf
        JOIN sectors s
          ON tf.driver_id  = s.driver_id
         AND tf.car_no    = s.car_no
         AND tf.lap_no    = s.lap_no
         AND tf.sector_id = s.sector_id
        JOIN drivers d
          ON tf.driver_id = d.driver_id
        WHERE s.sector_time_s > 1
    """)

    # ---------------------------------------------------------------
    # 2️⃣ Determine class-best reference per (race, class, sector_id)
    #     - choose the row with MIN(sector_time_s) as the reference
    # ---------------------------------------------------------------
    con.execute("""
        CREATE OR REPLACE TEMP VIEW sector_physics_ranked AS
        SELECT
            *,
            ROW_NUMBER() OVER (
                PARTITION BY race_id, class, sector_id
                ORDER BY sector_time_s ASC
            ) AS ref_rank
        FROM sector_physics_base;
    """)

    con.execute("""
        CREATE OR REPLACE TEMP VIEW sector_physics_ref AS
        SELECT
            race_id,
            class,
            sector_id,

            -- reference (class-best) values
            sector_time_s        AS ref_sector_time_s,
            brake_start_progress AS ref_brake_start_progress,
            brake_max_bar        AS ref_brake_max_bar,
            throttle_full_ratio  AS ref_throttle_full_ratio,
            steering_std_deg     AS ref_steering_std_deg,
            accx_min_g           AS ref_accx_min_g,
            accy_max_g           AS ref_accy_max_g
        FROM sector_physics_ranked
        WHERE ref_rank = 1;
    """)

    # ---------------------------------------------------------------
    # 3️⃣ Combine base + reference to compute diffs
    # ---------------------------------------------------------------
    con.execute("""
        CREATE OR REPLACE TABLE physics_sector_metrics AS
        SELECT
            b.race_id,
            b.session_id,
            b.class,
            b.driver_id,
            b.car_no,
            b.lap_no,
            b.sector_id,
            b.main_sector,
            b.sector_time_s,

            -- Original physics metrics
            b.brake_start_progress,
            b.brake_max_bar,
            b.throttle_full_ratio,
            b.throttle_hesitation_count,
            b.steering_std_deg,
            b.accx_min_g,
            b.accy_max_g,

            -- Reference (class-best) values
            r.ref_sector_time_s,
            r.ref_brake_start_progress,
            r.ref_brake_max_bar,
            r.ref_throttle_full_ratio,
            r.ref_steering_std_deg,
            r.ref_accx_min_g,
            r.ref_accy_max_g,

            -- Time delta vs class-best sector (handy for quick access)
            (b.sector_time_s - r.ref_sector_time_s) AS delta_class_s,

            -- Physics deltas vs reference
            (b.brake_start_progress - r.ref_brake_start_progress)
                AS brake_point_diff_vs_ref,
            (b.brake_max_bar       - r.ref_brake_max_bar)
                AS brake_max_bar_diff_vs_ref,
            (b.throttle_full_ratio - r.ref_throttle_full_ratio)
                AS throttle_full_ratio_diff_vs_ref,
            (b.steering_std_deg    - r.ref_steering_std_deg)
                AS steering_std_diff_vs_ref,

            -- For now we don't compute exit_speed_diff_kph here; it can be added
            -- once exit_speed_kph is available in telemetry_features or sectors.
            CAST(NULL AS DOUBLE) AS exit_speed_diff_kph,

            'class_best' AS ref_type

        FROM sector_physics_base b
        JOIN sector_physics_ref r
          ON b.race_id   = r.race_id
         AND b.class     = r.class
         AND b.sector_id = r.sector_id;
    """)

    # ---------------------------------------------------------------
    # 4️⃣ Sanity check
    # ---------------------------------------------------------------
    sample = con.execute("SELECT * FROM physics_sector_metrics LIMIT 5").df()
    print("✅ Sector physics metrics computed. Table 'physics_sector_metrics' created.")
    print("🔎 Sample rows:")
    print(sample)

    con.close()

if __name__ == "__main__":
    compute_sector_physics_metrics()
