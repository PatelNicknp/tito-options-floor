"""Screenshots of the automation pieces: Settings & data (status + provenance) and the SIMULATED failure banner.
Servers: 8765 = docs/, 8767 = /tmp/tito-failed (copy with a simulated failed run and old data)."""
from playwright.sync_api import sync_playwright

OUT = "screenshots/light"
with sync_playwright() as p:
    b = p.chromium.launch()
    errors = []
    for name, vp, mobile in (("desktop", {"width": 1100, "height": 900}, False), ("mobile", {"width": 390, "height": 844}, True)):
        ctx = b.new_context(viewport=vp, is_mobile=mobile, device_scale_factor=2 if mobile else 1, timezone_id="America/Los_Angeles")
        pg = ctx.new_page(); pg.on("pageerror", lambda e: errors.append(str(e))); pg.on("console", lambda m: m.type == "error" and errors.append(m.text))
        pg.goto("http://localhost:8765/#settings"); pg.wait_for_selector("#provenance-table")
        pg.screenshot(path=f"{OUT}/{name}-05-settings-data-status-provenance.png", full_page=True)
        pg.goto("http://localhost:8767/#today"); pg.wait_for_selector(".stale")
        pg.screenshot(path=f"{OUT}/{name}-06-failed-run-banner-SIMULATED.png", full_page=False)
        ctx.close()
    print("console/page errors:", errors)
    b.close()
