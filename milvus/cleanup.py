#!/usr/bin/env python3
"""Clean up Open WebUI's Milvus data. Shows what it would delete unless --delete is given.

  legacy      Drop the per-resource collections Open WebUI made before
              ENABLE_MILVUS_MULTITENANCY_MODE (open_webui_web_search_*, open_webui_file_*,
              open_webui_<knowledge id>, open_webui_knowledge_bases) and laptop-backend's
              unused nomic-era custom_backend_docs. One-off, after the switch and after
              reindexing knowledge (Admin → Settings → Documents).
  web-search  Delete all rows of the shared open_webui_web_search collection. Web search
              chunks are only needed while an answer is being written (the chat keeps its
              sources, and the Embedding Cache Function keeps the vectors), and the shared
              collection has no timestamp to delete by age, so this empties it. Run daily
              at a quiet hour (milvus/systemd/milvus-web-search-cleanup.timer).

Never touches the other shared collections (open_webui_files, _knowledge, _memories,
_hash_based) or laptop-backend's current custom_backend_docs_<model> collections.

Runs pymilvus inside the laptop-open-webui container (the host needs only Python 3);
MILVUS_ROOT_PASSWORD comes from the repo's .env.
"""
import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHARED = {
    "open_webui_web_search",
    "open_webui_files",
    "open_webui_knowledge",
    "open_webui_memories",
    "open_webui_hash_based",
}

# Runs inside the container; reads the mode, the delete flag and the shared names from argv.
REMOTE = r"""
import os, sys
from pymilvus import MilvusClient
mode, delete, shared = sys.argv[1], sys.argv[2] == "1", set(sys.argv[3].split(","))
c = MilvusClient(uri="http://milvus:19530", token="root:" + os.environ["MILVUS_ROOT_PASSWORD"])
if mode == "legacy":
    names = sorted(
        n for n in c.list_collections()
        if n == "custom_backend_docs" or (n.startswith("open_webui_") and n not in shared)
    )
    print(f"{len(names)} legacy collection(s){'' if delete else ' (dry run, nothing deleted)'}:")
    for n in names:
        print(f"  {n}")
        if delete:
            c.drop_collection(n)
    if delete:
        print(f"dropped {len(names)}")
else:
    name = "open_webui_web_search"
    if not c.has_collection(name):
        print(f"{name} doesn't exist yet (nothing searched since the switch)")
        sys.exit(0)
    c.load_collection(name)
    before = c.query(name, filter="", output_fields=["count(*)"])[0]["count(*)"]
    print(f"{name}: {before} row(s){'' if delete else ' (dry run, nothing deleted)'}")
    if delete and before:
        c.delete(name, filter='resource_id != ""')
        c.flush(name)
        print(f"deleted {before} row(s)")
"""


def env_value(key):
    for line in (REPO / ".env").read_text().splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit(f"{key} not found in {REPO / '.env'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["legacy", "web-search"])
    ap.add_argument("--delete", action="store_true", help="really delete (default: only list)")
    args = ap.parse_args()
    # The password reaches the container as an environment variable passed through by name
    # (--env NAME, value taken from this process's environment), so it never appears in argv.
    var = "MILVUS_ROOT_PASSWORD"
    container = "laptop-open-webui"
    cmd = ["docker", "exec", "-i", f"--env={var}", container, "python3", "-"]
    cmd += [args.mode, "1" if args.delete else "0", ",".join(sorted(SHARED))]
    env = {var: env_value(var), "PATH": "/usr/bin:/bin:/usr/local/bin"}
    sys.exit(subprocess.run(cmd, input=REMOTE, text=True, env=env).returncode)


if __name__ == "__main__":
    main()
