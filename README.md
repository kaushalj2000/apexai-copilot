# ApexAI Co-Pilot 🏎️

Telemetry-driven race engineering assistant built for the **Toyota GR Cup – Sebring 2025** dataset.

ApexAI Co-Pilot ingests timing, sector and telemetry data into DuckDB, builds derived physics and consistency metrics, and presents an interactive Streamlit dashboard with:

- **Overview** – best/ideal lap KPIs, sector time-loss and lap-time consistency.
- **Track Map & Sector Context** – visual track map plus sector deltas and detailed coaching cards.
- **Ask ApexAI** – natural-language Q&A over the DuckDB database (SQL + explanation).

---

## Project Structure

```text
ApexAI_CoPilot/
├── config/
│   ├── settings.*          # Local config (paths, DB name, etc.)
│   └── openai_key          # NOT in git – contains your OpenAI API key
├── data_raw/
│   ├── logo.png            # App logo
│   ├── sebring_track_map.png
│   └── sebring/            # (optional) raw timing & telemetry Excel files
├── data_processed/
│   ├── analysis_wide.parquet
│   ├── drivers.parquet
│   ├── laps.parquet
│   ├── sectors.parquet
│   ├── telemetry.parquet
│   ├── telemetry_features.parquet
│   ├── driver_insights.json
│   └── driver_coaching.json
├── notebooks/              # (optional) exploration / EDA
├── src/
│   ├── ai/
│   │   ├── chat_agent.py   # NL → SQL + explanation
│   │   └── push_coach.py   # Sector coaching text generation
│   ├── analytics/
│   │   ├── deltas.py       # Sector & lap deltas
│   │   ├── ideal_lap.py    # Ideal lap computation
│   │   ├── insights.py     # Session-level insights
│   │   └── physics_metrics.py
│   ├── pipelines/
│   │   ├── laps.py
│   │   ├── sectors.py
│   │   ├── telemetry.py
│   │   ├── telemetry_features.py
│   │   └── physics_sector_metrics.py
│   └── ui/
│       └── app_streamlit.py  # Main Streamlit app
├── apex_copilot.duckdb     # DuckDB database with all processed tables
├── requirements.txt
├── setup_project.py        # (optional) build / ETL helpers
└── README.md
```

Prerequisites

Python 3.10+

DuckDB
 Python package (installed via requirements.txt)

An OpenAI API key (for the Ask ApexAI and coaching text)

Installation
# 1. Clone the repo
git clone https://github.com/<your-username>/apexai-copilot.git
cd apexai-copilot

# 2. Create and activate a virtual environment
python -m venv .venv

# Windows PowerShell
.venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements.txt

Configure your OpenAI key

There are two options – pick one.

Option A – Environment variable (recommended)
# PowerShell example
$env:OPENAI_API_KEY = "sk-..."

Option B – config/openai_key file (project-local)

Create a file:

config/openai_key


Put only your key inside:

sk-XXXXXXXXXXXXXXXXXXXXXXXX


This file is in .gitignore so it won’t be committed.

Running the Streamlit App

From the project root:

streamlit run src/ui/app_streamlit.py


Then open the URL shown in your terminal (usually http://localhost:8501).

The app will:

Load the existing apex_copilot.duckdb database.

Read driver/coaching/insights from data_processed/.

Render:

Overview tab with KPIs, sector time-loss and lap-time consistency.

Track Map & Sector Context tab with the Sebring map, sector summary and detailed “Sector Attack Plan” cards.

Ask ApexAI tab where you can ask free-form questions like

“Is Car 7 consistent in Sector 1?”
and see the generated SQL, raw query result, and a short explanation.

Rebuilding the Database (Optional)

If you want to rebuild apex_copilot.duckdb from the raw Excel files in data_raw/sebring/:

python setup_project.py  # or the appropriate pipeline entry point


This will:

Load raw timing and telemetry Excel files.

Generate intermediate Parquet files in data_processed/.

Create or update apex_copilot.duckdb.
