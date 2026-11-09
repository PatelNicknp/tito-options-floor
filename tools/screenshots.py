"""Take screenshots of the main screens with headless Chromium. Local only."""
from playwright.sync_api import sync_playwright

OUT = "screenshots"
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1100, "height": 900}, timezone_id="America/Los_Angeles")
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto("http://localhost:8765/#today"); pg.wait_for_selector("#today-body h2")
    pg.screenshot(path=f"{OUT}/01-today-real-data-no-trade.png", full_page=True)
    pg.click("text=Settings & data"); pg.wait_for_selector("#sources-body table")
    pg.screenshot(path=f"{OUT}/02-settings-and-sources-real-data.png", full_page=True)
    pg.click("text=Log a trade")
    f = "#entry-form "
    vals = {"ticker": "F", "expiry": "2026-12-18", "strike": "13", "qty": "1", "bid": "0.29", "ask": "0.31", "stockPrice": "12.50",
            "timeSeen": "2026-11-09T08:15", "sector": "Consumer Discretionary", "nextEarnings": "2027-02-04", "pivot": "12.32",
            "stop": "12.20", "target": "13.44"}
    for k, v in vals.items():
        pg.fill(f + f'[name="{k}"]', v)
    pg.select_option(f + '[name="dataStatus"]', "delayed")
    pg.click(f + "button[type=submit]")
    pg.screenshot(path=f"{OUT}/03-log-form-delayed-rejected.png", full_page=True)
    pg.select_option(f + '[name="dataStatus"]', "realtime")
    pg.click(f + "button[type=submit]")
    pg.wait_for_selector("#entry-msg .good")
    pg.screenshot(path=f"{OUT}/04-log-form-entry-saved-example.png", full_page=True)
    pg.click("nav >> text=My positions"); pg.wait_for_selector("#positions-body h2")
    pg.screenshot(path=f"{OUT}/05-my-positions-example-entry.png", full_page=True)
    pg2 = ctx.new_page(); pg2.on("pageerror", lambda e: errors.append(str(e)))
    pg2.goto("http://localhost:8766/#today"); pg2.wait_for_selector(".contract")
    pg2.click(".contract summary")
    pg2.screenshot(path=f"{OUT}/06-suggestion-card-TEST-FIXTURE.png", full_page=True)
    print("page errors:", errors)
    b.close()
