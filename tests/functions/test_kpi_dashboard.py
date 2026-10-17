import calendar
import time
from types import SimpleNamespace

from conftest import load_function, owui

kpi = load_function("kpi_dashboard")

COUNT_FROM = calendar.timegm(time.strptime(kpi.COUNT_FROM, "%Y-%m-%d"))


def test_formatting_helpers():
    assert kpi._secs(None) == "—" and kpi._secs(12.6) == "13 s"
    assert kpi._pct(1, 0) == "—" and kpi._pct(1, 3) == "33%"
    assert kpi._ratio(1, 0) is None and kpi._ratio(1, 4) == 25


def test_delta_colours_by_direction():
    assert kpi._delta(None, 3) == ""
    assert kpi._delta(5, 5) == "<small>±0</small>"
    assert kpi._delta(4, 5, " s") == "<small class='better'>-1 s</small>"  # lower is better
    assert kpi._delta(6, 5, "%", lower_is_better=False) == "<small class='better'>+1%</small>"
    assert kpi._delta(5.04, 5, digits=1) == "<small>±0</small>"


def test_period_never_starts_before_count_from(monkeypatch):
    monkeypatch.setattr(kpi.time, "time", lambda: COUNT_FROM + 3 * 86400)
    assert kpi._since(None) == COUNT_FROM
    assert kpi._since(1) == COUNT_FROM + 2 * 86400
    assert kpi._since(30) == COUNT_FROM  # 30 days back would be before COUNT_FROM
    assert kpi._period(7) == f"Last 7 days (not before {kpi.COUNT_FROM})"


def test_period_links_bold_the_current_one():
    links = kpi._period_links(7, lambda label, d: f"[{label}]")
    assert links == "[All] [1d] <b>7d</b> [14d] [30d]"


async def test_viewers_are_admins_or_the_viewer_group():
    fn = kpi.Event()
    owui.Groups.get_groups_by_member_id.return_value = [SimpleNamespace(name="KPI-Viewers")]
    assert await fn._may_view(SimpleNamespace(id="a", role="admin"))
    assert await fn._may_view(SimpleNamespace(id="u", role="user"))  # group name is case-insensitive
    assert not await fn._may_view_people(SimpleNamespace(id="u", role="user"))  # per-person page: admins only

    owui.Groups.get_groups_by_member_id.return_value = []
    assert not await fn._may_view(SimpleNamespace(id="u", role="user"))
    fn.valves.viewer_group = ""
    owui.Groups.get_groups_by_member_id.return_value = [SimpleNamespace(name="kpi-viewers")]
    assert not await fn._may_view(SimpleNamespace(id="u", role="user"))
