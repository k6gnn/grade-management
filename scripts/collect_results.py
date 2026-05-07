#!/usr/bin/env python3
"""
Experiment Results Collection Script
=====================================
Collects pipeline logs, artifacts, and metadata from all three CI/CD platforms
after each experiment run and saves them into a structured local directory.

Usage:
    python collect_results.py <experiment_id> <run_number> <platform> <injection_type>

Examples:
    python collect_results.py E1 1 github compilation
    python collect_results.py E1 1 gitlab compilation
    python collect_results.py E1 1 jenkins compilation
    python collect_results.py E1 1 all compilation        # collect from all 3 at once

Arguments:
    experiment_id   — E1, E2, E3, E4, E5, E5b, E5c ... E15
    run_number      — 1–10 (or 1–5 if using reduced run count)
    platform        — github | gitlab | jenkins | all
    injection_type  — compilation | test | flaky | configuration | infrastructure | etc.

Required environment variables (set these once in your shell, never hardcode):
    GITHUB_TOKEN    — GitHub fine-grained PAT with Actions:read, Contents:read
    GITLAB_TOKEN    — GitLab personal access token with read_api scope
    JENKINS_TOKEN   — Jenkins API token for user k6gnn

Output structure:
    experiment_results/
    └── E1/
        ├── github/
        │   └── run_1/
        │       ├── metadata.json                    ← added by this script
        │       ├── m13_classification_report.json   ← from anomaly-detection job
        │       ├── pipeline_status.json             ← from anomaly-detection job
        │       ├── m14_risk_report.json             ← from m14-risk-assessment job
        │       ├── build.log                        ← from build job
        │       ├── test.log                         ← from test job
        │       ├── flaky_failure_log.txt            ← from test job
        │       └── surefire_reports/                ← from test job
        ├── gitlab/
        │   └── run_1/
        │       └── ...  (same files, from GitLab job artifacts)
        └── jenkins/
            └── run_1/
                └── ...  (same files, from Jenkins archived artifacts)
"""

import os
import sys
import json
import time
import zipfile
import requests
import argparse
from pathlib import Path
from datetime import datetime, timezone

# ─── Configuration ────────────────────────────────────────────────────────────

GITHUB_OWNER   = "k6gnn"
GITHUB_REPO    = "grade-management"

GITLAB_USER    = "k6gnn"
GITLAB_PROJECT = "grade-management"          # adjust if your GitLab project name differs

JENKINS_URL    = "http://localhost:8081"
JENKINS_USER   = "k6gnn"
JENKINS_JOB    = "grade-management"

OUTPUT_ROOT    = Path("experiment_results")

# ─── Token helpers ────────────────────────────────────────────────────────────

def get_token(env_var: str) -> str:
    token = os.environ.get(env_var, "").strip()
    if not token:
        print(f"ERROR: Environment variable {env_var} is not set.")
        print(f"  Set it with: export {env_var}=your_token_here")
        sys.exit(1)
    return token

# ─── Output directory helpers ─────────────────────────────────────────────────

def run_dir(experiment_id: str, platform: str, run_number: int) -> Path:
    d = OUTPUT_ROOT / experiment_id / platform / f"run_{run_number}"
    d.mkdir(parents=True, exist_ok=True)
    return d

def save_json(path: Path, data: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def save_text(path: Path, text: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

# ═════════════════════════════════════════════════════════════════════════════
# GITHUB ACTIONS COLLECTOR
# ═════════════════════════════════════════════════════════════════════════════

def collect_github(experiment_id: str, run_number: int, injection_type: str):
    print(f"\n[GITHUB] Collecting {experiment_id} run {run_number}...")
    token = get_token("GITHUB_TOKEN")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    base = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
    out  = run_dir(experiment_id, "github", run_number)

    # ── Get the most recent workflow run ──────────────────────────────────────
    resp = requests.get(
        f"{base}/actions/runs",
        headers=headers,
        params={"branch": "main", "per_page": 5}
    )
    resp.raise_for_status()
    runs = resp.json().get("workflow_runs", [])
    if not runs:
        print("  WARNING: No workflow runs found.")
        return False

    latest = runs[0]
    run_id     = latest["id"]
    conclusion = latest.get("conclusion") or latest.get("status", "unknown")
    started_at = latest.get("run_started_at", "")
    updated_at = latest.get("updated_at", "")

    # Calculate MTTR (seconds from start to completion)
    mttr_seconds = None
    if started_at and updated_at:
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        try:
            start = datetime.strptime(started_at, fmt).replace(tzinfo=timezone.utc)
            end   = datetime.strptime(updated_at, fmt).replace(tzinfo=timezone.utc)
            mttr_seconds = int((end - start).total_seconds())
        except ValueError:
            pass

    # ── Save metadata ─────────────────────────────────────────────────────────
    metadata = {
        "experiment_id":    experiment_id,
        "run_number":       run_number,
        "platform":         "github",
        "injection_type":   injection_type,
        "workflow_run_id":  run_id,
        "conclusion":       conclusion,
        "started_at":       started_at,
        "updated_at":       updated_at,
        "mttr_seconds":     mttr_seconds,
        "run_url":          latest.get("html_url", ""),
        "collected_at":     datetime.now(timezone.utc).isoformat()
    }
    save_json(out / "metadata.json", metadata)
    print(f"  Run ID: {run_id} | Result: {conclusion} | MTTR: {mttr_seconds}s")

    # ── Download artifacts ────────────────────────────────────────────────────
    artifacts_resp = requests.get(
        f"{base}/actions/runs/{run_id}/artifacts",
        headers=headers
    )
    artifacts_resp.raise_for_status()
    artifacts = artifacts_resp.json().get("artifacts", [])

    if not artifacts:
        print("  WARNING: No artifacts found for this run.")
    else:
        print(f"  Found {len(artifacts)} artifact(s)")

    for artifact in artifacts:
        name = artifact["name"]
        print(f"  Downloading artifact: {name}")
        dl_resp = requests.get(
            f"{base}/actions/artifacts/{artifact['id']}/zip",
            headers=headers,
            allow_redirects=True
        )
        dl_resp.raise_for_status()

        zip_path = out / f"{name}.zip"
        with open(zip_path, "wb") as f:
            f.write(dl_resp.content)

        # Extract zip — flatten into run directory
        try:
            with zipfile.ZipFile(zip_path, "r") as z:
                for member in z.namelist():
                    filename = Path(member).name
                    if not filename:
                        continue
                    # Surefire reports go into a subdirectory
                    if "surefire" in member.lower():
                        surefire_dir = out / "surefire_reports"
                        surefire_dir.mkdir(exist_ok=True)
                        target = surefire_dir / filename
                    else:
                        target = out / filename
                    with z.open(member) as src, open(target, "wb") as dst:
                        dst.write(src.read())
            zip_path.unlink()  # remove zip after extraction
        except zipfile.BadZipFile:
            print(f"  WARNING: Could not extract {name}.zip — may be empty")

    # ── Download job logs ─────────────────────────────────────────────────────
    jobs_resp = requests.get(
        f"{base}/actions/runs/{run_id}/jobs",
        headers=headers
    )
    jobs_resp.raise_for_status()
    jobs = jobs_resp.json().get("jobs", [])

    for job in jobs:
        job_name = job["name"].replace(" ", "_").replace("/", "-")
        log_resp = requests.get(
            f"{base}/actions/jobs/{job['id']}/logs",
            headers=headers,
            allow_redirects=True
        )
        if log_resp.status_code == 200:
            save_text(out / f"job_{job_name}.log", log_resp.text)

    print(f"  Saved to: {out}")
    return out, metadata
# ═════════════════════════════════════════════════════════════════════════════

def collect_gitlab(experiment_id: str, run_number: int, injection_type: str):
    print(f"\n[GITLAB] Collecting {experiment_id} run {run_number}...")
    token = get_token("GITLAB_TOKEN")
    headers = {"PRIVATE-TOKEN": token}
    out = run_dir(experiment_id, "gitlab", run_number)

    # Resolve numeric project ID from namespace/project slug
    encoded_path = f"{GITLAB_USER}%2F{GITLAB_PROJECT}"
    base = f"https://gitlab.com/api/v4/projects/{encoded_path}"

    # ── Get the most recent pipeline ─────────────────────────────────────────
    resp = requests.get(
        f"{base}/pipelines",
        headers=headers,
        params={"ref": "main", "per_page": 5, "order_by": "id", "sort": "desc"}
    )
    resp.raise_for_status()
    pipelines = resp.json()

    if not pipelines:
        print("  WARNING: No pipelines found.")
        return False

    pipeline = pipelines[0]
    pipeline_id = pipeline["id"]
    status      = pipeline.get("status", "unknown")
    started_at  = pipeline.get("started_at", "") or ""
    finished_at = pipeline.get("finished_at", "") or ""

    # Calculate MTTR
    mttr_seconds = None
    if started_at and finished_at:
        fmt = "%Y-%m-%dT%H:%M:%S.%fZ"
        fmt_simple = "%Y-%m-%dT%H:%M:%SZ"
        for fmt_try in [fmt, fmt_simple]:
            try:
                start = datetime.strptime(started_at, fmt_try).replace(tzinfo=timezone.utc)
                end   = datetime.strptime(finished_at, fmt_try).replace(tzinfo=timezone.utc)
                mttr_seconds = int((end - start).total_seconds())
                break
            except ValueError:
                continue

    # Map GitLab status to pass/fail
    conclusion = "success" if status == "success" else ("failed" if status == "failed" else status)

    metadata = {
        "experiment_id":  experiment_id,
        "run_number":     run_number,
        "platform":       "gitlab",
        "injection_type": injection_type,
        "pipeline_id":    pipeline_id,
        "conclusion":     conclusion,
        "started_at":     started_at,
        "finished_at":    finished_at,
        "mttr_seconds":   mttr_seconds,
        "run_url":        pipeline.get("web_url", ""),
        "collected_at":   datetime.now(timezone.utc).isoformat()
    }
    save_json(out / "metadata.json", metadata)
    print(f"  Pipeline ID: {pipeline_id} | Result: {conclusion} | MTTR: {mttr_seconds}s")

    # ── Get jobs and download logs + artifacts ────────────────────────────────
    jobs_resp = requests.get(
        f"{base}/pipelines/{pipeline_id}/jobs",
        headers=headers,
        params={"per_page": 50}
    )
    jobs_resp.raise_for_status()
    jobs = jobs_resp.json()

    print(f"  Found {len(jobs)} job(s)")
    for job in jobs:
        job_id   = job["id"]
        job_name = job["name"].replace(" ", "_").replace("/", "-")

        # Download job log (trace)
        log_resp = requests.get(
            f"{base}/jobs/{job_id}/trace",
            headers=headers
        )
        if log_resp.status_code == 200:
            save_text(out / f"job_{job_name}.log", log_resp.text)

        # Download job artifacts
        art_resp = requests.get(
            f"{base}/jobs/{job_id}/artifacts",
            headers=headers,
            stream=True
        )
        if art_resp.status_code == 200:
            zip_path = out / f"artifacts_{job_name}.zip"
            with open(zip_path, "wb") as f:
                for chunk in art_resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            try:
                with zipfile.ZipFile(zip_path, "r") as z:
                    for member in z.namelist():
                        filename = Path(member).name
                        if not filename:
                            continue
                        if "surefire" in member.lower():
                            surefire_dir = out / "surefire_reports"
                            surefire_dir.mkdir(exist_ok=True)
                            target = surefire_dir / filename
                        else:
                            target = out / filename
                        with z.open(member) as src, open(target, "wb") as dst:
                            dst.write(src.read())
                zip_path.unlink()
            except zipfile.BadZipFile:
                zip_path.unlink(missing_ok=True)

    print(f"  Saved to: {out}")
    return out, metadata

# ═════════════════════════════════════════════════════════════════════════════
# JENKINS COLLECTOR
# ═════════════════════════════════════════════════════════════════════════════

def collect_jenkins(experiment_id: str, run_number: int, injection_type: str):
    print(f"\n[JENKINS] Collecting {experiment_id} run {run_number}...")
    token = get_token("JENKINS_TOKEN")
    auth  = (JENKINS_USER, token)
    out   = run_dir(experiment_id, "jenkins", run_number)

    job_base = f"{JENKINS_URL}/job/{JENKINS_JOB}"

    # ── Get the most recent build ─────────────────────────────────────────────
    resp = requests.get(
        f"{job_base}/lastBuild/api/json",
        auth=auth
    )
    resp.raise_for_status()
    build = resp.json()

    build_number = build["number"]
    result       = (build.get("result") or "IN_PROGRESS").lower()
    duration_ms  = build.get("duration", 0)
    timestamp_ms = build.get("timestamp", 0)
    mttr_seconds = int(duration_ms / 1000) if duration_ms else None

    started_at  = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat() if timestamp_ms else ""
    finished_at = datetime.fromtimestamp(
        (timestamp_ms + duration_ms) / 1000, tz=timezone.utc
    ).isoformat() if (timestamp_ms and duration_ms) else ""

    conclusion = "success" if result == "success" else ("failed" if result == "failure" else result)

    metadata = {
        "experiment_id":  experiment_id,
        "run_number":     run_number,
        "platform":       "jenkins",
        "injection_type": injection_type,
        "build_number":   build_number,
        "conclusion":     conclusion,
        "started_at":     started_at,
        "finished_at":    finished_at,
        "mttr_seconds":   mttr_seconds,
        "run_url":        f"{job_base}/{build_number}/",
        "collected_at":   datetime.now(timezone.utc).isoformat()
    }
    save_json(out / "metadata.json", metadata)
    print(f"  Build #{build_number} | Result: {conclusion} | MTTR: {mttr_seconds}s")

    # ── Download console log ──────────────────────────────────────────────────
    log_resp = requests.get(
        f"{job_base}/{build_number}/consoleText",
        auth=auth
    )
    if log_resp.status_code == 200:
        save_text(out / "console.log", log_resp.text)
        console = log_resp.text
        # Extract sections using the exact echo strings from the real Jenkinsfile
        _extract_jenkins_section(console, "=== Stage 1: Build ===",      "=== Stage 2:",         out / "build.log")
        _extract_jenkins_section(console, "=== Stage 2: Test ===",       "=== Stage 3:",         out / "test.log")
        _extract_jenkins_section(console, "=== M13: ML Failure",         "Finished: ",           out / "m13.log")
        _extract_jenkins_section(console, "=== M14: Proactive",          "=== M9 Environment",   out / "m14.log")

    # ── Download archived artifacts ───────────────────────────────────────────
    art_resp = requests.get(
        f"{job_base}/{build_number}/artifact/*zip*/archive.zip",
        auth=auth,
        stream=True
    )
    if art_resp.status_code == 200:
        zip_path = out / "archive.zip"
        with open(zip_path, "wb") as f:
            for chunk in art_resp.iter_content(chunk_size=8192):
                f.write(chunk)
        try:
            with zipfile.ZipFile(zip_path, "r") as z:
                for member in z.namelist():
                    filename = Path(member).name
                    if not filename:
                        continue
                    if "surefire" in member.lower():
                        surefire_dir = out / "surefire_reports"
                        surefire_dir.mkdir(exist_ok=True)
                        target = surefire_dir / filename
                    else:
                        target = out / filename
                    with z.open(member) as src, open(target, "wb") as dst:
                        dst.write(src.read())
            zip_path.unlink()
        except zipfile.BadZipFile:
            zip_path.unlink(missing_ok=True)
    else:
        print("  No archived artifacts found (pipeline may have failed before package stage)")

    print(f"  Saved to: {out}")
    return out, metadata


def _extract_jenkins_section(console: str, start_marker: str, end_marker: str, out_path: Path):
    """Extract a section of the Jenkins console log between two markers."""
    start_idx = console.find(start_marker)
    if start_idx == -1:
        return
    end_idx = console.find(end_marker, start_idx)
    section = console[start_idx:end_idx] if end_idx != -1 else console[start_idx:]
    save_text(out_path, section)

# ═════════════════════════════════════════════════════════════════════════════
# RESULTS SUMMARY — append one row to master CSV
# ═════════════════════════════════════════════════════════════════════════════

def append_to_csv(metadata: dict, out: Path = None):
    """
    Appends a result row to experiment_results/results.csv.
    Creates the file with headers if it doesn't exist.
    Also reads m13_classification_report.json from the run directory
    to capture the M13 predicted class if available.
    """
    # Try to read M13 classification from downloaded artifact
    m13_class = ""
    m13_correct = ""
    if out and (out / "m13_classification_report.json").exists():
        try:
            with open(out / "m13_classification_report.json") as f:
                m13_data = json.load(f)
            m13_class = m13_data.get("predicted_class", m13_data.get("failure_type", ""))
        except Exception:
            pass

    # Try to read M14 risk level
    m14_risk = ""
    if out and (out / "m14_risk_report.json").exists():
        try:
            with open(out / "m14_risk_report.json") as f:
                m14_data = json.load(f)
            m14_risk = m14_data.get("risk_level", m14_data.get("status", ""))
        except Exception:
            pass

    csv_path = OUTPUT_ROOT / "results.csv"
    headers = [
        "experiment_id", "run_number", "platform", "injection_type",
        "conclusion", "mttr_seconds", "started_at", "collected_at",
        "m13_classification", "m14_risk_level", "run_url"
    ]

    write_header = not csv_path.exists()
    with open(csv_path, "a", encoding="utf-8") as f:
        if write_header:
            f.write(",".join(headers) + "\n")
        row = [
            str(metadata.get("experiment_id", "")),
            str(metadata.get("run_number", "")),
            str(metadata.get("platform", "")),
            str(metadata.get("injection_type", "")),
            str(metadata.get("conclusion", "")),
            str(metadata.get("mttr_seconds", "")),
            str(metadata.get("started_at", "")),
            str(metadata.get("collected_at", "")),
            m13_class,
            m14_risk,
            str(metadata.get("run_url", "")),
        ]
        f.write(",".join(row) + "\n")

    print(f"\n  Appended to results.csv: {csv_path}")

# ═════════════════════════════════════════════════════════════════════════════
# WAIT FOR PIPELINE — polls until complete before collecting
# ═════════════════════════════════════════════════════════════════════════════

def wait_for_github(timeout_seconds: int = 600) -> bool:
    """Polls GitHub until the latest run is no longer in_progress/queued."""
    token   = get_token("GITHUB_TOKEN")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    base = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
    print("  Waiting for GitHub Actions pipeline to complete", end="", flush=True)
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        resp = requests.get(f"{base}/actions/runs", headers=headers, params={"per_page": 1})
        resp.raise_for_status()
        runs = resp.json().get("workflow_runs", [])
        if runs:
            status = runs[0].get("status")
            if status not in ("in_progress", "queued", "waiting", "requested"):
                print(f" done ({status})")
                return True
        print(".", end="", flush=True)
        time.sleep(15)
    print(" TIMEOUT")
    return False

def wait_for_gitlab(timeout_seconds: int = 600) -> bool:
    """Polls GitLab until the latest pipeline is no longer running."""
    token   = get_token("GITLAB_TOKEN")
    headers = {"PRIVATE-TOKEN": token}
    encoded = f"{GITLAB_USER}%2F{GITLAB_PROJECT}"
    base    = f"https://gitlab.com/api/v4/projects/{encoded}"
    print("  Waiting for GitLab pipeline to complete", end="", flush=True)
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        resp = requests.get(f"{base}/pipelines", headers=headers,
                            params={"ref": "main", "per_page": 1})
        resp.raise_for_status()
        pipelines = resp.json()
        if pipelines:
            status = pipelines[0].get("status")
            if status not in ("running", "pending", "created", "waiting_for_resource"):
                print(f" done ({status})")
                return True
        print(".", end="", flush=True)
        time.sleep(15)
    print(" TIMEOUT")
    return False

def wait_for_jenkins(timeout_seconds: int = 600) -> bool:
    """Polls Jenkins until the latest build is no longer building."""
    token    = get_token("JENKINS_TOKEN")
    auth     = (JENKINS_USER, token)
    job_base = f"{JENKINS_URL}/job/{JENKINS_JOB}"
    print("  Waiting for Jenkins build to complete", end="", flush=True)
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            resp = requests.get(f"{job_base}/lastBuild/api/json", auth=auth, timeout=10)
            resp.raise_for_status()
            build = resp.json()
            if not build.get("building", False):
                print(f" done ({build.get('result', 'UNKNOWN')})")
                return True
        except requests.RequestException:
            pass
        print(".", end="", flush=True)
        time.sleep(15)
    print(" TIMEOUT")
    return False

# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

COLLECTORS = {
    "github":  (wait_for_github,  collect_github),
    "gitlab":  (wait_for_gitlab,  collect_gitlab),
    "jenkins": (wait_for_jenkins, collect_jenkins),
}

def main():
    parser = argparse.ArgumentParser(description="Collect CI/CD experiment results")
    parser.add_argument("experiment_id",  help="e.g. E1, E2, E5b, E13")
    parser.add_argument("run_number",     type=int, help="Run number (1-10)")
    parser.add_argument("platform",       help="github | gitlab | jenkins | all")
    parser.add_argument("injection_type", help="e.g. compilation, test, flaky")
    parser.add_argument("--no-wait",      action="store_true",
                        help="Skip waiting for pipeline — collect immediately")
    args = parser.parse_args()

    platforms = list(COLLECTORS.keys()) if args.platform == "all" else [args.platform]

    for platform in platforms:
        if platform not in COLLECTORS:
            print(f"ERROR: Unknown platform '{platform}'. Use: github | gitlab | jenkins | all")
            sys.exit(1)

        wait_fn, collect_fn = COLLECTORS[platform]

        if not args.no_wait:
            ok = wait_fn()
            if not ok:
                print(f"WARNING: Pipeline did not complete within timeout for {platform}. Collecting anyway.")

        result = collect_fn(args.experiment_id, args.run_number, args.injection_type)
        if result:
            out, metadata = result
            append_to_csv(metadata, out)

    print("\nCollection complete.")
    print(f"Results saved to: {OUTPUT_ROOT.resolve()}")

if __name__ == "__main__":
    main()
