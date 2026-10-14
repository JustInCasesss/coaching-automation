"""Coaching Institute WhatsApp Automation - zero-dependency core engine.

Modules
-------
config      : white-label config loader (config/institute.json)
store       : SQLite schema + data access
whatsapp    : WhatsApp Cloud API client (dry-run capable)
flows       : inbound conversation state machine
reminders   : fee reminder scheduler + escalation
performance : student performance reports + score alerts
server      : webhook HTTP server (Meta verify + receive)
cli         : operator commands (seed, demo, reminders, score, report)
"""

import sys

if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

__version__ = "1.0.0"
__all__ = ["config", "store", "whatsapp", "flows", "reminders", "performance", "server", "cli"]