"""devops/opencode/provision.py with a fake Docker and a fake authentik (no containers are created)."""

import base64
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("provision", ROOT / "devops" / "opencode" / "provision.py")
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)

UP, AUTH = provision.UPSTREAM, provision.AUTH


class FakeDocker:
    """Containers by name, plus every command run. Caddy routes per user unless told otherwise."""

    def __init__(self):
        self.containers, self.commands = {}, []
        self.caddy_config = '{"X-Opencode-Upstream": "…"}'
        self.log = ""

    def __call__(self, *args, check=True):
        self.commands.append(args)
        command = args[0]
        if command == "exec" and "wget" in args:
            return self.caddy_config
        if command == "ps":
            return "\n".join(n for n in self.containers if n != "laptop-opencode")
        if command == "inspect" and args[1] == "-f":
            c = self.containers.get(args[-1])
            return "" if c is None else str(c["running"]).lower()
        if command == "inspect":
            c = self.containers[args[1]]
            return json.dumps([{"Config": {"Env": [f"OPENCODE_SERVER_PASSWORD={c['password']}"],
                                           "Labels": {provision.LABEL: c["username"], provision.PK_LABEL: str(c["pk"])}},
                                "State": {"Running": c["running"]}}])
        if command == "run":
            name = args[args.index("--name") + 1]
            env = dict(a.split("=", 1) for a in args if "=" in a and not a.startswith(("laptop.", "/")))
            labels = dict(args[i + 1].split("=", 1) for i, a in enumerate(args) if a == "--label")
            self.containers[name] = {"pk": int(labels[provision.PK_LABEL]), "username": labels[provision.LABEL],
                                     "password": env["OPENCODE_SERVER_PASSWORD"], "running": True, "args": args}
            return name
        if command == "rm":
            self.containers.pop(args[-1], None)
        if command == "start":
            self.containers[args[-1]]["running"] = True
        return ""

    def ran(self, command):
        return [a for a in self.commands if a[0] == command]


class FakeAuthentik:
    def __init__(self, users):
        self.users = users  # [{pk, username, active, headers}]
        self.writes = []

    def list(self):
        return json.loads(json.dumps(self.users))

    def set_headers(self, changes):
        self.writes.append(changes)
        for user in self.users:
            if user["pk"] in changes:
                kept = {k: v for k, v in user["headers"].items() if k not in (UP, AUTH)}
                user["headers"] = {**kept, **(changes[user["pk"]] or {})}


def user(pk, username, active=True, headers=None):
    return {"pk": pk, "username": username, "active": active, "headers": headers or {}}


@pytest.fixture
def world(monkeypatch, tmp_path):
    fake_docker = FakeDocker()
    authentik = FakeAuthentik([user(4, "akadmin"), user(7, "asha@example.com"), user(9, "ravi@example.com")])
    monkeypatch.setattr(provision, "docker", fake_docker)
    monkeypatch.setattr(provision, "authentik_users", authentik.list)
    monkeypatch.setattr(provision, "authentik_set_headers", authentik.set_headers)
    monkeypatch.setattr(provision, "proxy_log", lambda: fake_docker.log)
    monkeypatch.setattr(provision, "REPO", tmp_path)  # no real .env
    monkeypatch.setenv("OPENCODE_BASIC_AUTH", "YWRtaW4tYXV0aA==")
    monkeypatch.delenv("OPENCODE_MAX_USERS", raising=False)
    return fake_docker, authentik


def headers_of(authentik, pk):
    return next(u for u in authentik.users if u["pk"] == pk)["headers"]


def password_in(auth_header):
    return base64.b64decode(auth_header).decode().split(":", 1)[1]


def test_everyone_gets_their_own_container_and_headers(world):
    docker, authentik = world

    provision.provision()

    assert set(docker.containers) == {"opencode-u7", "opencode-u9"}
    for pk in (7, 9):
        h = headers_of(authentik, pk)
        assert h[UP] == f"opencode-u{pk}"
        assert password_in(h[AUTH]) == docker.containers[f"opencode-u{pk}"]["password"]
    assert docker.containers["opencode-u7"]["password"] != docker.containers["opencode-u9"]["password"]
    # The admin keeps compose's laptop-opencode, with its existing password.
    assert headers_of(authentik, 4) == {UP: "laptop-opencode", AUTH: "YWRtaW4tYXV0aA=="}


def test_containers_are_isolated_and_limited(world):
    docker, _ = world
    provision.provision()
    args = docker.containers["opencode-u7"]["args"]
    joined = " ".join(args)
    assert "--network laptop-ai-backend_opencode-net" in joined and joined.count("--network") == 1
    assert "--memory 1g" in joined and "--restart no" in joined
    assert "opencode-u7-data:/root/.local/share/opencode" in args
    assert "opencode-u7-workspace:/root/workspace" in args
    assert f"{provision.USER_CONFIG}:/root/.config/opencode/opencode.json:ro" in args
    assert "OPENWEBUI_API_KEY" not in joined  # no admin key in personal containers
    assert provision.opencode_image() in args


def test_a_second_run_changes_nothing(world):
    docker, authentik = world
    provision.provision()
    before = (dict(docker.containers), len(authentik.writes))

    provision.provision()

    assert len(docker.ran("run")) == 2 and docker.ran("rm") == []
    assert len(authentik.writes) == before[1]


def test_at_most_max_users(world, monkeypatch):
    docker, authentik = world
    monkeypatch.setenv("OPENCODE_MAX_USERS", "1")
    provision.provision()
    assert set(docker.containers) == {"opencode-u7"}  # oldest account first
    assert UP not in headers_of(authentik, 9)


def test_deactivated_accounts_lose_the_container_but_not_the_volumes(world):
    docker, authentik = world
    provision.provision()
    authentik.users[1]["active"] = False

    provision.provision()

    assert "opencode-u7" not in docker.containers
    assert ("rm", "-f", "opencode-u7") in docker.commands  # no -v: named volumes stay
    assert UP not in headers_of(authentik, 7)


def test_deleted_accounts_lose_the_container(world):
    docker, authentik = world
    provision.provision()
    authentik.users = [u for u in authentik.users if u["pk"] != 9]
    provision.provision()
    assert set(docker.containers) == {"opencode-u7"}


def test_a_removed_container_comes_back_with_the_same_password(world):
    docker, authentik = world
    provision.provision()
    old = docker.containers.pop("opencode-u7")["password"]
    provision.provision()
    assert docker.containers["opencode-u7"]["password"] == old


def test_other_additional_headers_are_kept(world):
    docker, authentik = world
    authentik.users[1]["headers"] = {"X-Something-Else": "keep"}
    provision.provision()
    assert headers_of(authentik, 7)["X-Something-Else"] == "keep"


def test_refuses_until_caddy_routes_per_user(world):
    docker, authentik = world
    docker.caddy_config = '{"apps": {}}'
    with pytest.raises(RuntimeError, match="recreate open-webui-proxy"):
        provision.provision()
    assert docker.ran("run") == [] and authentik.writes == []


def test_stopped_containers_are_woken_by_requests(world):
    docker, _ = world
    provision.provision()
    docker.containers["opencode-u7"]["running"] = False
    docker.containers["opencode-u9"]["running"] = False
    docker.log = ('{"level":"error","msg":"dial tcp: lookup opencode-u7 on 127.0.0.11:53: no such host"}\n'
                  '{"level":"info","msg":"opencode-u9 mentioned without an error"}\n')

    provision.provision()

    assert docker.containers["opencode-u7"]["running"] is True
    assert docker.containers["opencode-u9"]["running"] is False


def test_dry_run_changes_nothing(world, capsys):
    docker, authentik = world
    provision.provision(dry_run=True)
    assert docker.ran("run") == [] and authentik.writes == []
    assert "would create opencode-u7 for asha@example.com" in capsys.readouterr().out


def test_service_and_placeholder_accounts_are_excluded_in_the_authentik_query():
    assert 'exclude(username="AnonymousUser")' in provision.LIST_USERS
    assert '("internal", "external")' in provision.LIST_USERS


def test_user_config_has_no_open_webui_key_and_only_reaches_the_proxy():
    config = json.loads(provision.USER_CONFIG.read_text())
    assert config["enabled_providers"] == ["ollama"]
    assert config["provider"]["ollama"]["options"]["baseURL"] == "http://open-webui-proxy:11434/v1"
    assert config["mcp"]["searxng"]["environment"]["SEARXNG_URL"] == "http://open-webui-proxy:8081"
    assert "openwebui" not in json.dumps(config["provider"]) and "apiKey" not in json.dumps(config)


def test_an_empty_user_list_removes_nothing(world):
    docker, authentik = world
    provision.provision()
    authentik.users = []
    with pytest.raises(RuntimeError, match="no active users"):
        provision.provision()
    assert set(docker.containers) == {"opencode-u7", "opencode-u9"}
