import os

def create_structure():
    # Define the folder structure based on Blueprint Section 7
    dirs = [
        "data_raw/sebring",
        "data_processed",
        "src/pipelines",
        "src/analytics",
        "src/ai",
        "src/ui",
        "notebooks",
        "config"
    ]
    
    # Define specific files to create
    files = [
        "src/__init__.py",
        "src/pipelines/__init__.py",
        "src/pipelines/laps.py",       # Blueprint 3.1
        "src/pipelines/sectors.py",    # Blueprint 3.2
        "src/pipelines/telemetry.py",  # Blueprint 3.3
        "src/pipelines/weather.py",    # Blueprint 3.5
        "src/analytics/__init__.py",
        "src/analytics/ideal_lap.py",  # Blueprint 4.1
        "src/analytics/deltas.py",     # Blueprint 4.2
        "src/analytics/physics_metrics.py", # Blueprint 4.3
        "src/analytics/insights.py",   # Blueprint 4.4
        "src/ai/__init__.py",
        "src/ai/push_coach.py",        # Blueprint 5.1
        "src/ai/chat_agent.py",        # Blueprint 5.2
        "src/ui/app_streamlit.py",     # Blueprint 6
        "config/settings.yaml",
        "README.md",
        "requirements.txt"
    ]

    print("🚀 Setting up ApexAI Co-Pilot project structure...")

    # Create Directories
    for d in dirs:
        os.makedirs(d, exist_ok=True)
        print(f"   Created directory: {d}")

    # Create Files
    for f in files:
        if not os.path.exists(f):
            with open(f, 'w') as file:
                pass # Create empty file
            print(f"   Created file:      {f}")
        else:
            print(f"   File exists:       {f}")

    # Create a .gitignore to prevent committing large data files
    gitignore_content = """
# Data
data_raw/
data_processed/
*.parquet
*.db
*.duckdb
*.csv

# Python
__pycache__/
*.pyc
.env
.venv/
venv/
.DS_Store
"""
    with open(".gitignore", "w") as f:
        f.write(gitignore_content)
    print("   Created file:      .gitignore")

    print("\n✅ Setup complete. Ready for data!")

if __name__ == "__main__":
    create_structure()