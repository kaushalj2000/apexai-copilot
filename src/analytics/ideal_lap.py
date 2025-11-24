import duckdb
import pandas as pd

DB_PATH = "apex_copilot.duckdb"

def compute_ideal_laps():
    print("🏆 Computing Ideal Laps...")

    con = duckdb.connect(DB_PATH)

    # ----------------------------------------------------
    # 1) CLASS-LEVEL IDEAL MICRO-SECTORS (same as before)
    # ----------------------------------------------------
    con.execute("""
        CREATE OR REPLACE TABLE ideal_lap_segments_class AS
        SELECT 
            d.class,
            s.sector_id,
            MIN(s.sector_time_s) AS best_sector_time_s,
            ARG_MIN(d.driver_id, s.sector_time_s) AS source_driver_id
        FROM sectors s
        JOIN drivers d ON s.driver_id = d.driver_id
        WHERE s.sector_time_s > 1
        GROUP BY d.class, s.sector_id
        ORDER BY d.class, s.sector_id;
    """)

    df_summary = con.execute("""
        SELECT 
            class, 
            SUM(best_sector_time_s) AS ideal_lap_time_s,
            COUNT(DISTINCT sector_id) AS sector_count
        FROM ideal_lap_segments_class
        GROUP BY class
        ORDER BY class
    """).df()

    print("📊 Class-level ideal laps:")
    print(df_summary)

    # ----------------------------------------------------
    # 2) NEW: FULL-SECTOR PER LAP (S1/S2/S3 = sum of micro-sectors)
    # ----------------------------------------------------
    con.execute("""
        CREATE OR REPLACE TABLE lap_sector_sums AS
        SELECT
            driver_id,
            car_no,
            lap_no,
            main_sector,
            SUM(sector_time_s) AS sector_time_sum
        FROM sectors
        WHERE sector_time_s > 1
        GROUP BY driver_id, car_no, lap_no, main_sector;
    """)

    # ----------------------------------------------------
    # 3) PER-DRIVER BEST SECTOR TIMES (now using summed sectors)
    # ----------------------------------------------------
    con.execute("""
        CREATE OR REPLACE TABLE ideal_lap_driver AS
        SELECT
            l.driver_id,
            MIN(d.class) AS class,
            MIN(CASE WHEN l.main_sector = 'S1' THEN l.sector_time_sum END) AS best_S1,
            MIN(CASE WHEN l.main_sector = 'S2' THEN l.sector_time_sum END) AS best_S2,
            MIN(CASE WHEN l.main_sector = 'S3' THEN l.sector_time_sum END) AS best_S3
        FROM lap_sector_sums l
        JOIN drivers d ON l.driver_id = d.driver_id
        GROUP BY l.driver_id;
    """)

    # ----------------------------------------------------
    # 4) PER-LAP IDEAL LAP + DELTA
    # ----------------------------------------------------
    con.execute("""
        CREATE OR REPLACE TABLE ideal_lap AS
        SELECT
            l.driver_id,
            l.lap_no,
            l.lap_time_s,
            i.class,
            i.best_S1,
            i.best_S2,
            i.best_S3,
            (i.best_S1 + i.best_S2 + i.best_S3) AS ideal_lap_time_s,
            l.lap_time_s - (i.best_S1 + i.best_S2 + i.best_S3) AS delta_to_ideal_s
        FROM laps l
        JOIN ideal_lap_driver i
          ON l.driver_id = i.driver_id
        WHERE l.is_valid = TRUE;
    """)

    n_rows = con.execute("SELECT COUNT(*) FROM ideal_lap").fetchone()[0]
    print(f"✅ ideal_lap table created with {n_rows} rows.")

    con.close()


if __name__ == "__main__":
    compute_ideal_laps()
