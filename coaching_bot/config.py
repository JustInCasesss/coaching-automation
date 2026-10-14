"""Configuration helpers. Secrets are read only from environment variables."""
from __future__ import annotations

import json
import os
from pathlib import Path


def truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def demo_mode() -> bool:
    return truthy(os.getenv("COACH_DEMO_MODE"))


def is_live_whatsapp() -> bool:
    return (
        not demo_mode()
        and bool(os.getenv("WHATSAPP_TOKEN", "").strip())
        and bool(os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip())
    )


def admin_token() -> str:
    return os.getenv("ADMIN_TOKEN", "").strip()


def load_institute_config() -> dict:
    path = Path(os.getenv("COACH_CONFIG", "config/institute.json"))
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RuntimeError(f"Institute configuration not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in {path}: {exc}") from None

    if not isinstance(config, dict):
        raise RuntimeError("Institute configuration must be a JSON object.")
    config.setdefault("institute_name", "Your Coaching Institute")
    config.setdefault("courses", [])
    config.setdefault("fees", {})
    return config


def validate_server_config() -> None:
    token = admin_token()
    if len(token) < 24 or token.lower() in {
        "change-me", "replace-me", "admin", "password", "your-admin-token"
    }:
        raise RuntimeError(
            "Set ADMIN_TOKEN to a unique random secret of at least 24 characters. "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
    if is_live_whatsapp():
        required = {
            "WHATSAPP_VERIFY_TOKEN": os.getenv("WHATSAPP_VERIFY_TOKEN", "").strip(),
            "META_APP_SECRET": os.getenv("META_APP_SECRET", "").strip(),
            "WHATSAPP_REMINDER_TEMPLATE": os.getenv("WHATSAPP_REMINDER_TEMPLATE", "").strip(),
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise RuntimeError(
                "Live WhatsApp requires these environment variables: " + ", ".join(missing)
            )
