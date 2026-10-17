#!/usr/bin/env python3
"""Gives every authentik user their own opencode container. Run every minute on the host by
opencode-provision.timer (systemd/ next to this file); it needs the `docker` CLI and nothing else.

Each run:
- every active authentik user (oldest accounts first, at most OPENCODE_MAX_USERS) gets container
  opencode-u<pk> on network opencode-net only, with its own volumes opencode-u<pk>-data and
  opencode-u<pk>-workspace and the config opencode-user.json (local models through the proxy, no
  Open WebUI key). The admin (OPENCODE_ADMIN_USER, default akadmin) keeps compose's laptop-opencode;
- each of them gets authentik attribute additionalHeaders = {X-Opencode-Upstream: <container>,
  X-Opencode-Auth: <base64 opencode:password>}; authentik's outpost hands those to Caddy after
  sign-in, and Caddy sends the person to their own container (Caddyfile, :4443);
- containers of deactivated or deleted accounts are removed; their volumes are kept;
- stopped containers (idle-stop.sh) that Caddy failed to reach in the last 90 s are started again.

This runs on the host on purpose: nothing a signed-up user can reach gets control of Docker.

  python3 devops/opencode/provision.py            # one run (what the timer does)
  python3 devops/opencode/provision.py --dry-run  # print what it would do
"""

import argparse
import base64
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
COMPOSE = REPO / "devops" / "docker-compose.yml"
USER_CONFIG = HERE / "opencode-user.json"

LABEL = "laptop.opencode.user"  # value: authentik username
PK_LABEL = "laptop.opencode.pk"
ADMIN_CONTAINER = "laptop-opencode"
UPSTREAM, AUTH = "X-Opencode-Upstream", "X-Opencode-Auth"
WAKE_PATTERN = re.compile(r"\b(laptop-opencode|opencode-u\d+)\b")
MARKER = "PROVISION_JSON:"

# Runs inside authentik (ak shell): every user that could own an opencode or still carries the
# headers. Service accounts never get one.
LIST_USERS = f"""
import json
from authentik.core.models import User
rows = []
for u in User.objects.exclude(username="AnonymousUser").order_by("date_joined", "pk"):  # authentik's built-in placeholder
    headers = (u.attributes or {{}}).get("additionalHeaders") or {{}}
    if u.type in ("internal", "external") or headers:
        rows.append({{"pk": u.pk, "username": u.username, "active": u.is_active and u.type in ("internal", "external"),
                     "headers": headers}})
print("{MARKER}" + json.dumps(rows))
"""

SET_HEADERS = """
import base64, json
from authentik.core.models import User
for pk, headers in json.loads(base64.b64decode("{payload}")).items():
    user = User.objects.filter(pk=int(pk)).first()
    if user is None:
        continue
    attributes = dict(user.attributes or {{}})
    current = dict(attributes.get("additionalHeaders") or {{}})
    current.pop("{upstream}", None)
    current.pop("{auth}", None)
    if headers:
        current.update(headers)
    if current:
        attributes["additionalHeaders"] = current
    else:
        attributes.pop("additionalHeaders", None)
    user.attributes = attributes
    user.save(update_fields=["attributes"])
"""


def log(message: str) -> None:
    print(message, flush=True)


def docker(*args: str, check: bool = True) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120)
    if check and result.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args[:2])} failed: {result.stderr.strip()[:300]}")
    return result.stdout


def ak_shell(code: str) -> str:
    return docker("exec", os.environ.get("AUTHENTIK_CONTAINER", "laptop-authentik-server"), "ak", "shell", "-c", code)


def authentik_users() -> list[dict]:
    for line in ak_shell(LIST_USERS).splitlines():
        if line.startswith(MARKER):
            return json.loads(line[len(MARKER):])
    raise RuntimeError("authentik returned no user list")


def authentik_set_headers(changes: dict[int, dict | None]) -> None:
    """{pk: {header: value} to set, or None to remove opencode's headers}."""
    payload = base64.b64encode(json.dumps({str(k): v for k, v in changes.items()}).encode()).decode()
    ak_shell(SET_HEADERS.format(payload=payload, upstream=UPSTREAM, auth=AUTH))


def env_file() -> dict:
    env = {}
    path = REPO / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip("'\"")
    return {**env, **os.environ}


def opencode_image() -> str:
    match = re.search(r"image: (ghcr\.io/anomalyco/opencode:\S+)", COMPOSE.read_text())
    if not match:
        raise RuntimeError("opencode image not found in docker-compose.yml")
    return match.group(1)


def basic_auth(password: str) -> str:
    return base64.b64encode(f"opencode:{password}".encode()).decode()


def containers() -> dict[str, dict]:
    """Personal opencode containers by name: {pk, username, running, password}."""
    out = {}
    names = docker("ps", "-a", "--filter", f"label={LABEL}", "--format", "{{.Names}}").split()
    for name in names:
        info = json.loads(docker("inspect", name))[0]
        env = dict(e.split("=", 1) for e in info["Config"]["Env"] if "=" in e)
        labels = info["Config"]["Labels"] or {}
        out[name] = {"pk": int(labels.get(PK_LABEL, -1)), "username": labels.get(LABEL),
                     "running": info["State"]["Running"], "password": env.get("OPENCODE_SERVER_PASSWORD")}
    return out


def create(name: str, user: dict, password: str, config: dict) -> None:
    docker(
        "run", "-d", "--name", name,
        "--label", f"{LABEL}={user['username']}", "--label", f"{PK_LABEL}={user['pk']}",
        "--network", config["network"], "--memory", config["memory"], "--restart", "no",
        "-e", "OPENCODE_SERVER_USERNAME=opencode", "-e", f"OPENCODE_SERVER_PASSWORD={password}",
        "-v", f"{name}-data:/root/.local/share/opencode",
        "-v", f"{name}-workspace:/root/workspace",
        "-v", f"{USER_CONFIG}:/root/.config/opencode/opencode.json:ro",
        "-w", "/root/workspace",
        config["image"], "web", "--hostname", "0.0.0.0", "--port", "4096",
    )


def proxy_log() -> str:
    """Caddy's last 90 s of log (it logs to stderr)."""
    proxy = os.environ.get("OPENCODE_PROXY_CONTAINER", "laptop-open-webui-proxy")
    result = subprocess.run(["docker", "logs", "--since", "90s", proxy], capture_output=True, text=True, timeout=60)
    return result.stdout + result.stderr


def wake_requested() -> set[str]:
    """Containers Caddy failed to reach recently (stopped by idle-stop.sh, so their name doesn't resolve)."""
    names = set()
    for line in proxy_log().splitlines():
        if '"level":"error"' in line or "dial tcp" in line:
            names |= set(WAKE_PATTERN.findall(line))
    return names


def caddy_routes_by_user() -> bool:
    """Whether the running Caddy already sends people to their own container. The previous config
    sent every signed-in user to laptop-opencode, so assigning anyone before then would let them in."""
    proxy = os.environ.get("OPENCODE_PROXY_CONTAINER", "laptop-open-webui-proxy")
    loaded = docker("exec", proxy, "wget", "-qO-", "http://127.0.0.1:2019/config/", check=False)
    return UPSTREAM in loaded


def provision(dry_run: bool = False) -> None:
    if not caddy_routes_by_user():
        raise RuntimeError("the running Caddy doesn't route :4443 per user yet: recreate open-webui-proxy "
                           "(docker compose -f devops/docker-compose.yml up -d --no-deps --force-recreate open-webui-proxy)")
    env = env_file()
    max_users = int(env.get("OPENCODE_MAX_USERS", "4"))
    admin = env.get("OPENCODE_ADMIN_USER", "akadmin")
    config = {"image": opencode_image(), "memory": env.get("OPENCODE_MEMORY", "1g"),
              "network": env.get("OPENCODE_NETWORK", "laptop-ai-backend_opencode-net")}
    act = (lambda what, fn, *a: log(f"would {what}")) if dry_run else (lambda what, fn, *a: (log(what), fn(*a)))

    users = authentik_users()
    if not any(u["active"] for u in users):
        # Never read as "everyone left": that would remove every personal container.
        raise RuntimeError("authentik returned no active users; changing nothing")
    existing = containers()
    by_pk = {c["pk"]: name for name, c in existing.items()}
    wanted_headers: dict[int, dict | None] = {}
    keep = set()

    placed = 0
    for user in users:
        if not user["active"]:
            continue
        if user["username"] == admin:
            if env.get("OPENCODE_BASIC_AUTH"):
                wanted_headers[user["pk"]] = {UPSTREAM: ADMIN_CONTAINER, AUTH: env["OPENCODE_BASIC_AUTH"]}
            continue
        if placed >= max_users:
            if user["headers"].get(UPSTREAM):
                wanted_headers[user["pk"]] = None
            log(f"no opencode for {user['username']}: all {max_users} places taken (OPENCODE_MAX_USERS)")
            continue
        placed += 1
        name = by_pk.get(user["pk"], f"opencode-u{user['pk']}")
        keep.add(name)
        password = (existing.get(name) or {}).get("password")
        if not password:
            # Reuse the password authentik already has (container removed by hand), else a new one.
            old = user["headers"].get(AUTH)
            try:
                password = base64.b64decode(old).decode().split(":", 1)[1] if old else None
            except Exception:
                password = None
            password = password or secrets.token_urlsafe(24)
            act(f"create {name} for {user['username']}", create, name, user, password, config)
        wanted_headers[user["pk"]] = {UPSTREAM: name, AUTH: basic_auth(password)}

    for name, container in existing.items():
        if name not in keep:
            act(f"remove {name} ({container['username']}: account deactivated or deleted; volumes kept)",
                docker, "rm", "-f", name)

    changes = {}
    for user in users:
        current = {k: v for k, v in user["headers"].items() if k in (UPSTREAM, AUTH)} or None
        wanted = wanted_headers.get(user["pk"])
        if current != wanted:
            changes[user["pk"]] = wanted
    if changes:
        act(f"update authentik headers for {len(changes)} user(s)", authentik_set_headers, changes)

    for name in sorted(wake_requested()):
        stopped = name == ADMIN_CONTAINER or (name in existing and name in keep)
        state = docker("inspect", "-f", "{{.State.Running}}", name, check=False).strip()
        if stopped and state == "false":
            act(f"start {name} (requested through Caddy)", docker, "start", name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="print what would change")
    args = parser.parse_args()
    try:
        provision(args.dry_run)
    except RuntimeError as e:
        sys.exit(f"provision: {e}")


if __name__ == "__main__":
    main()
