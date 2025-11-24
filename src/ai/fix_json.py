import json
import re
from pathlib import Path

# Config
FILE_PATH = Path("data_processed/driver_coaching.json")

def fix_driver_coaching():
    if not FILE_PATH.exists():
        print(f"❌ File not found: {FILE_PATH}")
        return

    print(f"🔧 Reading {FILE_PATH}...")
    with open(FILE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    fixed_count = 0

    # Iterate through every driver and every coaching entry
    for driver_id, driver_data in data.items():
        coaching_list = driver_data.get("coaching", [])
        
        for entry in coaching_list:
            raw_text = entry.get("coaching_text", "")
            
            # Check if we have the "Nested JSON" bug
            # Look for markdown fences or a starting bracket
            if isinstance(raw_text, str) and ("```" in raw_text or raw_text.strip().startswith("{")):
                
                # 1. Strip Markdown fences
                clean_text = re.sub(r"```(?:json)?", "", raw_text)
                clean_text = clean_text.replace("```", "").strip()
                
                try:
                    # 2. Try to parse the inner JSON
                    inner_json = json.loads(clean_text)
                    
                    # 3. Hoist the inner data up to the main entry
                    # This replaces the generic "Coaching" title with the real one
                    if "short_title" in inner_json:
                        entry["short_title"] = inner_json["short_title"]
                    
                    if "emoji_tag" in inner_json:
                        entry["emoji_tag"] = inner_json["emoji_tag"]
                        
                    if "coaching_text" in inner_json:
                        entry["coaching_text"] = inner_json["coaching_text"]
                    
                    fixed_count += 1
                    
                except json.JSONDecodeError:
                    # If parsing fails, just clean the fences so it displays as text
                    print(f"  ⚠️ Could not parse JSON for {driver_id}, but stripping fences.")
                    entry["coaching_text"] = clean_text

    # Save the fixed file
    with open(FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    print(f"✅ Success! Fixed {fixed_count} nested JSON entries.")
    print(f"📂 Saved cleaned file to {FILE_PATH}")

if __name__ == "__main__":
    fix_driver_coaching()