"""Stock Box-first candles, Yahoo fallback, retries, failure path, status file and workflow guard."""
import json
import sys
import urllib.error
from datetime import date, datetime
from pathlib import Path

import pytest

from tito import build, candles, sources
from tito.sources import ET


def bars(start_n, end_iso, n=300):
    from tito.calendar_us import is_trading_day
    from datetime import timedelta
    days, d = [], date.fromisoformat(end_iso)
    while len(days) < n:
        if is_trading_day(d):
            days.append(d.isoformat())
        d -= timedelta(days=1)
    days.reverse()
    return [{"date": x, "open": 1, "high": 1, "low": 1, "close": start_n + i, "volume": 100} for i, x in enumerate(days)]


def boom():
    raise sources.FetchError("down")


EXP = date(2026, 11, 6)


def test_expected_session():
    assert candles.expected_session(datetime(2026, 11, 9, 9, 0, tzinfo=ET)) == date(2026, 11, 6)  # Monday 6 AM PT
    assert candles.expected_session(datetime(2026, 11, 6, 16, 30, tzinfo=ET)) == date(2026, 11, 6)
    assert candles.expected_session(datetime(2026, 11, 27, 9, 0, tzinfo=ET)) == date(2026, 11, 25)  # after Thanksgiving


def test_stockbox_trusted_only_after_full_run():
    assert candles.stockbox_trusted({"mode": "full", "price_as_of": "2026-11-06"}, EXP)
    assert not candles.stockbox_trusted({"mode": "hourly", "price_as_of": "2026-11-06"}, EXP)
    assert not candles.stockbox_trusted({"mode": "full", "price_as_of": "2026-11-05"}, EXP)


def test_stockbox_first_path_uses_no_yahoo():
    sb = bars(100, "2026-11-06")
    calls = []
    got, src = candles.load_candles(lambda: sb, lambda: calls.append("recent") or [], lambda: calls.append("full") or [], True, EXP, None)
    assert src == "stockbox" and got == sb and calls == []


def test_partial_stockbox_bar_replaced_by_yahoo_recent():
    sb = bars(100, "2026-11-06")
    recent = [{"date": "2026-11-05", "close": 9, "open": 1, "high": 1, "low": 1, "volume": 5},
              {"date": "2026-11-06", "close": 10, "open": 1, "high": 1, "low": 1, "volume": 7}]
    got, src = candles.load_candles(lambda: sb, lambda: recent, boom, False, EXP, "2026-11-06")
    assert src == "stockbox+yahoo-recent"
    assert got[-2:] == recent and got[-3] == sb[-3] and len(got) == len(sb)


def test_unconfirmed_bar_dropped_when_yahoo_down():
    sb = bars(100, "2026-11-06")
    got, src = candles.load_candles(lambda: sb, boom, boom, False, EXP, "2026-11-06")
    assert src == "stockbox-unconfirmed-dropped" and got[-1]["date"] < "2026-11-06"


def test_fallback_to_yahoo_when_stockbox_missing():
    full = bars(5, "2026-11-06")
    got, src = candles.load_candles(boom, boom, lambda: full, True, EXP, None)
    assert src == "yahoo" and got == full
    got, src = candles.load_candles(boom, boom, boom, True, EXP, None)
    assert src == "none" and got == []


class FakeResp:
    def __init__(self, body): self.body = body
    def read(self): return self.body
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_fetcher_backoff_then_success(monkeypatch):
    calls, sleeps = [], []
    def fake(req, timeout):
        calls.append(1)
        if len(calls) < 3:
            raise urllib.error.HTTPError(req.full_url, 503, "busy", {}, None)
        return FakeResp(b"ok")
    monkeypatch.setattr(sources.urllib.request, "urlopen", fake)
    monkeypatch.setattr(sources.time, "sleep", lambda s: sleeps.append(s))
    f = sources.Fetcher(pause=0)
    assert f.get("https://x") == b"ok" and len(calls) == 3
    assert [s for s in sleeps if s >= 2] == [2.0, 4.0]


def test_fetcher_no_retry_on_404(monkeypatch):
    calls = []
    def fake(req, timeout):
        calls.append(1)
        raise urllib.error.HTTPError(req.full_url, 404, "nope", {}, None)
    monkeypatch.setattr(sources.urllib.request, "urlopen", fake)
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    with pytest.raises(sources.FetchError):
        sources.Fetcher(pause=0).get("https://x")
    assert len(calls) == 1


def test_simulated_failure_keeps_last_good_data(tmp_path, monkeypatch):
    out, st = tmp_path / "latest.json", tmp_path / "status.json"
    good = {"generatedAtPT": "2026-11-06 06:01 AM PT", "signalSession": "2026-11-05", "keep": True}
    out.write_text(json.dumps(good))
    st.write_text(json.dumps({"lastRunResult": "success", "lastSuccessPT": good["generatedAtPT"], "lastSuccessSession": "2026-11-05"}))
    monkeypatch.setenv("TITO_FAIL_FETCH", "https://")  # every network request fails
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    cfg = Path(__file__).resolve().parents[1] / "config.json"
    monkeypatch.setattr(sys, "argv", ["build", "--config", str(cfg), "--out", str(out), "--status", str(st), "--limit", "2"])
    assert build.main() == 1
    assert json.loads(out.read_text()) == good  # last good data untouched
    s = json.loads(st.read_text())
    assert s["lastRunResult"] == "failed" and "unavailable" in s["lastError"]
    assert s["lastSuccessPT"] == good["generatedAtPT"] and s["nextRunPT"].endswith("06:00 AM PT")


def test_guard(tmp_path, monkeypatch):
    st = tmp_path / "status.json"
    assert build.guard(st, "workflow_dispatch") is True

    class Clock(datetime):
        now_value = None
        @classmethod
        def now(cls, tz=None): return cls.now_value.astimezone(tz) if tz else cls.now_value
    monkeypatch.setattr(build, "datetime", Clock)
    Clock.now_value = datetime.fromisoformat("2026-11-09T14:00:00+00:00")
    assert build.guard(st, "schedule") is True            # Mon 6:00 AM PST, nothing produced yet
    st.write_text(json.dumps({"lastSuccessSession": "2026-11-06", "lastSuccessPT": "2026-11-08 10:34 PM PT"}))
    assert build.guard(st, "schedule") is True            # produced on Sunday night: Monday's run still goes
    st.write_text(json.dumps({"lastSuccessSession": "2026-11-06", "lastSuccessPT": "2026-11-09 06:03 AM PT"}))
    assert build.guard(st, "schedule") is False           # backup run: today's data already produced
    Clock.now_value = datetime.fromisoformat("2026-11-09T13:00:00+00:00")
    st.write_text("{}")
    assert build.guard(st, "schedule") is False           # 5:00 AM PST: the other DST twin
    Clock.now_value = datetime.fromisoformat("2026-11-26T14:00:00+00:00")
    assert build.guard(st, "schedule") is False           # Thanksgiving
    Clock.now_value = datetime.fromisoformat("2026-11-09T14:00:00+00:00")
    st.write_text("{}")
    assert build.guard(st, "workflow_dispatch", "manual") is True
    assert build.guard(st, "workflow_dispatch", "keeper") is True
    st.write_text(json.dumps({"lastSuccessSession": "2026-11-06", "lastSuccessPT": "2026-11-09 06:03 AM PT"}))
    assert build.guard(st, "workflow_dispatch", "keeper") is False
    assert build.guard(st, "workflow_dispatch", "watchdog") is False
    assert build.guard(st, "workflow_dispatch", "") is True   # the button still runs


def test_next_run_pt():
    from tito.sources import PT
    assert build.next_run_pt(datetime(2026, 11, 6, 7, 0, tzinfo=PT)) == "2026-11-09 06:00 AM PT"
    assert build.next_run_pt(datetime(2026, 11, 9, 5, 0, tzinfo=PT)) == "2026-11-09 06:00 AM PT"


def test_workflow_permissions_and_schedule():
    root = Path(__file__).resolve().parents[1]
    wf = (root / ".github/workflows/update.yml").read_text()
    assert "contents: write" in wf and "pages: write" not in wf and "id-token" not in wf and "secrets." not in wf
    for cron in ("0 13 * * 1-5", "0 14 * * 1-5", "40 13 * * 1-5", "40 14 * * 1-5", "0 16 * * 1-5", "0 17 * * 1-5"):
        assert cron in wf
    assert "concurrency:" in wf and "workflow_dispatch" in wf and "github.token" in wf
    assert "git fetch origin main" in wf
    for name in ("update.yml", "keeper.yml", "watchdog.yml"):
        text = (root / ".github/workflows" / name).read_text()
        assert "secrets." not in text and "pages: write" not in text and "id-token" not in text
    wd = (root / ".github/workflows/watchdog.yml").read_text()
    for cron in ("15 13 * * 1-5", "55 14 * * 1-5", "15 17 * * 1-5", "45 19 * * 1-5", "45 20 * * 1-5"):
        assert cron in wd
    keeper_yml = (root / ".github/workflows/keeper.yml").read_text()
    assert "update-keeper" in keeper_yml and "timeout-minutes: 350" in keeper_yml and "github.token" in keeper_yml
    src = (root / "generator/tito/keeper.py").read_text().lower()
    for bad in ("yahoo", "cboe", "query1", "finance.yahoo", "stockbox"):
        assert bad not in src


def test_keeper_makes_up_a_missing_day_and_stops():
    from datetime import timedelta, timezone
    from tito import keeper
    from tito.sources import PT
    missing = {"lastSuccessSession": "2026-11-06", "lastSuccessPT": "2026-11-08 10:34 PM PT"}
    ready = {"lastSuccessSession": "2026-11-06", "lastSuccessPT": "2026-11-09 06:03 AM PT"}
    morning = datetime(2026, 11, 9, 6, 15, tzinfo=PT)
    assert keeper.decide(morning, missing, [])[0] == "dispatch"          # Sunday night's file is not Monday's run
    assert keeper.decide(datetime(2026, 11, 9, 6, 55, tzinfo=PT), ready, [])[0] == "none"
    assert keeper.decide(datetime(2026, 11, 9, 6, 5, tzinfo=PT), missing, [])[0] == "none"
    assert keeper.decide(datetime(2026, 11, 9, 13, 0, tzinfo=PT), missing, [])[0] == "none"
    assert keeper.decide(datetime(2026, 11, 26, 9, 0, tzinfo=PT), missing, [])[0] == "none"  # Thanksgiving
    assert keeper.decide(morning, None, [])[0] == "none"
    active = [{"id": 9, "status": "in_progress", "created_at": "2026-11-09T14:05:00Z"}]
    assert keeper.decide(morning, missing, active)[0] == "none"
    ghost = [{"id": 43097135921, "status": "queued", "created_at": "2026-11-11T20:37:13Z"}]
    assert keeper.decide(datetime(2026, 11, 13, 6, 15, tzinfo=PT),
                         {"lastSuccessSession": "2026-11-11", "lastSuccessPT": "2026-11-12 12:21 PM PT"}, ghost)[0] == "dispatch"
    fresh_queue = [{"id": 10, "status": "queued", "created_at": "2026-11-09T13:50:00Z"}]
    assert keeper.decide(morning, missing, fresh_queue)[0] == "none"     # queued 25 min ago still counts as live
    assert keeper.decide(morning, missing, [{"id": 11, "status": "queued"}])[0] == "none"  # no time: stay safe
    recent = [{"id": 1, "status": "completed", "conclusion": "failure", "created_at": "2026-11-09T14:15:00Z"}]
    assert keeper.decide(datetime(2026, 11, 9, 6, 25, tzinfo=PT), missing, recent)[0] == "none"
    assert keeper.decide(datetime(2026, 11, 9, 6, 55, tzinfo=PT), missing, recent)[0] == "dispatch"
    failed_twice = recent + [{"id": 2, "status": "completed", "conclusion": "timed_out", "created_at": "2026-11-09T15:10:00Z"}]
    assert keeper.decide(datetime(2026, 11, 9, 9, 15, tzinfo=PT), missing, failed_twice)[0] == "gave_up"
    assert "keeper is live" in keeper.watchdog_action(morning, missing, [], [{"id": 4, "status": "queued"}])[1]
    assert keeper.next_check_after(datetime(2026, 11, 6, 13, 0, tzinfo=PT), 15) == datetime(2026, 11, 9, 6, 15, tzinfo=PT)
    assert keeper.next_check_after(datetime(2026, 11, 25, 13, 0, tzinfo=PT), 15) == datetime(2026, 11, 27, 6, 15, tzinfo=PT)
    assert keeper.should_start_keeper([], True)[0] is True
    assert keeper.should_start_keeper([{"id": 4, "status": "in_progress"}], True)[0] is False
    assert keeper.should_yield("5", [{"id": 8, "status": "queued"}])[0] is True
    assert keeper.self_heal_on({}) is True and keeper.self_heal_on({"selfHeal": False}) is False

    start = datetime(2026, 11, 9, 6, 20, tzinfo=PT)

    class Clock:
        def __init__(self):
            self.t = start
        def __call__(self):
            return self.t
        def sleep(self, seconds):
            self.t += timedelta(seconds=seconds)

    clock = Clock()
    state = {"status": dict(missing), "runs": []}
    dispatched = []

    def load_state():
        return state["status"], list(state["runs"]), []

    def dispatch_update(reason):
        dispatched.append(reason)
        stamp = clock().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        state["runs"].append({"id": len(state["runs"]) + 1, "status": "completed", "conclusion": "success", "created_at": stamp})
        state["status"] = dict(ready)
        return True

    chained = []
    result = keeper.run_keeper(
        clock, clock.sleep, load_state, dispatch_update, lambda: chained.append(clock()) or True,
        settings={"chunk_min": 330, "check_after_min": 15, "max_tries": 2, "min_gap_min": 15, "lookback_min": 20},
        my_id="1",
    )
    assert result == "handoff" and dispatched == ["keeper"] and chained
