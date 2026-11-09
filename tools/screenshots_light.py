"""Light-theme screenshots, desktop (1100px) and mobile (390px). Local servers: 8765 = site, 8766 = TEST FIXTURE copy."""
from playwright.sync_api import sync_playwright

OUT = "screenshots/light"
ENTRY = {"ticker": "F", "expiry": "2026-12-18", "strike": "13", "qty": "1", "bid": "0.29", "ask": "0.31", "stockPrice": "12.50",
         "timeSeen": "2026-11-09T08:15", "sector": "Consumer Discretionary", "nextEarnings": "2027-02-04", "pivot": "12.32",
         "stop": "12.20", "target": "13.44"}

with sync_playwright() as p:
    b = p.chromium.launch()
    errors = []
    for name, vp, mobile in (("desktop", {"width": 1100, "height": 900}, False), ("mobile", {"width": 390, "height": 844}, True)):
        ctx = b.new_context(viewport=vp, is_mobile=mobile, device_scale_factor=2 if mobile else 1, timezone_id="America/Los_Angeles")
        pg = ctx.new_page(); pg.on("pageerror", lambda e: errors.append(str(e))); pg.on("console", lambda m: m.type == "error" and errors.append(m.text))
        pg.goto("http://localhost:8765/#today"); pg.wait_for_selector("#today-body h2")
        pg.screenshot(path=f"{OUT}/{name}-01-today-real-data.png", full_page=True)
        fx = ctx.new_page(); fx.on("pageerror", lambda e: errors.append(str(e)))
        fx.goto("http://localhost:8766/#today"); fx.wait_for_selector(".contract"); fx.click(".contract summary")
        fx.screenshot(path=f"{OUT}/{name}-02-suggestion-card-TEST-FIXTURE.png", full_page=True)
        pg.click("nav >> text=Log a trade")
        for k, v in ENTRY.items():
            pg.fill(f'#entry-form [name="{k}"]', v)
        pg.select_option('#entry-form [name="dataStatus"]', "realtime")
        pg.screenshot(path=f"{OUT}/{name}-03-log-a-trade-form.png", full_page=True)
        pg.click("#entry-form button[type=submit]"); pg.wait_for_selector("#entry-msg .good")
        pg.click("nav >> text=My positions"); pg.wait_for_selector("#positions-body h2")
        pg.screenshot(path=f"{OUT}/{name}-04-my-positions-example-entry.png", full_page=True)
        ctx.close()
    print("console/page errors:", errors)
    b.close()
