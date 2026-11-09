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


def test_next_run_pt():
    from tito.sources import PT
    assert build.next_run_pt(datetime(2026, 11, 6, 7, 0, tzinfo=PT)) == "2026-11-09 06:00 AM PT"
    assert build.next_run_pt(datetime(2026, 11, 9, 5, 0, tzinfo=PT)) == "2026-11-09 06:00 AM PT"


def test_workflow_permissions_and_schedule():
    wf = (Path(__file__).resolve().parents[1] / ".github/workflows/update.yml").read_text()
    assert "contents: write" in wf and "pages: write" not in wf and "id-token" not in wf and "secrets." not in wf
    for cron in ("0 13 * * 1-5", "0 14 * * 1-5", "40 13 * * 1-5", "40 14 * * 1-5"):
        assert cron in wf
    assert "concurrency:" in wf and "workflow_dispatch" in wf
