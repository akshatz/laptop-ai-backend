#!/usr/bin/env python3
"""Apply devops/openobserve/alerts.yaml to OpenObserve: the email destination and every alert,
created or updated by name. Alerts not in the file are left alone. Needs PyYAML.

  python3 devops/openobserve/alerts.py             # apply
  python3 devops/openobserve/alerts.py --dry-run   # print the API payloads

With BAO_TOKEN set (e.g. from `bao login -method=oidc` or the bao-readonly login), the O2 login and
alert email come from OpenBao's apps/default/openobserve (basic_auth, alert_email; BAO_ADDR defaults
to http://127.0.0.1:8200). Otherwise, or for a key missing there, OPENOBSERVE_BASIC_AUTH and
OPENOBSERVE_ALERT_EMAIL from the repo's .env (or the environment) are used.
OPENOBSERVE_URL defaults to http://127.0.0.1:5080. Emails need O2's ZO_SMTP_* settings (compose).
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ORG = "default"


def load_env() -> dict:
    env = {}
    path = REPO / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip("'\"")
    return {**env, **os.environ}


def from_openbao(env: dict) -> dict:
    """apps/default/openobserve as .env-style keys, or {} without a token. A token that OpenBao
    refuses (expired, sealed) is an error rather than a silent fallback to .env."""
    token = env.get("BAO_TOKEN")
    if not token:
        return {}
    addr = env.get("BAO_ADDR", "http://127.0.0.1:8200").rstrip("/")
    req = urllib.request.Request(f"{addr}/v1/apps/data/default/openobserve", headers={"X-Vault-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)["data"]["data"]
    except (urllib.error.URLError, KeyError) as e:
        sys.exit(f"could not read apps/default/openobserve from OpenBao ({addr}): {e}")
    keys = {"basic_auth": "OPENOBSERVE_BASIC_AUTH", "alert_email": "OPENOBSERVE_ALERT_EMAIL"}
    return {env_key: data[k] for k, env_key in keys.items() if data.get(k)}


class O2:
    def __init__(self, url: str, auth: str):
        self.url, self.auth = url.rstrip("/"), auth

    def wait_ready(self, max_wait: int = 90) -> None:
        """O2 resets connections for a few seconds after a (re)start."""
        deadline = time.monotonic() + max_wait
        while True:
            try:
                with urllib.request.urlopen(self.url + "/healthz", timeout=5):
                    return
            except (urllib.error.URLError, ConnectionError, OSError):
                if time.monotonic() > deadline:
                    sys.exit(f"OpenObserve at {self.url} not ready after {max_wait} s")
                time.sleep(2)

    def call(self, method: str, path: str, body=None):
        req = urllib.request.Request(
            self.url + path, method=method, data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": f"Basic {self.auth}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                text = resp.read().decode()
        except urllib.error.HTTPError as e:
            sys.exit(f"{method} {path}: HTTP {e.code} {e.read().decode()[:300]}")
        return json.loads(text) if text else None


def destination_payload(name: str, email: str) -> dict:
    return {"name": name, "type": "email", "emails": [email], "template": "o2_default_content"}


def alert_payload(a: dict, destination: str) -> dict:
    return {
        "name": a["name"],
        "stream_type": "logs",
        "stream_name": a["stream"],
        "is_real_time": False,
        "enabled": a.get("enabled", True),
        "description": a.get("description", "").strip(),
        "destinations": [destination],
        "query_condition": {"type": "sql", "sql": a["sql"]},
        "trigger_condition": {
            "period": a["period"],
            "operator": a.get("operator", ">="),
            "threshold": a["threshold"],
            "frequency": a["every"] * 60,
            "frequency_type": "minutes",
            "silence": a["silence"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print payloads, change nothing")
    args = parser.parse_args()

    env = load_env()
    if not args.dry_run:
        bao = from_openbao(env)
        if bao:
            print(f"using OpenBao for: {', '.join(sorted(bao))}")
        env.update(bao)
    spec = yaml.safe_load((HERE / "alerts.yaml").read_text())
    dest_name = spec["destination"]
    email = env.get("OPENOBSERVE_ALERT_EMAIL", "")
    if not email and not args.dry_run:
        sys.exit("OPENOBSERVE_ALERT_EMAIL is not set (.env)")
    dest = destination_payload(dest_name, email or "you@example.com")
    alerts = [alert_payload(a, dest_name) for a in spec["alerts"]]
    if args.dry_run:
        print(json.dumps({"destination": dest, "alerts": alerts}, indent=2, ensure_ascii=False))
        return

    if not env.get("OPENOBSERVE_BASIC_AUTH"):
        sys.exit("OPENOBSERVE_BASIC_AUTH is not set (.env)")
    o2 = O2(env.get("OPENOBSERVE_URL", "http://127.0.0.1:5080"), env["OPENOBSERVE_BASIC_AUTH"])
    o2.wait_ready()

    existing = {d["name"] for d in o2.call("GET", f"/api/{ORG}/alerts/destinations")}
    if dest_name in existing:
        o2.call("PUT", f"/api/{ORG}/alerts/destinations/{dest_name}", dest)
        print(f"updated destination {dest_name}")
    else:
        o2.call("POST", f"/api/{ORG}/alerts/destinations", dest)
        print(f"created destination {dest_name}")

    ids = {a["name"]: a["alert_id"] for a in o2.call("GET", f"/api/v2/{ORG}/alerts")["list"]}
    for alert in alerts:
        if alert["name"] in ids:
            o2.call("PUT", f"/api/v2/{ORG}/alerts/{ids[alert['name']]}", alert)
            print(f"updated alert {alert['name']}")
        else:
            o2.call("POST", f"/api/v2/{ORG}/alerts", alert)
            print(f"created alert {alert['name']}")


if __name__ == "__main__":
    main()
