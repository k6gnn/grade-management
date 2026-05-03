#!/usr/bin/env python3
"""
m14_predict.py — M14 Proactive Failure Risk Predictor (Multiplatform Inference)
================================================================================
Runs before CI/CD pipeline stages to assess failure risk based on:
  - Historical run outcomes for the current repository/project/job
  - Current commit change metadata when available
  - Event type, branch, workflow name, and temporal/context features

Supported platforms:
  - github   : GitHub Actions API
  - gitlab   : GitLab Pipelines + Repository API
  - jenkins  : Jenkins API if credentials are available, otherwise local-history fallback
  - offline  : local-history/change-file fallback only

Typical usage:
  GitHub Actions:
    python scripts/m14_predict.py \
      --platform github \
      --repository "owner/repo" \
      --commit "$GITHUB_SHA" \
      --branch "$GITHUB_REF_NAME" \
      --event "$GITHUB_EVENT_NAME" \
      --model models/m14_model.pkl \
      --config models/m14_config.pkl \
      --output m14_risk_report.json

  GitLab CI:
    python scripts/m14_predict.py \
      --platform gitlab \
      --repository "$CI_PROJECT_PATH" \
      --commit "$CI_COMMIT_SHA" \
      --branch "$CI_COMMIT_REF_NAME" \
      --event "$CI_PIPELINE_SOURCE" \
      --model models/m14_model.pkl \
      --config models/m14_config.pkl \
      --output m14_risk_report.json

  Jenkins:
    python3 scripts/m14_predict.py \
      --platform jenkins \
      --repository "$JOB_NAME" \
      --commit "$GIT_COMMIT" \
      --branch "${BRANCH_NAME:-main}" \
      --event "jenkins-build" \
      --model models/m14_model.pkl \
      --config models/m14_config.pkl \
      --history-file .ci/m14_history.jsonl \
      --output m14_risk_report.json

Environment variables:
  Common:
    M14_THRESHOLD       Optional override. If absent, config threshold is used.
    M14_THRESHOLD_MODE  balanced | high_recall | low_noise. Default: config/default/balanced.
    M14_MODE            warning_only | block. Default: warning_only.

  GitHub:
    GITHUB_TOKEN        Token for GitHub REST API.
    GITHUB_WORKFLOW     Workflow name.

  GitLab:
    CI_API_V4_URL       Usually https://gitlab.com/api/v4, auto-set in GitLab CI.
    CI_PROJECT_ID       Preferred project identifier for GitLab API.
    CI_PIPELINE_ID      Current pipeline ID, excluded if seen in history.
    CI_JOB_TOKEN        Works for current project in many GitLab configurations.
    GITLAB_TOKEN        Optional Personal/Project Access Token with read_api.

  Jenkins:
    JENKINS_URL / JOB_URL        Jenkins base/job URL.
    JENKINS_USER                 Optional user for Basic auth.
    JENKINS_API_TOKEN            Optional API token for Basic auth.
    JOB_NAME                     Job name.

Notes:
  * This script is intentionally warning-only by default. It writes a JSON report
    and lets the pipeline continue so experiments can still collect M13/MTTR data.
  * Jenkins support is best-effort because Jenkins installations differ. If the
    Jenkins API is unavailable, provide --history-file and optionally --change-file.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import numpy as np

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("m14_predict")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DECAY_ALPHA = 0.75
WINDOW_SIZE = 10
FALLBACK_RISK_SCORE = 0.0
HISTORY_FETCH_LIMIT = 60
LARGE_CHANGE_THRESHOLD = 500
_MAIN_BRANCHES = frozenset(("main", "master", "trunk", "develop"))

# File classification patterns.
_TEST_INDICATORS = (
    "test/", "/tests/", "spec/", ".test.", ".spec.", "test.", "spec.",
    "_test.py", "_spec.rb", "tests.py", "src/test/",
)
_BUILD_INDICATORS = (
    "pom.xml", "build.gradle", "build.gradle.kts", "gradlew", "makefile",
    "cmakelists.txt", "build.sbt", "build", "workspace", ".bazel", "meson.build",
)
_CI_INDICATORS = (
    ".github/workflows/", ".travis.yml", ".circleci/", "jenkinsfile",
    ".gitlab-ci.yml", "azure-pipelines.yml", ".drone.yml",
)
_DEP_INDICATORS = (
    "requirements.txt", "pipfile", "pipfile.lock", "poetry.lock", "pyproject.toml",
    "setup.cfg", "package.json", "yarn.lock", "package-lock.json", "pnpm-lock.yaml",
    "gemfile", "gemfile.lock", "go.mod", "go.sum", "cargo.toml", "cargo.lock",
    "composer.json", "composer.lock", ".csproj", "build.gradle", "build.gradle.kts",
)
_DOC_EXTENSIONS = (".md", ".rst", ".txt", ".pdf", ".docx", ".adoc", ".wiki")
_SRC_EXTENSIONS = (
    ".java", ".kt", ".py", ".js", ".ts", ".go", ".rs", ".cpp", ".c",
    ".h", ".cs", ".rb", ".php", ".swift", ".scala", ".clj", ".ex",
    ".elixir", ".r", ".lua", ".dart", ".zig",
)

# ---------------------------------------------------------------------------
# Generic HTTP helpers
# ---------------------------------------------------------------------------

def _http_json(url: str, headers: Optional[dict[str, str]] = None, timeout: int = 15) -> Any:
    req = Request(url, headers=headers or {})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, (int, float)):
        # Jenkins timestamp in milliseconds or seconds.
        if value > 10_000_000_000:
            value = value / 1000.0
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None

# ---------------------------------------------------------------------------
# GitHub adapter
# ---------------------------------------------------------------------------

def _gh_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def fetch_github_completed_runs(repo: str, token: str, limit: int = HISTORY_FETCH_LIMIT) -> list[dict]:
    runs: list[dict] = []
    page = 1
    while len(runs) < limit:
        url = f"https://api.github.com/repos/{repo}/actions/runs?status=completed&per_page=100&page={page}"
        try:
            data = _http_json(url, _gh_headers(token), timeout=15)
        except Exception as exc:
            log.warning("GitHub: could not fetch runs page %d: %s", page, exc)
            break
        batch = data.get("workflow_runs", [])
        if not batch:
            break
        runs.extend(batch)
        page += 1
        if len(batch) < 100:
            break
    return runs[:limit]


def fetch_github_commit(repo: str, sha: str, token: str) -> Optional[dict]:
    try:
        return _http_json(f"https://api.github.com/repos/{repo}/commits/{sha}", _gh_headers(token), timeout=20)
    except Exception as exc:
        log.warning("GitHub: could not fetch commit %s: %s", sha[:12], exc)
        return None


def parse_github_run(run: dict) -> dict:
    conclusion = (run.get("conclusion") or "").lower()
    return {
        "build_failed": int(conclusion in ("failure", "timed_out", "startup_failure")),
        "created_at": _parse_dt(run.get("created_at")),
        "workflow_name": (run.get("name") or run.get("workflow_name") or "").lower(),
        "head_sha": run.get("head_sha", ""),
        "event": run.get("event", ""),
        "head_branch": run.get("head_branch", ""),
        "run_id": run.get("id", ""),
    }

# ---------------------------------------------------------------------------
# GitLab adapter
# ---------------------------------------------------------------------------

def _gitlab_api_base() -> str:
    return os.environ.get("CI_API_V4_URL", "https://gitlab.com/api/v4").rstrip("/")


def _gitlab_project_id(repository: str) -> str:
    # Prefer CI_PROJECT_ID because it avoids URL encoding issues.
    project = os.environ.get("CI_PROJECT_ID") or repository
    return str(project) if str(project).isdigit() else quote(str(project), safe="")


def _gitlab_headers() -> dict[str, str]:
    token = os.environ.get("GITLAB_TOKEN") or os.environ.get("GITLAB_PRIVATE_TOKEN")
    if token:
        return {"PRIVATE-TOKEN": token}
    job_token = os.environ.get("CI_JOB_TOKEN")
    if job_token:
        return {"JOB-TOKEN": job_token}
    return {}


def fetch_gitlab_completed_runs(repository: str, limit: int = HISTORY_FETCH_LIMIT) -> list[dict]:
    base = _gitlab_api_base()
    project = _gitlab_project_id(repository)
    headers = _gitlab_headers()
    if not headers:
        log.warning("GitLab: no GITLAB_TOKEN/GITLAB_PRIVATE_TOKEN/CI_JOB_TOKEN found")
        return []

    current_pipeline = str(os.environ.get("CI_PIPELINE_ID", ""))
    runs: list[dict] = []
    page = 1
    while len(runs) < limit:
        query = urlencode({"scope": "finished", "per_page": 100, "page": page})
        url = f"{base}/projects/{project}/pipelines?{query}"
        try:
            batch = _http_json(url, headers, timeout=20)
        except Exception as exc:
            log.warning("GitLab: could not fetch pipelines page %d: %s", page, exc)
            break
        if not batch:
            break
        for p in batch:
            if current_pipeline and str(p.get("id")) == current_pipeline:
                continue
            runs.append(p)
        page += 1
        if len(batch) < 100:
            break
    return runs[:limit]


def fetch_gitlab_commit(repository: str, sha: str) -> Optional[dict]:
    base = _gitlab_api_base()
    project = _gitlab_project_id(repository)
    headers = _gitlab_headers()
    if not headers:
        return None
    try:
        commit = _http_json(f"{base}/projects/{project}/repository/commits/{quote(sha, safe='')}?with_stats=true", headers, timeout=20)
    except Exception as exc:
        log.warning("GitLab: could not fetch commit %s: %s", sha[:12], exc)
        return None
    try:
        diff = _http_json(f"{base}/projects/{project}/repository/commits/{quote(sha, safe='')}/diff?per_page=100", headers, timeout=20)
    except Exception as exc:
        log.warning("GitLab: could not fetch commit diff %s: %s", sha[:12], exc)
        diff = []
    return {"gitlab_commit": commit, "gitlab_diff": diff}


def parse_gitlab_run(p: dict) -> dict:
    status = (p.get("status") or "").lower()
    return {
        "build_failed": int(status in ("failed", "canceled")),
        "created_at": _parse_dt(p.get("created_at") or p.get("updated_at")),
        "workflow_name": (p.get("name") or p.get("source") or "gitlab-pipeline").lower(),
        "head_sha": p.get("sha", ""),
        "event": p.get("source", ""),
        "head_branch": p.get("ref", ""),
        "run_id": p.get("id", ""),
    }

# ---------------------------------------------------------------------------
# Jenkins adapter / local fallback
# ---------------------------------------------------------------------------

def _jenkins_headers() -> dict[str, str]:
    user = os.environ.get("JENKINS_USER") or os.environ.get("JENKINS_USERNAME")
    token = os.environ.get("JENKINS_API_TOKEN") or os.environ.get("JENKINS_TOKEN")
    if user and token:
        auth = base64.b64encode(f"{user}:{token}".encode()).decode()
        return {"Authorization": f"Basic {auth}"}
    return {}


def fetch_jenkins_completed_runs(limit: int = HISTORY_FETCH_LIMIT) -> list[dict]:
    # JOB_URL is the most reliable URL inside a Jenkins job. It already points to the job.
    job_url = (os.environ.get("JOB_URL") or "").rstrip("/")
    if not job_url:
        base = (os.environ.get("JENKINS_URL") or "").rstrip("/")
        job_name = os.environ.get("JOB_NAME", "")
        if base and job_name:
            # Best effort for simple non-folder jobs. Folder jobs are handled better by JOB_URL.
            job_path = "/".join(f"job/{quote(part, safe='')}" for part in job_name.split("/"))
            job_url = f"{base}/{job_path}"
    if not job_url:
        log.warning("Jenkins: JOB_URL/JENKINS_URL not available; using local history fallback only")
        return []

    tree = "builds[number,result,timestamp,duration,actions[lastBuiltRevision[SHA1]],changeSet[items[commitId]],url]{0,%d}" % limit
    url = f"{job_url}/api/json?tree={quote(tree, safe='[],:') }"
    try:
        data = _http_json(url, _jenkins_headers(), timeout=20)
    except Exception as exc:
        log.warning("Jenkins: could not fetch build history: %s", exc)
        return []
    return data.get("builds", [])[:limit]


def _jenkins_sha(build: dict) -> str:
    for action in build.get("actions", []) or []:
        rev = action.get("lastBuiltRevision") if isinstance(action, dict) else None
        if isinstance(rev, dict) and rev.get("SHA1"):
            return str(rev.get("SHA1"))
    for item in (build.get("changeSet") or {}).get("items", []) or []:
        if item.get("commitId"):
            return str(item.get("commitId"))
    return ""


def parse_jenkins_run(b: dict) -> dict:
    result = (b.get("result") or "").upper()
    # UNSTABLE often means test failures, so treat as failure for risk prediction.
    return {
        "build_failed": int(result in ("FAILURE", "UNSTABLE", "ABORTED", "NOT_BUILT")),
        "created_at": _parse_dt(b.get("timestamp")),
        "workflow_name": (os.environ.get("JOB_NAME") or "jenkins-job").lower(),
        "head_sha": _jenkins_sha(b),
        "event": "jenkins-build",
        "head_branch": os.environ.get("BRANCH_NAME", ""),
        "run_id": b.get("number", ""),
    }


def load_local_history(path: Optional[str]) -> list[dict]:
    if not path:
        path = os.environ.get("M14_HISTORY_FILE", "")
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        log.warning("Local history file not found: %s", p)
        return []

    rows: list[dict] = []
    try:
        if p.suffix.lower() == ".csv":
            with p.open("r", encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f):
                    rows.append(_normalise_local_history_row(row))
        else:
            # Supports JSON list or JSONL.
            text = p.read_text(encoding="utf-8").strip()
            if not text:
                return []
            if text.startswith("["):
                for row in json.loads(text):
                    rows.append(_normalise_local_history_row(row))
            else:
                for line in text.splitlines():
                    if line.strip():
                        rows.append(_normalise_local_history_row(json.loads(line)))
    except Exception as exc:
        log.warning("Could not parse local history file %s: %s", p, exc)
        return []

    rows = [r for r in rows if r.get("created_at") is not None]
    rows.sort(key=lambda r: r["created_at"])
    return rows[-HISTORY_FETCH_LIMIT:]


def _normalise_local_history_row(row: dict) -> dict:
    status = str(row.get("status", row.get("conclusion", row.get("result", "")))).lower()
    if "build_failed" in row:
        failed = int(float(row.get("build_failed") or 0))
    else:
        failed = int(status in ("failure", "failed", "timed_out", "startup_failure", "unstable", "aborted"))
    return {
        "build_failed": failed,
        "created_at": _parse_dt(row.get("created_at") or row.get("timestamp") or row.get("time")),
        "workflow_name": str(row.get("workflow_name") or row.get("workflow") or row.get("job_name") or "local").lower(),
        "head_sha": str(row.get("head_sha") or row.get("sha") or row.get("commit") or ""),
        "event": str(row.get("event") or row.get("source") or "local"),
        "head_branch": str(row.get("head_branch") or row.get("branch") or ""),
        "run_id": row.get("run_id") or row.get("id") or row.get("number") or "",
    }

# ---------------------------------------------------------------------------
# Change metadata helpers
# ---------------------------------------------------------------------------

def _is_test_file(path: str) -> bool:
    pl = path.lower()
    return any(ind in pl for ind in _TEST_INDICATORS)


def _is_build_file(path: str) -> bool:
    name = path.split("/")[-1].lower()
    pl = path.lower()
    return any(ind.lower() in pl or name == ind.lower() for ind in _BUILD_INDICATORS)


def _is_ci_file(path: str) -> bool:
    pl = path.lower()
    return any(ind.lower() in pl for ind in _CI_INDICATORS)


def _is_dep_file(path: str) -> bool:
    name = path.split("/")[-1].lower()
    pl = path.lower()
    return any(ind.lower() in pl or name == ind.lower() for ind in _DEP_INDICATORS)


def _is_doc_file(path: str) -> bool:
    return any(path.lower().endswith(ext) for ext in _DOC_EXTENSIONS)


def _is_src_file(path: str) -> bool:
    if _is_test_file(path) or _is_build_file(path) or _is_ci_file(path) or _is_dep_file(path):
        return False
    return any(path.lower().endswith(ext) for ext in _SRC_EXTENSIONS)


def _default_change() -> dict[str, float]:
    return {
        "files_changed_count": 0.0,
        "lines_added": 0.0,
        "lines_deleted": 0.0,
        "src_files_changed": 0.0,
        "test_files_changed": 0.0,
        "build_files_changed": 0.0,
        "ci_config_changed": 0.0,
        "dependency_files_changed": 0.0,
        "docs_only_change": 0.0,
        "has_large_change": 0.0,
    }


def parse_change_metadata(commit_data: Optional[dict], platform: str = "github") -> dict[str, float]:
    if commit_data is None:
        return _default_change()
    if platform == "gitlab" and ("gitlab_commit" in commit_data or "gitlab_diff" in commit_data):
        commit = commit_data.get("gitlab_commit") or {}
        diff = commit_data.get("gitlab_diff") or []
        stats = commit.get("stats") or {}
        added = float(stats.get("additions", 0) or 0)
        deleted = float(stats.get("deletions", 0) or 0)
        files = []
        for d in diff:
            path = d.get("new_path") or d.get("old_path") or ""
            files.append({"filename": path})
        return _change_from_files(files, added, deleted)
    # GitHub-compatible shape.
    stats = commit_data.get("stats", {})
    files = commit_data.get("files", [])
    added = float(stats.get("additions", 0) or 0)
    deleted = float(stats.get("deletions", 0) or 0)
    return _change_from_files(files, added, deleted)


def _change_from_files(files: list[dict], added: float, deleted: float) -> dict[str, float]:
    total_churn = added + deleted
    paths = [str(f.get("filename") or f.get("new_path") or f.get("old_path") or "") for f in files]
    src_count = sum(1 for p in paths if _is_src_file(p))
    test_count = sum(1 for p in paths if _is_test_file(p))
    build_count = sum(1 for p in paths if _is_build_file(p))
    ci_count = sum(1 for p in paths if _is_ci_file(p))
    dep_count = sum(1 for p in paths if _is_dep_file(p))
    doc_count = sum(1 for p in paths if _is_doc_file(p))
    n_files = len(paths)
    docs_only = int(n_files > 0 and doc_count == n_files)
    return {
        "files_changed_count": float(n_files),
        "lines_added": float(added),
        "lines_deleted": float(deleted),
        "src_files_changed": float(src_count),
        "test_files_changed": float(test_count),
        "build_files_changed": float(build_count),
        "ci_config_changed": float(ci_count),
        "dependency_files_changed": float(dep_count),
        "docs_only_change": float(docs_only),
        "has_large_change": float(int(total_churn > LARGE_CHANGE_THRESHOLD)),
    }


def load_change_file(path: Optional[str]) -> Optional[dict[str, float]]:
    if not path:
        path = os.environ.get("M14_CHANGE_FILE", "")
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        log.warning("Change file not found: %s", p)
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Could not parse change file %s: %s", p, exc)
        return None
    out = _default_change()
    for k in out:
        if k in raw:
            out[k] = float(raw[k] or 0)
    return out

# ---------------------------------------------------------------------------
# Feature engineering — mirrors M14 v5 training script
# ---------------------------------------------------------------------------

def streak_at_end(arr: np.ndarray, value: int) -> int:
    if len(arr) == 0:
        return 0
    mask = arr[::-1] != value
    if not mask.any():
        return int(len(arr))
    return int(mask.argmax())


def smoothed_ratio(numerator: float, denominator: float, smoothing: float = 1.0) -> float:
    return float(numerator) / (float(denominator) + smoothing)


def exponential_decay_score(arr: np.ndarray, alpha: float = DECAY_ALPHA) -> float:
    n = len(arr)
    if n == 0:
        return 0.0
    weights = np.array([alpha ** (n - 1 - k) for k in range(n)], dtype=np.float64)
    return float(np.dot(weights, arr) / weights.sum())


def event_features(event: str, workflow_name: str) -> dict[str, int]:
    e = event.lower()
    w = workflow_name.lower()
    return {
        "event_push": int(e == "push"),
        "event_pull_request": int("pull_request" in e or "merge_request" in e),
        "event_schedule": int(e == "schedule" or e == "scheduled"),
        "event_workflow_dispatch": int(e in ("workflow_dispatch", "web", "manual")),
        "workflow_name_has_test": int(any(x in w for x in ("test", "pytest", "junit", "ci"))),
        "workflow_name_has_build": int(any(x in w for x in ("build", "compile", "package"))),
        "workflow_name_has_lint": int(any(x in w for x in ("lint", "style", "format", "checkstyle", "ruff", "flake"))),
        "workflow_name_has_release_or_deploy": int(any(x in w for x in ("release", "deploy", "publish", "wheel", "package"))),
    }


def build_feature_vector(
    history_runs: list[dict],
    change: dict[str, float],
    event: str,
    branch: str,
    workflow_name: str,
    commit_ts: Optional[datetime],
    prev_run_ts: Optional[datetime],
    feature_names: list[str],
    window_size: int = WINDOW_SIZE,
    decay_alpha: float = DECAY_ALPHA,
    all_history_runs: Optional[list[dict]] = None,
    historical_changes: Optional[list[dict[str, float]]] = None,
) -> np.ndarray:
    w = min(len(history_runs), window_size)
    recent = history_runs[-w:] if w else []
    outcomes = np.array([r.get("build_failed", 0) for r in recent], dtype=float)

    fr_n = float(outcomes.mean()) if w else 0.0
    fr_3 = float(outcomes[-3:].mean()) if w >= 3 else fr_n
    fr_5 = float(outcomes[-5:].mean()) if w >= 5 else fr_3
    all_outcomes = np.array([r.get("build_failed", 0) for r in (all_history_runs or history_runs)], dtype=float)
    fr_30 = float(all_outcomes[-30:].mean()) if len(all_outcomes) >= 3 else fr_n
    repo_base_fr = float(all_outcomes.mean()) if len(all_outcomes) else 0.0

    recency_score = exponential_decay_score(outcomes, alpha=decay_alpha)
    failure_accel = (fr_3 - fr_5) - (fr_5 - fr_n)
    consec_fail = streak_at_end(outcomes, 1)
    consec_succ = streak_at_end(outcomes, 0)
    last_failed = int(outcomes[-1]) if w else 0
    failed_2_ago = int(outcomes[-2]) if w >= 2 else 0
    failed_3_ago = int(outcomes[-3]) if w >= 3 else 0
    trend_fr = fr_3 - fr_n
    outcome_std = float(outcomes.std()) if w > 1 else 0.0
    all_success = int(outcomes.sum() == 0) if w else 0

    curr_files = change["files_changed_count"]
    curr_added = change["lines_added"]
    curr_deleted = change["lines_deleted"]
    curr_churn = curr_added + curr_deleted
    curr_src = change["src_files_changed"]
    curr_test = change["test_files_changed"]
    curr_build = change["build_files_changed"]
    curr_ci = change["ci_config_changed"]
    curr_deps = change["dependency_files_changed"]

    log_files = math.log1p(curr_files)
    log_added = math.log1p(curr_added)
    log_deleted = math.log1p(curr_deleted)
    log_churn = math.log1p(curr_churn)

    if historical_changes:
        prev_files_arr = np.array([c.get("files_changed_count", 0) for c in historical_changes], dtype=float)
        prev_churn_arr = np.array([c.get("lines_added", 0) + c.get("lines_deleted", 0) for c in historical_changes], dtype=float)
        prev_src_arr = np.array([c.get("src_files_changed", 0) for c in historical_changes], dtype=float)
        prev_test_arr = np.array([c.get("test_files_changed", 0) for c in historical_changes], dtype=float)
        files_vs_mean = smoothed_ratio(curr_files, prev_files_arr.mean())
        churn_vs_mean = smoothed_ratio(curr_churn, prev_churn_arr.mean())
        src_vs_mean = smoothed_ratio(curr_src, prev_src_arr.mean())
        test_vs_mean = smoothed_ratio(curr_test, prev_test_arr.mean())
    else:
        files_vs_mean = churn_vs_mean = src_vs_mean = test_vs_mean = 1.0

    wf_curr = workflow_name.lower()
    wf_names_window = [str(r.get("workflow_name", "")).lower() for r in recent]
    wf_mask = np.array([n == wf_curr for n in wf_names_window], dtype=bool)
    same_wf_fr = float(outcomes[wf_mask].mean()) if w and wf_mask.any() else fr_n
    n_wf_window = len(set(n for n in wf_names_window if n))

    ts = commit_ts or datetime.now(timezone.utc)
    dow = ts.weekday()
    hod = ts.hour
    prev_run_ts = prev_run_ts if prev_run_ts and prev_run_ts.tzinfo else (prev_run_ts.replace(tzinfo=timezone.utc) if prev_run_ts else None)
    delta_h = max(0.0, (ts - prev_run_ts).total_seconds() / 3600.0) if prev_run_ts else 24.0

    prev_churn_arr_for_spike = (
        np.array([c.get("lines_added", 0) + c.get("lines_deleted", 0) for c in historical_changes], dtype=float)
        if historical_changes else np.array([curr_churn], dtype=float)
    )
    churn_spike = int(curr_churn > 3.0 * (float(prev_churn_arr_for_spike.mean()) + 1.0))

    ev_feats = event_features(event, workflow_name)

    feat: dict[str, float] = {
        "failure_rate_last_N": fr_n,
        "failure_rate_last_3": fr_3,
        "consecutive_failures": float(consec_fail),
        "consecutive_successes": float(consec_succ),
        "last_build_failed": float(last_failed),
        "failed_2_ago": float(failed_2_ago),
        "failed_3_ago": float(failed_3_ago),
        "trend_failure_rate": trend_fr,
        "failure_rate_last_5": fr_5,
        "outcome_std_last_N": outcome_std,
        "all_success_in_window": float(all_success),
        "long_consecutive_successes": float(consec_succ),
        "failure_recency_score": recency_score,
        "repo_base_failure_rate": repo_base_fr,
        "failure_acceleration": failure_accel,
        "log_files_changed_count": log_files,
        "log_lines_added": log_added,
        "log_lines_deleted": log_deleted,
        "log_total_churn": log_churn,
        "src_files_changed": curr_src,
        "test_files_changed": curr_test,
        "build_files_changed": curr_build,
        "ci_config_changed": curr_ci,
        "dependency_files_changed": curr_deps,
        "docs_only_change": change["docs_only_change"],
        "has_large_change": change["has_large_change"],
        "change_touches_code": float(int(curr_src > 0)),
        "change_touches_tests": float(int(curr_test > 0)),
        "change_touches_build_or_deps": float(int(curr_build > 0 or curr_deps > 0)),
        "change_touches_ci": float(int(curr_ci > 0)),
        "files_changed_vs_recent_mean": files_vs_mean,
        "churn_vs_recent_mean": churn_vs_mean,
        "src_changed_vs_recent_mean": src_vs_mean,
        "test_changed_vs_recent_mean": test_vs_mean,
        "current_failure_rate_vs_repo_recent_30": fr_3 - fr_30,
        "repo_recent_failure_rate_30": fr_30,
        "day_of_week": float(dow),
        "is_weekend": float(int(dow >= 5)),
        "is_business_hours": float(int(0 <= dow <= 4 and 9 <= hod <= 17)),
        "log1p_hours_since_last_run": math.log1p(delta_h),
        "churn_spike": float(churn_spike),
        "pr_with_no_test_changes": float(int(("pull_request" in event.lower() or "merge_request" in event.lower()) and curr_test == 0)),
        "is_main_branch": float(int(branch.lower().strip() in _MAIN_BRANCHES)),
        "same_workflow_failure_rate": same_wf_fr,
        "churn_x_recent_failure": float(log_churn) * fr_3,
        "n_workflows_in_window": float(n_wf_window),
        **{k: float(v) for k, v in ev_feats.items()},
    }
    return np.array([feat.get(name, 0.0) for name in feature_names], dtype=np.float64)

# ---------------------------------------------------------------------------
# Config/model loading
# ---------------------------------------------------------------------------

def load_config(path: Path) -> dict:
    if not path.exists():
        log.warning("Config not found at %s — using defaults", path)
        return {}
    # Your config may be JSON despite .pkl extension; support both.
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    try:
        import joblib
        cfg = joblib.load(path)
        return cfg if isinstance(cfg, dict) else {}
    except Exception as exc:
        log.warning("Could not load config %s: %s", path, exc)
        return {}


def load_model(path: Path) -> Any:
    if not path.exists():
        log.warning("Model not found at %s", path)
        return None
    try:
        import joblib
        return joblib.load(path)
    except Exception as exc:
        log.warning("Could not load model %s: %s", path, exc)
        return None


def choose_threshold(config: dict, fallback: float) -> tuple[float, str, bool]:
    env_present = "M14_THRESHOLD" in os.environ
    mode = os.environ.get("M14_THRESHOLD_MODE") or config.get("default_threshold_mode") or "balanced"
    if env_present:
        return float(os.environ["M14_THRESHOLD"]), mode, True
    thresholds = config.get("thresholds") or {}
    if isinstance(thresholds, dict) and mode in thresholds:
        return float(thresholds[mode]), mode, False
    # Some configs may store flat keys.
    for key in (f"{mode}_threshold", mode):
        if key in config:
            return float(config[key]), mode, False
    return fallback, mode, False

# ---------------------------------------------------------------------------
# Platform orchestration
# ---------------------------------------------------------------------------

def fetch_platform_data(args: argparse.Namespace, window_size: int) -> tuple[list[dict], dict[str, float], Optional[datetime], Optional[datetime], list[dict[str, float]], str]:
    platform = args.platform.lower()
    raw_runs: list[dict] = []
    commit_data: Optional[dict] = None
    source = platform

    if platform == "github":
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
        if token:
            raw = fetch_github_completed_runs(args.repository, token, HISTORY_FETCH_LIMIT)
            raw_runs = [parse_github_run(r) for r in raw]
            commit_data = fetch_github_commit(args.repository, args.commit, token)
        else:
            log.warning("GitHub: GITHUB_TOKEN not set")
            source += ":no_token"

    elif platform == "gitlab":
        raw = fetch_gitlab_completed_runs(args.repository, HISTORY_FETCH_LIMIT)
        raw_runs = [parse_gitlab_run(r) for r in raw]
        commit_data = fetch_gitlab_commit(args.repository, args.commit)

    elif platform == "jenkins":
        raw = fetch_jenkins_completed_runs(HISTORY_FETCH_LIMIT)
        raw_runs = [parse_jenkins_run(r) for r in raw]
        local = load_local_history(args.history_file)
        if local:
            # Prefer local history if it is richer/longer than API history.
            raw_runs = local if len(local) >= len(raw_runs) else raw_runs
            source += "+local_history"
        change_file = load_change_file(args.change_file)
        if change_file is not None:
            change = change_file
        else:
            change = _default_change()
        raw_runs = [r for r in raw_runs if r.get("created_at") is not None]
        raw_runs.sort(key=lambda r: r["created_at"])
        prev_ts = raw_runs[-1]["created_at"] if raw_runs else None
        return raw_runs, change, datetime.now(timezone.utc), prev_ts, [], source

    elif platform == "offline":
        raw_runs = load_local_history(args.history_file)
        change = load_change_file(args.change_file) or _default_change()
        prev_ts = raw_runs[-1]["created_at"] if raw_runs else None
        return raw_runs, change, datetime.now(timezone.utc), prev_ts, [], "offline"

    else:
        raise SystemExit(f"Unsupported platform: {args.platform}")

    raw_runs = [r for r in raw_runs if r.get("created_at") is not None]
    raw_runs.sort(key=lambda r: r["created_at"])
    prev_ts = raw_runs[-1]["created_at"] if raw_runs else None
    change = parse_change_metadata(commit_data, platform=platform)

    commit_ts: Optional[datetime] = None
    if platform == "github" and commit_data:
        raw_ts = ((commit_data.get("commit") or {}).get("author") or {}).get("date")
        commit_ts = _parse_dt(raw_ts)
    elif platform == "gitlab" and commit_data:
        raw_ts = ((commit_data.get("gitlab_commit") or {}).get("authored_date") or (commit_data.get("gitlab_commit") or {}).get("created_at"))
        commit_ts = _parse_dt(raw_ts)
    if commit_ts is None:
        commit_ts = datetime.now(timezone.utc)

    # Historical change metadata for repo-local normalisation.
    hist_changes: list[dict[str, float]] = []
    history_window = raw_runs[-window_size:] if raw_runs else []
    hist_shas = [r.get("head_sha", "") for r in history_window if r.get("head_sha")]
    if platform == "github":
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
        for sha in hist_shas[:window_size]:
            hist_changes.append(parse_change_metadata(fetch_github_commit(args.repository, sha, token), "github"))
            time.sleep(0.05)
    elif platform == "gitlab":
        for sha in hist_shas[:window_size]:
            hist_changes.append(parse_change_metadata(fetch_gitlab_commit(args.repository, sha), "gitlab"))
            time.sleep(0.05)

    return raw_runs, change, commit_ts, prev_ts, hist_changes, source


def risk_level(score: float, threshold: float) -> str:
    if score >= threshold:
        return "high"
    if score >= 0.45:
        return "medium"
    if score >= 0.20:
        return "low"
    return "very_low"

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="M14 proactive failure risk predictor")
    parser.add_argument("--platform", default="github", choices=["github", "gitlab", "jenkins", "offline"])
    parser.add_argument("--repository", required=True, help="GitHub owner/repo, GitLab project path/id, or Jenkins job name")
    parser.add_argument("--commit", required=True, help="HEAD commit SHA")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--event", default="push")
    parser.add_argument("--model", default="models/m14_model.pkl")
    parser.add_argument("--config", default="models/m14_config.pkl")
    parser.add_argument("--output", default="m14_risk_report.json")
    parser.add_argument("--history-file", default="", help="Optional JSON/JSONL/CSV history fallback")
    parser.add_argument("--change-file", default="", help="Optional JSON change metadata fallback")
    args = parser.parse_args()

    mode = os.environ.get("M14_MODE", "warning_only")
    workflow_name = (
        os.environ.get("GITHUB_WORKFLOW")
        or os.environ.get("CI_JOB_STAGE")
        or os.environ.get("JOB_NAME")
        or args.event
    )

    log.info("M14 Proactive Failure Risk Assessment")
    log.info("  Platform   : %s", args.platform)
    log.info("  Repository : %s", args.repository)
    log.info("  Commit     : %s", args.commit[:12])
    log.info("  Branch     : %s", args.branch)
    log.info("  Event      : %s", args.event)
    log.info("  Mode       : %s", mode)

    config = load_config(Path(args.config))
    feature_names: list[str] = list(config.get("feature_names", []))
    window_size = int(config.get("window_size", WINDOW_SIZE))
    decay_alpha = float(config.get("decay_alpha", DECAY_ALPHA))
    threshold, threshold_mode, threshold_overridden = choose_threshold(config, fallback=0.725)
    log.info("  Threshold  : %.3f (%s%s)", threshold, threshold_mode, ", env override" if threshold_overridden else "")

    model = load_model(Path(args.model))

    raw_runs, change, commit_ts, prev_run_ts, hist_changes, data_source = fetch_platform_data(args, window_size)
    log.info("Fetched/loaded %d completed historical runs from %s", len(raw_runs), data_source)
    log.info(
        "  files_changed=%d  churn=%d  src=%d  test=%d  ci=%d  deps=%d",
        int(change["files_changed_count"]),
        int(change["lines_added"] + change["lines_deleted"]),
        int(change["src_files_changed"]),
        int(change["test_files_changed"]),
        int(change["ci_config_changed"]),
        int(change["dependency_files_changed"]),
    )

    if not feature_names:
        log.warning("feature_names missing in config — cannot build feature vector")
        risk_score = FALLBACK_RISK_SCORE
        warning = False
    elif model is None:
        log.warning("Model unavailable — cannot predict")
        risk_score = FALLBACK_RISK_SCORE
        warning = False
    else:
        history_window = raw_runs[-window_size:] if raw_runs else []
        feat_vec = build_feature_vector(
            history_runs=history_window,
            change=change,
            event=args.event,
            branch=args.branch,
            workflow_name=workflow_name,
            commit_ts=commit_ts,
            prev_run_ts=prev_run_ts,
            feature_names=feature_names,
            window_size=window_size,
            decay_alpha=decay_alpha,
            all_history_runs=raw_runs or None,
            historical_changes=hist_changes,
        )
        X = feat_vec.reshape(1, -1)
        if hasattr(model, "predict_proba"):
            risk_score = float(model.predict_proba(X)[0, 1])
        elif hasattr(model, "decision_function"):
            score = float(model.decision_function(X)[0])
            risk_score = 1.0 / (1.0 + math.exp(-score))
        else:
            risk_score = float(model.predict(X)[0])
        warning = risk_score >= threshold
        log.info("Risk score: %.4f  threshold=%.3f  warning=%s", risk_score, threshold, warning)

    history_window = raw_runs[-window_size:] if raw_runs else []
    fr_window = float(np.mean([r.get("build_failed", 0) for r in history_window])) if history_window else 0.0
    report: dict[str, Any] = {
        "mechanism": "M14",
        "platform": args.platform,
        "warning": bool(warning),
        "risk_score": round(float(risk_score), 6),
        "risk_level": risk_level(float(risk_score), threshold),
        "threshold": threshold,
        "threshold_mode": threshold_mode,
        "threshold_env_override": threshold_overridden,
        "mode": mode,
        "model_name": config.get("model_name", "unavailable"),
        "features_used": len(feature_names),
        "history_window_size": len(history_window),
        "history_failure_rate": round(fr_window, 4),
        "historical_runs_available": len(raw_runs),
        "data_source": data_source,
        "repository": args.repository,
        "commit": args.commit,
        "branch": args.branch,
        "event": args.event,
        "workflow": workflow_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": (
            f"HIGH RISK: Predicted failure probability {risk_score:.1%} exceeds threshold {threshold:.1%}. "
            f"Pipeline continues in warning-only mode."
            if warning else
            f"Risk score {risk_score:.1%} is below threshold {threshold:.1%}. Pipeline is expected to succeed."
        ),
    }

    output_path = Path(args.output)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    log.info("Report written to %s", output_path)

    if warning:
        log.warning("⚠️  HIGH RISK: score=%.3f ≥ threshold=%.3f", risk_score, threshold)
        if mode == "block":
            raise SystemExit(2)
        log.warning("   Pipeline continues because M14_MODE=%s", mode)
    else:
        log.info("✓ Risk score %.3f is below threshold %.3f", risk_score, threshold)


if __name__ == "__main__":
    main()
