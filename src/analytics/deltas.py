import duckdb
import pandas as pd

DB_PATH = "apex_copilot.duckdb"

def compute_deltas():
    print("📉 Computing Advanced Delta Engine (Sector + Lap Level)...")

    con = duckdb.connect(DB_PATH)

    # =============================================================
    # 1️⃣ MICRO-SECTOR DELTAS (S1a, S1b, S2a, S2b, S3a, S3b)
    # =============================================================
    con.execute("""
        CREATE OR REPLACE TABLE sector_deltas AS
        SELECT 
            s.driver_id,
            s.car_no,
            s.lap_no,
            s.sector_id,
            s.main_sector,
            s.sector_time_s,

            -- Ideal per CLASS
            ils.best_sector_time_s AS ideal_class_s,
            (s.sector_time_s - ils.best_sector_time_s) AS delta_class_s,

            -- Personal Best Sector Times (best_S1 / best_S2 / best_S3)
            CASE 
                WHEN s.main_sector='S1' THEN ild.best_S1
                WHEN s.main_sector='S2' THEN ild.best_S2
                WHEN s.main_sector='S3' THEN ild.best_S3
            END AS ideal_driver_s,

            (s.sector_time_s -
             CASE 
                WHEN s.main_sector='S1' THEN ild.best_S1
                WHEN s.main_sector='S2' THEN ild.best_S2
                WHEN s.main_sector='S3' THEN ild.best_S3
             END
            ) AS delta_driver_s,

            ild.best_S1,
            ild.best_S2,
            ild.best_S3,
            d.class

        FROM sectors s
        JOIN drivers d ON s.driver_id = d.driver_id
        JOIN ideal_lap_segments_class ils ON s.sector_id = ils.sector_id
            AND d.class = ils.class
        JOIN ideal_lap_driver ild ON s.driver_id = ild.driver_id

        WHERE s.sector_time_s > 1
    """)
    print("   ✅ Micro-sector deltas complete.")


    # =============================================================
    # 2️⃣ MAIN SECTOR (S1/S2/S3) DELTAS
    # =============================================================
    con.execute("""
        CREATE OR REPLACE TABLE main_sector_deltas AS
        SELECT
            driver_id,
            car_no,
            lap_no,
            main_sector,

            SUM(sector_time_s) AS actual_main_s,
            
            CASE 
                WHEN main_sector='S1' THEN MIN(best_S1)
                WHEN main_sector='S2' THEN MIN(best_S2)
                WHEN main_sector='S3' THEN MIN(best_S3)
            END AS ideal_main_s,

            SUM(sector_time_s) -
            CASE 
                WHEN main_sector='S1' THEN MIN(best_S1)
                WHEN main_sector='S2' THEN MIN(best_S2)
                WHEN main_sector='S3' THEN MIN(best_S3)
            END AS delta_main_s

        FROM sector_deltas
        GROUP BY driver_id, car_no, lap_no, main_sector
    """)
    print("   ✅ Main-sector deltas complete.")


    # =============================================================
    # 3️⃣ LAP DELTA (Actual vs Ideal Lap)
    # =============================================================
    con.execute("""
        CREATE OR REPLACE TABLE lap_deltas AS
        SELECT
            l.driver_id,
            l.lap_no,
            l.lap_time_s,
            i.ideal_lap_time_s,
            (l.lap_time_s - i.ideal_lap_time_s) AS delta_lap_s,
            i.class
        FROM laps l
        JOIN ideal_lap i
          ON l.driver_id = i.driver_id
         AND l.lap_no = i.lap_no
        WHERE l.is_valid = TRUE
    """)
    print("   ✅ Lap deltas computed.")


    # =============================================================
    # 4️⃣ DRIVER OPPORTUNITY ANALYSIS
    # =============================================================
    con.execute("""
        CREATE OR REPLACE TABLE driver_opportunities AS
        SELECT 
            driver_id,
            main_sector,
            AVG(delta_main_s) AS avg_loss_s,
            STDDEV(delta_main_s) AS consistency_s,
            MIN(delta_main_s) AS best_gain_s
        FROM main_sector_deltas
        WHERE delta_main_s < 20  -- remove outliers/pit laps
        GROUP BY driver_id, main_sector
        ORDER BY driver_id, avg_loss_s DESC
    """)
    print("   ✅ Driver opportunities table created.")


    # =============================================================
    # Final validation display
    # =============================================================
    check = con.execute("SELECT * FROM driver_opportunities LIMIT 5").df()
    print("📊 Sample Opportunities:")
    print(check)

    con.close()
    print("🎯 Delta Engine Completed: sector_deltas, main_sector_deltas, lap_deltas, driver_opportunities")

if __name__ == "__main__":
    compute_deltas()
