"""Make up a dropped GitHub cron without fetching market data.

GitHub's scheduler can skip a newly added schedule, and it can drop a cron at a busy
minute. The daily build stays in update.yml (that is the only job that reads prices).
This module only reads this repo's status.json and its Actions runs, then uses the
workflow's own GITHUB_TOKEN to dispatch that one build when today's file is missing.

One published file per trading day, and at most two build attempts. A run that is
already queued or in progress blocks another, so two slots cannot overlap.

    python -m tito.keeper check          # watchdog: dispatch one build if today's data is missing
    python -m tito.keeper ensure-keeper  # start keeper.yml when none is queued or running
    python -m tito.keeper keeper         # sleep until each slot, then check
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from tito.build import data_already_produced
from tito.calendar_us import is_trading_day
from tito.sources import PT

UPDATE_FILE = "update.yml"
KEEPER_FILE = "keeper.yml"
# Pacific clock. The watchdog crons are these times in both PST and PDT.
SLOTS = ((6, 0), (6, 40), (9, 0), (11, 30), (12, 30))
ACTIVE = frozenset({"queued", "in_progress", "waiting", "requested", "pending"})
ATTEMPT = frozenset({"success", "failure", "timed_out"})
SETTINGS = {"check_after_min": 15, "chunk_min": 330, "min_gap_min": 15, "max_tries": 2, "lookback_min": 20}
# A Daily update job stops after 45 minutes. GitHub can leave a run "queued" forever
# (one sat queued from Nov 11 to Nov 13, 2026 and blocked every make-up). An update run
# that has been queued or running longer than this is treated as stuck, not as live.
STALE_UPDATE_MIN = 90


def self_heal_on(cfg: dict | None) -> bool:
    """Missing key means on. config.json on main is re-read so a switch flipped mid-sleep is obeyed."""
    if not cfg:
        return True
    return bool(cfg.get("selfHeal", True))


def check_times(day, lag_min: int) -> list[datetime]:
    if not is_trading_day(day):
        return []
    return [datetime(day.year, day.month, day.day, h, m, tzinfo=PT) + timedelta(minutes=lag_min) for h, m in SLOTS]


def next_check_after(after: datetime, lag_min: int = 15) -> datetime | None:
    """Earliest slot check strictly after `after` (aware). Skips weekends and NYSE holidays."""
    after = after.astimezone(PT)
    best = None
    day = after.date()
    for i in range(0, 14):
        for t in check_times(day + timedelta(days=i), lag_min):
            if t > after and (best is None or t < best):
                best = t
        if best is not None and i >= 1:
            break
    return best


def plan(after: datetime, deadline: datetime, lag_min: int = 15) -> tuple[str, datetime]:
    """('check', when) if the next slot check falls before the job must hand off, else ('handoff', deadline)."""
    nxt = next_check_after(after, lag_min)
    if nxt is None or nxt > deadline:
        return "handoff", deadline
    return "check", nxt


def run_started(run: dict) -> datetime | None:
    raw = run.get("created_at") or run.get("run_started_at") or ""
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def attempts_since_open(runs: list[dict], day) -> list[dict]:
    """Finished update runs that started at or after 6:00 AM PT on `day`. Cancelled runs do not count."""
    open_at = datetime(day.year, day.month, day.day, 6, 0, tzinfo=PT)
    out = []
    for run in runs:
        started = run_started(run)
        if started is None or started.astimezone(PT) < open_at:
            continue
        if run.get("status") in ACTIVE:
            continue
        if run.get("conclusion") in ATTEMPT:
            out.append(run)
    return out


def live_update_runs(runs: list[dict], now_pt: datetime, stale_min: int = STALE_UPDATE_MIN) -> tuple[list[dict], list[dict]]:
    """(live, stuck) among queued or running update runs. Stuck = older than stale_min.

    A run with no readable start time stays live, so an odd API reply never starts a second build.
    """
    live, stuck = [], []
    for run in runs:
        if run.get("status") not in ACTIVE:
            continue
        started = run_started(run)
        if started is not None and (now_pt - started).total_seconds() / 60.0 > stale_min:
            stuck.append(run)
        else:
            live.append(run)
    return live, stuck


def decide(now_pt: datetime, status: dict | None, runs: list[dict] | None, *, max_tries: int = 2, min_gap_min: int = 15) -> tuple[str, str]:
    """What to do at a slot. 'dispatch' starts one build, 'gave_up' stops for the day, 'none' waits.

    status or runs of None means GitHub could not be read: do nothing, so a blip never starts a fetch.
    """
    if status is None or runs is None:
        return "none", "could not read status or runs; not dispatching"
    if not is_trading_day(now_pt.date()):
        return "none", f"{now_pt.date()} is not a US trading day"
    if (now_pt.hour, now_pt.minute) < (6, 10):
        return "none", "before 6:10 AM PT"
    if (now_pt.hour, now_pt.minute) >= (13, 0):
        return "none", "after 1:00 PM PT"
    if data_already_produced(status, now_pt):
        return "none", f"today's data was already produced at {status.get('lastSuccessPT')}"
    active, stuck = live_update_runs(runs, now_pt)
    if active:
        return "none", f"update run {active[0].get('id')} is {active[0].get('status')}"
    if stuck:
        print(f"ignoring stuck update run {stuck[0].get('id')} ({stuck[0].get('status')} since {stuck[0].get('created_at')})", flush=True)
    done = attempts_since_open(runs, now_pt.date())
    if len(done) >= max_tries:
        return "gave_up", f"{len(done)} runs since 6:00 AM PT did not publish today's data; not starting another"
    if done:
        latest = max(run_started(r) for r in done)
        gap = (now_pt - latest.astimezone(PT)).total_seconds() / 60.0
        if gap < min_gap_min:
            return "none", f"last attempt was {int(gap)} min ago (waiting {min_gap_min})"
    return "dispatch", "today's data is missing"


def keeper_is_live(keeper_runs: list[dict], my_id: str | None = None) -> list[dict]:
    return [r for r in keeper_runs if r.get("status") in ACTIVE and str(r.get("id")) != str(my_id)]


def should_yield(my_id: str | None, keeper_runs: list[dict]) -> tuple[bool, str]:
    """Step aside when a newer keeper is queued or running, so the chain never forks."""
    newer = [r for r in keeper_is_live(keeper_runs, my_id) if int(r.get("id") or 0) > int(my_id or 0)]
    if not newer:
        return False, ""
    return True, f"newer keeper {newer[0].get('id')} is {newer[0].get('status')}"


def should_start_keeper(keeper_runs: list[dict], enabled: bool = True) -> tuple[bool, str]:
    if not enabled:
        return False, "selfHeal is off"
    live = keeper_is_live(keeper_runs)
    if live:
        return False, f"keeper {live[0].get('id')} is already {live[0].get('status')}"
    return True, "no live keeper"


def watchdog_action(now_pt: datetime, status, runs, keeper_runs, *, max_tries: int = 2, min_gap_min: int = 15) -> tuple[str, str]:
    """The short cron. A live keeper owns the make-up, so this does not start a second one."""
    if keeper_runs is None:
        return "none", "could not read keeper runs; not dispatching"
    if keeper_is_live(keeper_runs):
        return "none", "keeper is live"
    return decide(now_pt, status, runs, max_tries=max_tries, min_gap_min=min_gap_min)


def run_keeper(clock, sleep, load_state, dispatch_update, dispatch_keeper, *, settings: dict | None = None, enabled=None, my_id: str | None = None) -> str:
    """Sleep until each slot, dispatch one build when today's file is missing, hand off before 6 hours.

    load_state() -> (status or None, update runs or None, keeper runs or None).
    enabled() -> bool, re-read so the switch can stop the chain.
    Returns 'off', 'yield', or 'handoff'.
    """
    cfg = dict(SETTINGS)
    if settings:
        cfg.update(settings)
    enabled = enabled or (lambda: True)
    started = clock()
    deadline = started + timedelta(minutes=int(cfg["chunk_min"]))
    last = started - timedelta(minutes=int(cfg["lookback_min"]))
    while True:
        if not enabled():
            print("keeper: selfHeal is off, stopping (no next keeper)", flush=True)
            return "off"
        try:
            _, _, keepers = load_state()
        except Exception as exc:  # noqa: BLE001
            keepers = None
            print(f"keeper: could not read keeper runs ({exc})", flush=True)
        if keepers is not None:
            yield_, why = should_yield(my_id, keepers)
            if yield_:
                print(f"keeper: stepping aside - {why}", flush=True)
                return "yield"
        kind, when = plan(last, deadline, int(cfg["check_after_min"]))
        print(f"keeper: next {kind} at {when.astimezone(PT).strftime('%Y-%m-%d %H:%M PT')}", flush=True)
        sleep(max(0, int((when - clock()).total_seconds())))
        if not enabled():
            continue
        if kind == "handoff":
            try:
                _, _, keepers = load_state()
            except Exception:  # noqa: BLE001
                keepers = None
            others = keeper_is_live(keepers or [], my_id)
            if others:
                print(f"keeper: keeper {others[0].get('id')} is already live, not chaining", flush=True)
                return "yield"
            ok = dispatch_keeper()
            print("keeper: handed off to the next keeper" if ok else "keeper: hand-off failed; a watchdog cron will start one", flush=True)
            return "handoff"
        last = when
        try:
            status, runs, _keepers = load_state()
        except Exception as exc:  # noqa: BLE001
            print(f"keeper: could not read state ({exc}), skipping this check", flush=True)
            continue
        action, why = decide(clock().astimezone(PT), status, runs, max_tries=int(cfg["max_tries"]), min_gap_min=int(cfg["min_gap_min"]))
        print(f"keeper decision: {action.upper()} - {why}", flush=True)
        if action == "dispatch" and dispatch_update("keeper"):
            print("keeper: dispatched one update", flush=True)
        elif action == "gave_up":
            _warn(why)


# ---------------------------------------------------------------- GitHub API
def _warn(text: str) -> None:
    print(f"::warning title=Update keeper::{text}", flush=True)


def _token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is not set")
    return token


def _api(method: str, path: str, body: dict | None = None):
    repo = os.environ["GITHUB_REPOSITORY"]
    url = f"https://api.github.com/repos/{repo}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + _token(),
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else {}


def fetch_runs(workflow: str, n: int = 30) -> list[dict]:
    return _api("GET", f"/actions/workflows/{workflow}/runs?per_page={n}").get("workflow_runs", [])


def fetch_status() -> dict | None:
    """docs/data/status.json as it is on main. None if it cannot be read. No market data."""
    repo = os.environ["GITHUB_REPOSITORY"]
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/contents/docs/data/status.json?ref=main",
        headers={
            "Authorization": "Bearer " + _token(),
            "Accept": "application/vnd.github.raw+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {}
        print(f"could not read status.json ({exc})", flush=True)
        return None
    except Exception as exc:  # noqa: BLE001
        print(f"could not read status.json ({exc})", flush=True)
        return None


def read_config(path: str = "config.json") -> dict:
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def live_config(path: str = "config.json") -> dict:
    """config.json on main, so selfHeal flipped during a long sleep is obeyed. Falls back to the checkout."""
    try:
        repo = os.environ["GITHUB_REPOSITORY"]
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/contents/config.json?ref=main",
            headers={
                "Authorization": "Bearer " + _token(),
                "Accept": "application/vnd.github.raw+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as exc:  # noqa: BLE001
        print(f"could not read config.json from main ({exc}), using the checkout", flush=True)
        return read_config(path)


def dispatch(workflow: str, inputs: dict | None = None, tries: int = 3) -> bool:
    ref = os.environ.get("GITHUB_REF_NAME") or "main"
    body = {"ref": ref}
    if inputs:
        body["inputs"] = inputs
    for i in range(tries):
        try:
            _api("POST", f"/actions/workflows/{workflow}/dispatches", body)
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"dispatch {workflow} failed ({exc}), try {i + 1} of {tries}", flush=True)
            if i + 1 < tries:
                time.sleep(20)
    return False


def _dry() -> bool:
    return os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")


def cmd_check() -> int:
    cfg = read_config()
    if not self_heal_on(cfg):
        print("check: selfHeal is off", flush=True)
        return 0
    try:
        status, runs, keepers = fetch_status(), fetch_runs(UPDATE_FILE), fetch_runs(KEEPER_FILE)
    except Exception as exc:  # noqa: BLE001
        print(f"check: could not read GitHub ({exc}); not dispatching", flush=True)
        return 0
    action, why = watchdog_action(datetime.now(PT), status, runs, keepers)
    print(f"check: {action.upper()} - {why}", flush=True)
    if action == "gave_up":
        _warn(why)
    if action == "dispatch" and not _dry():
        dispatch(UPDATE_FILE, {"reason": "watchdog"})
    return 0


def cmd_ensure() -> int:
    cfg = read_config()
    try:
        runs = fetch_runs(KEEPER_FILE) if self_heal_on(cfg) else []
    except Exception as exc:  # noqa: BLE001
        print(f"ensure-keeper: could not read keeper runs ({exc}), leaving it", flush=True)
        return 0
    start, why = should_start_keeper(runs, self_heal_on(cfg))
    print(f"ensure-keeper: {'START' if start else 'NONE'} - {why}", flush=True)
    if start and not _dry():
        dispatch(KEEPER_FILE, {})
    return 0


def cmd_keeper() -> int:
    def clock():
        return datetime.now(PT).astimezone(PT)

    def load_state():
        return fetch_status(), fetch_runs(UPDATE_FILE), fetch_runs(KEEPER_FILE)

    def enabled():
        return self_heal_on(live_config())

    run_keeper(
        clock, time.sleep, load_state,
        lambda reason: dispatch(UPDATE_FILE, {"reason": reason}),
        lambda: dispatch(KEEPER_FILE, {}),
        my_id=os.environ.get("GITHUB_RUN_ID"),
        enabled=enabled,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    cmd = (argv if argv is not None else sys.argv[1: ])[:1]
    cmd = cmd[0] if cmd else ""
    if cmd == "check":
        return cmd_check()
    if cmd == "ensure-keeper":
        return cmd_ensure()
    if cmd == "keeper":
        return cmd_keeper()
    print("usage: python -m tito.keeper check|ensure-keeper|keeper", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
