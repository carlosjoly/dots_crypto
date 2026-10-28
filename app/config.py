import os
from pathlib import Path
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

def load_config():
    with open(ROOT / "config.yaml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["_root"] = str(ROOT)
    cfg["_env"] = {
        "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        "goplus_enabled": os.getenv("GOPLUS_ENABLED", "false").lower() == "true"
            or bool(cfg.get("security", {}).get("goplus_enabled", False)),
        "goplus_app_key": os.getenv("GOPLUS_APP_KEY", "").strip(),
        "goplus_app_secret": os.getenv("GOPLUS_APP_SECRET", "").strip(),
    }
    return cfg
