import pandas as pd
import duckdb
import os

# Config
RAW_DIR = "data_raw/sebring"
PROCESSED_DIR = "data_processed"
DB_PATH = "apex_copilot.duckdb"

def run_laps_pipeline():
    print("🏎️  Running LAPS Pipeline (Excel -> Merge -> Parquet)...")
    os.makedirs(PROCESSED_DIR, exist_ok=True)
    
    # --- 1. Load Driver Metadata (Results) ---
    results_path = os.path.join(RAW_DIR, "00_Results GR Race 1 Official_Anonymized.xlsx")
    if not os.path.exists(results_path):
        print(f"❌ Critical: Results file missing at {results_path}")
        return

    df_res = pd.read_excel(results_path)
    df_res.columns = df_res.columns.str.strip()
    
    # Create Drivers Table
    drivers_df = df_res[['NUMBER', 'CLASS', 'VEHICLE', 'GROUP', 'TIRES']].drop_duplicates().rename(columns={
        'NUMBER': 'car_no',
        'CLASS': 'class',
        'VEHICLE': 'vehicle',
        'GROUP': 'group',
        'TIRES': 'tires'
    })
    drivers_df['driver_id'] = drivers_df['car_no'].apply(lambda x: f"D_{x}")

    # --- 2. Load Lap Data (The 3-File Merge) ---
    path_time = os.path.join(RAW_DIR, "sebring_lap_time_R1.xlsx")
    path_start = os.path.join(RAW_DIR, "sebring_lap_start_time_R1.xlsx")
    path_end = os.path.join(RAW_DIR, "sebring_lap_end_time_R1.xlsx")

    print("   Loading Excel files (this may take a moment)...")
    df_time = pd.read_excel(path_time)
    df_start = pd.read_excel(path_start)
    df_end = pd.read_excel(path_end)

    merge_keys = ['vehicle_id', 'lap', 'outing', 'meta_session']
    
    df_time = df_time.rename(columns={'value': 'lap_time_ms'})
    df_start = df_start.rename(columns={'value': 'start_ts_raw'})
    df_end = df_end.rename(columns={'value': 'end_ts_raw'})

    # Optional: force numeric, drop junk  # NEW
    df_time['lap_time_ms'] = pd.to_numeric(df_time['lap_time_ms'], errors='coerce')  # NEW
    df_time = df_time[df_time['lap_time_ms'].notna()]                                # NEW

    cols_to_keep = merge_keys
    
    # MERGE 1: Time + Start
    df_laps = pd.merge(
        df_time[cols_to_keep + ['lap_time_ms']], 
        df_start[cols_to_keep + ['start_ts_raw']], 
        on=merge_keys, 
        how='inner'
    )
    
    # MERGE 2: + End
    df_laps = pd.merge(
        df_laps,
        df_end[cols_to_keep + ['end_ts_raw']],
        on=merge_keys,
        how='inner'
    )

    # --- 3. Cleaning & Calculations ---

    # 🔹 RENAME lap -> lap_no BEFORE saving / joining  # NEW
    df_laps = df_laps.rename(columns={'lap': 'lap_no'})  # NEW

    # Parse ISO Timestamps
    df_laps['start_ts'] = pd.to_datetime(df_laps['start_ts_raw'])
    df_laps['end_ts'] = pd.to_datetime(df_laps['end_ts_raw'])
    
    # Parse Lap Time (ms -> s)
    df_laps['lap_time_s'] = df_laps['lap_time_ms'] / 1000.0
    
    # Car Number Logic
    def extract_car_no(vid):
        if pd.isna(vid):
            return -1
        parts = str(vid).split('-')
        try:
            return int(parts[-1])
        except ValueError:
            return -1

    df_laps['car_no'] = df_laps['vehicle_id'].apply(extract_car_no)
    df_laps['driver_id'] = df_laps['car_no'].apply(lambda x: f"D_{x}")

    # lap validity
    df_laps['is_valid'] = df_laps['lap_time_s'].between(60, 240)

    # Optional: race/session tags (nice for future)  # NEW
    df_laps['race_id'] = "sebring_R1"                    # NEW
    drivers_df['race_id'] = "sebring_R1"                 # NEW

    # --- 4. Save ---
    drivers_parquet = os.path.join(PROCESSED_DIR, "drivers.parquet")
    laps_parquet = os.path.join(PROCESSED_DIR, "laps.parquet")
    
    drivers_df.to_parquet(drivers_parquet, index=False)
    df_laps.to_parquet(laps_parquet, index=False)
    
    con = duckdb.connect(DB_PATH)
    con.execute(f"CREATE OR REPLACE TABLE drivers AS SELECT * FROM '{drivers_parquet}'")
    con.execute(f"CREATE OR REPLACE TABLE laps AS SELECT * FROM '{laps_parquet}'")
    con.close()
    
    print(f"✅ Laps Pipeline Done. Processed {len(df_laps)} merged laps.")

if __name__ == "__main__":
    run_laps_pipeline()