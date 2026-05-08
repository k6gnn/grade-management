#!/usr/bin/env python3
"""
fetch_jenkins_status.py
Fetches real stage statuses from the Jenkins API for the current build
and writes pipeline_status.json for M13 to consume.

Called by Jenkinsfile post { always { } } block.
Uses JENKINS_URL, JOB_NAME, BUILD_NUMBER env vars set by Jenkins automatically.
Requires JENKINS_API_TOKEN env var (set as Jenkins credential).
"""
import json
import os
import sys
try:
    import requests
except ImportError:
    print("ERROR: requests not installed")
    sys.exit(1)

jenkins_url  = os.getenv("JENKINS_URL", "http://localhost:8081").rstrip("/")
job_name     = os.getenv("JOB_NAME", "Thesis-Project")
build_number = os.getenv("BUILD_NUMBER", "")
api_token    = os.getenv("JENKINS_API_TOKEN", "")
jenkins_user = os.getenv("JENKINS_USER", "k6gnn")
overall_result = os.getenv("BUILD_RESULT", "UNKNOWN")

job_statuses = {}
try:
    # Use Workflow API to get individual stage results
    url  = f"{jenkins_url}/job/{job_name}/{build_number}/wfapi/describe"
    auth = (jenkins_user, api_token) if api_token else None
    resp = requests.get(url, auth=auth, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    # Map stage names to status keys M13 understands
    stage_mapping = {
        "build":                  "build_status",
        "stage 1":                "build_status",
        "stage 1 - build":        "build_status",
        "test":                   "test_status",
        "stage 2":                "test_status",
        "stage 2 - test":         "test_status",
        "package":                "package_status",
        "stage 3":                "package_status",
        "stage 3 - package":      "package_status",
        "pre-pipeline":           "config_status",
        "m8 - configuration validation": "config_status",
        "m9 - environment verification": "config_status",
        "deploy":                 "deploy_status",
        "stage 4":                "deploy_status",
        "stage 4 - deploy":       "deploy_status",
    }

    for stage in data.get("stages", []):
        name   = stage.get("name", "").lower().strip()
        status = stage.get("status", "UNKNOWN")

        # Normalize Jenkins status to lowercase
        # Jenkins uses: SUCCESS, FAILED, UNSTABLE, ABORTED, NOT_EXECUTED
        normalized = status.lower()
        if normalized == "success":
            normalized = "success"
        elif normalized in ("failed", "failure"):
            normalized = "failed"
        elif normalized == "unstable":
            normalized = "failed"  # treat unstable as failed for M13
        elif normalized == "aborted":
            normalized = "canceled"
        elif normalized == "not_executed":
            normalized = "skipped"

        key = stage_mapping.get(name)
        if key:
            job_statuses[key] = normalized
            print(f"  Stage '{name}' -> {key} = {normalized}")
        else:
            print(f"  Stage '{name}' -> no mapping (status: {normalized})")

    print("Fetched stage statuses:", job_statuses)

except Exception as e:
    print(f"WARNING: Could not fetch stage statuses via Jenkins API: {e}")
    # Fall back to overall build result
    if overall_result in ("FAILURE", "UNSTABLE"):
        job_statuses["build_status"] = "failed"
    print(f"Fallback: using overall_result={overall_result}")

# Determine pipeline_failed
pipeline_failed = (
    any(v in ("failed", "canceled") for v in job_statuses.values())
    or overall_result in ("FAILURE", "UNSTABLE", "ABORTED")
)

d = {
    "platform":        "jenkins",
    "job_name":        job_name,
    "build_number":    build_number,
    "build_url":       f"{jenkins_url}/job/{job_name}/{build_number}/",
    "commit":          os.getenv("GIT_COMMIT", "unknown"),
    "branch":          os.getenv("GIT_BRANCH", "unknown"),
    "overall_result":  overall_result,
    "pipeline_failed": pipeline_failed,
}
d.update(job_statuses)

with open("pipeline_status.json", "w") as f:
    json.dump(d, f, indent=2)

print("pipeline_status.json written:")
print(json.dumps(d, indent=2))
