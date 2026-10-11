#!/usr/bin/env python3
"""
Run run.py on many repos in parallel.

Usage:
  python3 run_batch.py repos.csv [-j 4] [run.py options...]
  python3 run_batch.py repos.csv --check     # just verify every repo is reachable

repos.csv has rows `name,repo` (header optional); repo is owner/repo, a full https URL,
or an SSH URL (git@host:path, cloned via your ssh-agent; run `ssh-add` first).
Each run writes to <out>/<name>/ (default ./output/<name>/): the extracted
file, report.md (written by Claude), facts.md (precomputed repo facts),
console.log, and the run logs.
Repos are cloned with full git history. Any option not
listed here (--task, --model, --timeout, --branch, --quiet, ...) is passed
through to run.py unchanged.

Default run.py options come from $RUN_ARGS (default: "--task TASK.md
--extract evaluation.json"); options on the command line are added after them.
"""
import argparse
import base64
import csv
import os
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # for `from run import ...`
RUN = HERE / "run.py"
IMAGE = os.environ.get("RUN_IMAGE", "claude-task-runner:latest")
DOCKERFILE = os.environ.get("RUN_DOCKERFILE", "Dockerfile")
DEFAULT_ARGS = "--task TASK.md --extract evaluation.json"


def check_one(name, repo, branch):
    """`git ls-remote` from the host: confirms the repo (and branch) is reachable
    with your credentials without cloning anything."""
    from run import repo_url  # noqa: E402  (run.py sits next to this file)
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0",
               GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=15")
    url = repo_url(repo)
    if os.environ.get("GITHUB_TOKEN") and url.startswith("https://github.com/"):
        # Same token run.py uses; passed via env so it never shows up in `ps`.
        auth = base64.b64encode(f"x-access-token:{os.environ['GITHUB_TOKEN']}".encode())
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.extraHeader",
                   GIT_CONFIG_VALUE_0=f"Authorization: Basic {auth.decode()}")
    cmd = ["git", "ls-remote", "--exit-code", url,
           f"refs/heads/{branch}" if branch else "HEAD"]
    try:
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=45,
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return name, url, False, "timed out after 45s"
    if r.returncode == 0:
        return name, url, True, r.stdout.split()[0][:12]
    if r.returncode == 2:  # reachable, but the ref doesn't exist
        return name, url, False, f"branch {branch!r} not found" if branch else "empty repo"
    e = r.stderr
    if "Permission denied (publickey" in e:
        why = "SSH key rejected (wrong key or not loaded)"
    elif any(s in e for s in ("could not read Username", "Repository not found",
                              "correct access rights", "not found", "Authentication failed")):
        why = "not found, or private and your credentials have no access"
    elif "Could not resolve host" in e or "timed out" in e:
        why = "network error: " + e.strip().splitlines()[-1]
    else:
        err = [ln for ln in e.splitlines() if ln.strip()]
        why = err[-1] if err else f"exit {r.returncode}"
    return name, url, False, why


def check_all(rows, extra, jobs):
    from run import is_ssh_url
    branch = None
    for i, x in enumerate(extra):
        if x == "--branch" and i + 1 < len(extra):
            branch = extra[i + 1]
        elif x.startswith("--branch="):
            branch = x.split("=", 1)[1]
    if any(is_ssh_url(r) for _, r in rows) and subprocess.run(
            ["ssh-add", "-l"], capture_output=True).returncode != 0:
        print("warning: SSH repos listed but your ssh-agent has no keys; the check may "
              "pass using key files, but real runs need `ssh-add`.\n")
    print(f"checking {len(rows)} repos" + (f" (branch {branch})" if branch else ""))
    bad = []
    with ThreadPoolExecutor(jobs) as pool:
        for fut in as_completed([pool.submit(check_one, n, r, branch) for n, r in rows]):
            name, url, ok, info = fut.result()
            print(f"[{'ok' if ok else 'FAIL'}] {name}: {url} ({info})", flush=True)
            if not ok:
                bad.append(name)
    print(f"\n{len(rows) - len(bad)}/{len(rows)} reachable"
          + (f"; unreachable: {', '.join(sorted(bad))}" if bad else ""))
    sys.exit(1 if bad else 0)


def run_one(name, repo, out_root, extra):
    out = out_root / name
    out.mkdir(parents=True, exist_ok=True)
    t = time.time()
    with open(out / "console.log", "w") as f:
        rc = subprocess.run([sys.executable, str(RUN), repo, "--out", str(out), *extra],
                            stdout=f, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL).returncode
    return name, rc, time.time() - t


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("csv")
    p.add_argument("-j", "--jobs", type=int, default=4, help="parallel runs (default 4)")
    p.add_argument("--out", default="output", help="root output dir (default ./output)")
    p.add_argument("--check", action="store_true",
                   help="only check that every repo (and --branch) is reachable; no Docker/Claude")
    a, extra = p.parse_known_args()
    extra = shlex.split(os.environ.get("RUN_ARGS", DEFAULT_ARGS)) + extra

    with open(a.csv, newline="") as f:
        rows = [(r[0].strip(), r[1].strip()) for r in csv.reader(f)
                if len(r) >= 2 and r[0].strip() and r[1].strip()]
    if rows and rows[0] == ("name", "repo"):
        rows = rows[1:]  # optional header
    names = [n for n, _ in rows]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        sys.exit(f"duplicate names in {a.csv}: {', '.join(sorted(dupes))}")

    if a.check:
        check_all(rows, extra, max(a.jobs, 16))

    # Build the image once up front so parallel runs don't all try to.
    if "--rebuild" in extra or subprocess.run(
            ["docker", "image", "inspect", IMAGE], capture_output=True).returncode != 0:
        subprocess.run(["docker", "build", "-t", IMAGE, "-f", str(HERE / DOCKERFILE),
                        str(HERE)], check=True)
    extra = [x for x in extra if x != "--rebuild"]

    out_root = Path(a.out)
    print(f"running {len(rows)} repos, {a.jobs} at a time → {out_root}/<name>/")
    failed = []
    with ThreadPoolExecutor(a.jobs) as pool:
        futs = [pool.submit(run_one, n, r, out_root, extra) for n, r in rows]
        for fut in as_completed(futs):
            name, rc, secs = fut.result()
            print(f"[{'ok' if rc == 0 else 'FAIL'}] {name} ({secs:.0f}s)", flush=True)
            if rc != 0:
                failed.append(name)

    print(f"\n{len(rows) - len(failed)}/{len(rows)} succeeded"
          + (f"; failed: {', '.join(failed)}" if failed else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
