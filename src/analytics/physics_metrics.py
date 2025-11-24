import duckdb
import pandas as pd

DB_PATH = "apex_copilot.duckdb"

def compute_physics_metrics():
    print("⚛️  Computing Physics Metrics (Driver Inputs & Style)...")

    con = duckdb.connect(DB_PATH)

    # 1) Aggregate telemetry per driver + lap
    # This gives a compact description of how each lap was driven.
    con.execute("""
        CREATE OR REPLACE TABLE lap_physics AS
        SELECT
            driver_id,
            lap,

            -- How much data we actually have for this lap
            COUNT(*) AS samples,

            -- Speed / Pace
            AVG(speed_kph) AS avg_speed_kph,

            -- Throttle usage
            AVG(throttle_pct) AS avg_throttle,
            SUM(CASE WHEN throttle_pct > 95 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS full_throttle_ratio,

            -- Coasting: neither on throttle nor on brakes
            SUM(
                CASE 
                    WHEN throttle_pct < 5 AND brake_f_bar < 0.5 THEN 1 
                    ELSE 0 
                END
            ) * 1.0 / COUNT(*) AS coasting_ratio,

            -- Braking behaviour
            MAX(brake_f_bar) AS max_brake_pressure,
            AVG(brake_f_bar) AS avg_brake_pressure,
            SUM(CASE WHEN brake_f_bar > 1 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS braking_ratio,

            -- Steering: variability = corrections; low = smooth, high = twitchy
            STDDEV(steering_deg) AS steering_variability,

            -- Longitudinal Gs
            MIN(accx_g) AS max_braking_g,   -- most negative = hardest brake
            MAX(accx_g) AS max_accel_g,     -- most positive = hardest acceleration

            -- Lateral Gs
            MAX(ABS(accy_g)) AS max_cornering_g

        FROM telemetry
        GROUP BY driver_id, lap
    """)

    # 2) Join with laps and keep only valid race laps
    con.execute("""
        CREATE OR REPLACE TABLE valid_lap_physics AS
        SELECT 
            lp.*,
            l.lap_time_s,
            l.is_valid
        FROM lap_physics lp
        JOIN laps l 
          ON lp.driver_id = l.driver_id 
         AND lp.lap = l.lap_no
        WHERE l.is_valid = TRUE
    """)

    # Quick sanity check
    sample = con.execute("SELECT * FROM valid_lap_physics LIMIT 5").df()
    print("✅ Physics metrics computed. Table 'valid_lap_physics' created.")
    print("🔎 Sample rows:")
    print(sample)

    con.close()

if __name__ == "__main__":
    compute_physics_metrics()
