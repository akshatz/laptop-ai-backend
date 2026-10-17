"""devops/opencode/sync-to-postgres.sh and idle-stop.sh, against a real Postgres (see conftest.py)."""

import time

from conftest import sample_data


def counts(oc):
    return oc.sql(
        "select (select count(*) from sessions where deleted_at is null) || '/' ||"
        " (select count(*) from messages where deleted_at is null) || '/' ||"
        " (select count(*) from parts where deleted_at is null)"
    )[0]


# ---- sync-to-postgres.sh -------------------------------------------------------------------------

def test_sync_creates_the_database_and_copies_everything(opencode):
    result = opencode.run("sync-to-postgres.sh")

    assert result.returncode == 0, result.stderr
    assert "2 sessions, 4 messages, 4 parts" in result.stdout
    assert opencode.sql(
        "select s.title, m.role, p.data->>'text' from parts p"
        " join messages m on m.id = p.message_id join sessions s on s.id = p.session_id order by p.id"
    ) == [
        "Session 1|assistant|answer 1", "Session 1|user|question 1",
        "Session 2|assistant|answer 2", "Session 2|user|question 2",
    ]
    # Millisecond timestamps become timestamptz; typed columns come from the session row.
    assert opencode.sql("select extract(epoch from created_at)::bigint, tokens_input, model from sessions where id = 'ses_1'") == [
        "1792224001|100|openwebui/llama3.2:latest"
    ]


def test_sync_twice_adds_no_duplicates(opencode):
    opencode.run("sync-to-postgres.sh")
    result = opencode.run("sync-to-postgres.sh")
    assert result.returncode == 0, result.stderr
    assert opencode.sql("select count(*) from parts") == ["4"]
    assert result.stderr == ""  # no "already exists" notices


def test_changes_are_updated(opencode):
    opencode.run("sync-to-postgres.sh")
    data = sample_data()
    data["session"][0]["title"] = "Renamed"
    opencode.set_data(data)

    opencode.run("sync-to-postgres.sh")

    assert opencode.sql("select title from sessions where id = 'ses_1'") == ["Renamed"]


def test_deleted_sessions_are_kept_and_marked(opencode):
    opencode.run("sync-to-postgres.sh")
    data = sample_data()
    opencode.set_data({
        "session": [s for s in data["session"] if s["id"] != "ses_2"],
        "message": [m for m in data["message"] if m["session_id"] != "ses_2"],
        "part": [p for p in data["part"] if p["session_id"] != "ses_2"],
    })

    opencode.run("sync-to-postgres.sh")

    assert counts(opencode) == "1/2/2"
    assert opencode.sql("select id from sessions where deleted_at is not null") == ["ses_2"]
    assert opencode.sql("select count(*) from parts") == ["4"]  # nothing removed

    opencode.set_data(sample_data())  # back again (e.g. restored): no longer marked
    opencode.run("sync-to-postgres.sh")
    assert counts(opencode) == "2/4/4"


def test_a_failed_read_changes_nothing(opencode):
    opencode.run("sync-to-postgres.sh")

    result = opencode.run("sync-to-postgres.sh", FAKE_DB_FAIL="1")

    assert result.returncode != 0 and "read failed" in result.stderr
    assert counts(opencode) == "2/4/4"  # not everything marked deleted


# ---- idle-stop.sh ---------------------------------------------------------------------------------

def stopped(oc):
    return any(call.startswith("stop ") for call in oc.docker_calls())


def test_not_running_does_nothing(opencode):
    (opencode.state).mkdir(parents=True)
    (opencode.state / "last-active").write_text("1")

    result = opencode.run("idle-stop.sh", FAKE_RUNNING="false")

    assert result.returncode == 0, result.stderr
    assert not stopped(opencode)
    assert not (opencode.state / "last-active").exists()  # next start begins a fresh idle period


def test_an_open_connection_counts_as_use(opencode):
    opencode.state.mkdir(parents=True)
    (opencode.state / "last-active").write_text(str(int(time.time()) - 3600))

    opencode.run("idle-stop.sh", FAKE_CONNECTIONS="2")

    assert not stopped(opencode)
    assert int((opencode.state / "last-active").read_text()) >= int(time.time()) - 5


def test_first_run_starts_the_idle_clock(opencode):
    opencode.run("idle-stop.sh")
    assert not stopped(opencode) and (opencode.state / "last-active").exists()


def test_stops_after_the_idle_limit_with_a_final_copy(opencode):
    opencode.state.mkdir(parents=True)
    (opencode.state / "last-active").write_text(str(int(time.time()) - 31 * 60))
    (opencode.state / "last-synced").write_text("1/1/1/2/4/4")  # nothing changed since the last copy

    result = opencode.run("idle-stop.sh")

    assert result.returncode == 0, result.stderr
    calls = opencode.docker_calls()
    final_copy = max(i for i, c in enumerate(calls) if "--format json" in c)
    assert calls[-1] == "stop laptop-opencode" and final_copy < len(calls) - 1
    assert counts(opencode) == "2/4/4"
    assert not (opencode.state / "last-active").exists()


def test_not_yet_idle_long_enough(opencode):
    opencode.state.mkdir(parents=True)
    (opencode.state / "last-active").write_text(str(int(time.time()) - 29 * 60))
    opencode.run("idle-stop.sh")
    assert not stopped(opencode)


def test_idle_limit_is_configurable(opencode):
    opencode.state.mkdir(parents=True)
    (opencode.state / "last-active").write_text(str(int(time.time()) - 6 * 60))
    opencode.run("idle-stop.sh", OPENCODE_IDLE_MINUTES="5")
    assert stopped(opencode)


def test_copies_only_when_something_changed(opencode):
    copies = lambda: sum("--format json" in c for c in opencode.docker_calls())

    opencode.run("idle-stop.sh", FAKE_CONNECTIONS="1")
    after_first = copies()
    opencode.run("idle-stop.sh", FAKE_CONNECTIONS="1")
    assert after_first == 3 and copies() == 3  # three tables read once; second run: no change, no copy
    assert (opencode.state / "last-synced").read_text().strip() == "1/1/1/2/4/4"

    opencode.run("idle-stop.sh", FAKE_CONNECTIONS="1", FAKE_MARKER="1/1/9/2/4/5")
    assert copies() == 6


def test_a_failed_copy_is_retried_next_run(opencode):
    result = opencode.run("idle-stop.sh", FAKE_CONNECTIONS="1", FAKE_JSON_FAIL="1")
    assert result.returncode == 0 and "copy to Postgres failed" in result.stderr
    assert not (opencode.state / "last-synced").exists()

    opencode.run("idle-stop.sh", FAKE_CONNECTIONS="1")
    assert counts(opencode) == "2/4/4"


def test_unreadable_opencode_db_skips_the_copy(opencode):
    result = opencode.run("idle-stop.sh", FAKE_CONNECTIONS="1", FAKE_DB_FAIL="1")
    assert result.returncode == 0, result.stderr
    assert not any("--format json" in c for c in opencode.docker_calls())
    assert not (opencode.state / "last-synced").exists()


# ---- per-person opencodes ------------------------------------------------------------------------

def ravi_data():
    """Another person's opencode: same shape, different ids."""
    data = sample_data()
    for table in data.values():
        for row in table:
            for key in ("id", "session_id", "message_id"):
                if key in row:
                    row[key] = "ravi_" + row[key]
    return data


def test_rows_carry_their_owner_and_deletion_is_per_owner(opencode):
    opencode.run("sync-to-postgres.sh")  # no OPENCODE_OWNER: the admin's (shared) opencode
    opencode.set_data(ravi_data())

    opencode.run("sync-to-postgres.sh", OPENCODE_OWNER="ravi@example.com")

    assert opencode.sql("select owner, count(*) from sessions group by owner order by owner") == [
        "akadmin|2", "ravi@example.com|2"]
    # Ravi's copy didn't mark the admin's sessions deleted, though they're not in Ravi's opencode.
    assert opencode.sql("select count(*) from sessions where deleted_at is not null") == ["0"]


def test_rows_from_before_owners_existed_become_the_admins(opencode):
    opencode.run("sync-to-postgres.sh")
    opencode.sql("update sessions set owner = null")
    opencode.set_data(ravi_data())
    opencode.run("sync-to-postgres.sh", OPENCODE_OWNER="ravi@example.com")
    assert opencode.sql("select owner, count(*) from sessions group by owner order by owner") == [
        "akadmin|2", "ravi@example.com|2"]


def test_idle_stop_checks_every_personal_container(opencode):
    result = opencode.run("idle-stop.sh", FAKE_PERSONAL="opencode-u7 opencode-u9", FAKE_OWNER="ravi@example.com")
    assert result.returncode == 0, result.stderr
    for name in ("laptop-opencode", "opencode-u7", "opencode-u9"):
        assert (opencode.tmp / "state" / "opencode-idle-stop" / name / "last-active").exists()
    assert opencode.sql("select distinct owner from sessions") == ["ravi@example.com"]
