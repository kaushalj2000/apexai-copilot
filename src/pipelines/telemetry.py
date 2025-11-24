import pandas as pd
import duckdb
import os
import numpy as np

# Config
RAW_DIR = "data_raw/sebring"
PROCESSED_DIR = "data_processed"
DB_PATH = "apex_copilot.duckdb"

# Mapping Telemetry Names
TELEM_MAP = {
    'ath': 'throttle_pct',
    'pbrake_f': 'brake_f_bar',
    'pbrake_r': 'brake_r_bar',
    'Steering_Angle': 'steering_deg',
    'accx_can': 'accx_g',
    'accy_can': 'accy_g',
    'gear': 'gear',
    'speed': 'speed_kph',
    'nmot': 'rpm'
}

def run_telemetry_pipeline():
    print("📡 Running TELEMETRY Pipeline (Excel -> Pivot -> Merge Laps)...")
    
    # 1. Load Telemetry
    xlsx_path = os.path.join(RAW_DIR, "sebring_telemetry_R1.xlsx")
    
    # We read columns needed + meta columns
    # Use 'usecols' if file is huge, but read_excel implies we load it all anyway
    df = pd.read_excel(xlsx_path)
    df.columns = df.columns.str.strip()
    
    # 2. Filter & Clean
    # Keep rows for physics sensors
    df = df[df['telemetry_name'].isin(TELEM_MAP.keys())]
    
    # Ensure timestamp is datetime
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # 3. Pivot (Handling Duplicates)
    # We include 'original_vehicle_id', 'outing' in index to keep them
    index_cols = ['timestamp', 'vehicle_number', 'vehicle_id', 'lap', 'outing', 'meta_session']
    
    # Check which index cols actually exist in the file
    valid_index_cols = [c for c in index_cols if c in df.columns]
    
    df_wide = df.pivot_table(
        index=valid_index_cols,
        columns='telemetry_name',
        values='telemetry_value',
        aggfunc='mean' # Handle duplicates by averaging
    ).reset_index()
    
    df_wide = df_wide.rename(columns=TELEM_MAP)
    df_wide['driver_id'] = df_wide['vehicle_number'].apply(lambda x: f"D_{int(x)}")

    # 4. Load Laps to Calculate Lap Progress
    # We read the parquet we just created in the Laps pipeline
    laps_path = os.path.join(PROCESSED_DIR, "laps.parquet")
    
    if os.path.exists(laps_path):
        df_laps = pd.read_parquet(laps_path)
        
        # We need to join on keys. 
        # Safe keys: driver_id, lap_no (in laps) vs lap (in telemetry)
        # Let's prep df_laps for merge
        df_laps_join = df_laps[['driver_id', 'lap_no', 'start_ts', 'end_ts']].rename(columns={'lap_no': 'lap'})
        
        # Merge
        df_wide = pd.merge(df_wide, df_laps_join, on=['driver_id', 'lap'], how='inner')

        # Calculate Progress (0.0 to 1.0)
        duration = (df_wide['end_ts'] - df_wide['start_ts']).dt.total_seconds()
        elapsed = (df_wide['timestamp'] - df_wide['start_ts']).dt.total_seconds()

        # Avoid divide-by-zero  # NEW
        valid = duration > 0                                       # NEW
        df_wide.loc[valid, 'lap_progress'] = elapsed[valid] / duration[valid]  # NEW
        df_wide.loc[~valid, 'lap_progress'] = np.nan              # NEW

        # Clamp values 0-1 just in case of timestamp jitter
        df_wide['lap_progress'] = df_wide['lap_progress'].clip(0, 1)

        print("   ✅ Calculated lap_progress using merged lap timing.")

    else:
        print("   ⚠️ Laps parquet not found. Skipping lap_progress calculation.")

    # 5. Save
    telemetry_parquet = os.path.join(PROCESSED_DIR, "telemetry.parquet")
    df_wide.to_parquet(telemetry_parquet, index=False)
    
    con = duckdb.connect(DB_PATH)
    con.execute(f"CREATE OR REPLACE TABLE telemetry AS SELECT * FROM '{telemetry_parquet}'")
    con.close()
    
    print(f"✅ Telemetry Pipeline Done. Saved {len(df_wide)} rows.")

if __name__ == "__main__":
    run_telemetry_pipeline()