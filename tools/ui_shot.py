# -*- coding: utf-8 -*-
"""KylinGuard UI screenshot helper.

Usage:
  python tools/ui_shot.py out_dir [prefix]

Captures every main view of the app at desktop width into out_dir.
Requires the backend serving the built frontend at http://127.0.0.1:8000,
and playwright (pip install playwright && playwright install chromium).
"""
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
VIEWS = [
    ("chat", ""),
    ("models", "?view=models"),
    ("extensions", "?view=extensions"),
    ("audit", "?view=audit"),
    ("policy", "?view=policy"),
    ("dashboard", "?view=dashboard"),
    ("alerts", "?view=alerts"),
]


def main() -> None:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "tools/shots")
    prefix = sys.argv[2] if len(sys.argv) > 2 else "shot"
    out_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        for name, query in VIEWS:
            page.goto(f"{BASE}/{query}", wait_until="networkidle")
            time.sleep(1.2)
            page.screenshot(path=str(out_dir / f"{prefix}-{name}.png"))
            print(f"saved {prefix}-{name}.png")
        # narrow pass for responsive checks on the busiest views
        page.set_viewport_size({"width": 1100, "height": 850})
        for name, query in [("dashboard", "?view=dashboard"), ("chat", "")]:
            page.goto(f"{BASE}/{query}", wait_until="networkidle")
            time.sleep(1.0)
            page.screenshot(path=str(out_dir / f"{prefix}-{name}-narrow.png"))
            print(f"saved {prefix}-{name}-narrow.png")
        browser.close()


if __name__ == "__main__":
    main()
