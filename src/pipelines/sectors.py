import pandas as pd
import duckdb
import os
import numpy as np
import datetime


# Config
RAW_DIR = "data_raw/sebring"
PROCESSED_DIR = "data_processed"
DB_PATH = "apex_copilot.duckdb"

def parse_time(val):
    """Converts '1:22.4', '00:53.2', 82.4, or datetime.time to seconds."""
    if pd.isna(val):
        return None

    # Case 1: already a datetime.time object
    if isinstance(val, datetime.time):
        # convert HH:MM:SS.microseconds → seconds
        return val.hour * 3600 + val.minute * 60 + val.second + val.microsecond / 1e6

    # Case 2: string like '1:22.4' or '00:53.2'
    if isinstance(val, str):
        val = val.strip()
        if ':' in val:
            try:
                parts = val.split(':')
                minutes = int(parts[-2]) if len(parts) == 3 else int(parts[0])
                seconds = float(parts[-1])
                return minutes * 60 + seconds
            except:
                return None
        else:
            try:
                return float(val)
            except:
                return None

    # Case 3: Already numeric (float, int)
    try:
        return float(val)
    except:
        return None

def run_sectors_pipeline():
    print("⏱️  Running SECTORS Pipeline (Excel -> Clean -> Wide)...")
    
    xlsx_path = os.path.join(RAW_DIR, "23_AnalysisEnduranceWithSections_Race 1_Anonymized.xlsx")
    df = pd.read_excel(xlsx_path)
    df.columns = df.columns.str.strip()

    # Columns to keep for context (Wide Format)
    meta_cols = [
        'NUMBER', 'LAP_NUMBER', 'LAP_TIME', 'KPH', 'TOP_SPEED', 
        'CLASS', 'GROUP', 'MANUFACTURER', 'ELAPSED', 'HOUR', 
        'S1_SECONDS', 'S2_SECONDS', 'S3_SECONDS'
    ]
    
    # Verify cols exist
    existing_meta_cols = [c for c in meta_cols if c in df.columns]

    # Map for Long Format (The Micro-Sectors)
    sector_map = {
        'S1a': 'IM1a_time', 'S1b': 'IM1_time',
        'S2a': 'IM2a_time', 'S2b': 'IM2_time',
        'S3a': 'IM3a_time', 'S3b': 'FL_time'
    }

    sector_rows = []

    for _, row in df.iterrows():
        car_no = row['NUMBER']
        driver_id = f"D_{car_no}"
        lap_no = row['LAP_NUMBER']
        
        # 1. Build the Long-Format Sectors
        for sec_id, col_name in sector_map.items():
            if col_name in df.columns:
                raw_val = row[col_name]
                time_s = parse_time(raw_val)
                
                if time_s is not None:
                    sector_rows.append({
                        'driver_id': driver_id,
                        'car_no': car_no,
                        'lap_no': lap_no,
                        'sector_id': sec_id,
                        'main_sector': sec_id[:2], # S1, S2, or S3
                        'sector_time_s': time_s
                    })

    # Create DataFrames
    df_sectors_long = pd.DataFrame(sector_rows)
    
    # We also save the original "Wide" analysis data because it has TOP_SPEED and KPH
    df_analysis_wide = df[existing_meta_cols].copy()
    df_analysis_wide['driver_id'] = df_analysis_wide['NUMBER'].apply(lambda x: f"D_{x}")

    # --- Save ---
    sectors_parquet = os.path.join(PROCESSED_DIR, "sectors.parquet")
    analysis_parquet = os.path.join(PROCESSED_DIR, "analysis_wide.parquet")

    df_sectors_long.to_parquet(sectors_parquet, index=False)
    df_analysis_wide.to_parquet(analysis_parquet, index=False)
    
    con = duckdb.connect(DB_PATH)
    con.execute(f"CREATE OR REPLACE TABLE sectors AS SELECT * FROM '{sectors_parquet}'")
    con.execute(f"CREATE OR REPLACE TABLE analysis_wide AS SELECT * FROM '{analysis_parquet}'")
    con.close()
    
    print(f"✅ Sectors Pipeline Done. Tables 'sectors' and 'analysis_wide' created.")

if __name__ == "__main__":
    run_sectors_pipeline()