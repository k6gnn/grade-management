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
        "build":                           "build_status",
        "stage 1":                         "build_status",
        "stage 1 - build":                 "build_status",
        "test":                            "test_status",
        "stage 2":                         "test_status",
        "stage 2 - test":                  "test_status",
        "package":                         "package_status",
        "stage 3":                         "package_status",
        "stage 3 - package":               "package_status",
        "pre-pipeline":                    "config_status",
        "m8 - configuration validation":   "config_status",
        "m9 - environment verification":   "config_status",
        "configuration validation":        "config_status",
        "environment verification":        "config_status",
        "m8":                              "config_status",
        "m9":                              "config_status",
        "deploy":                          "deploy_status",
        "stage 4":                         "deploy_status",
        "stage 4 - deploy":                "deploy_status",
        "m14 - risk assessment":           "m14_status",
    }

    for stage in data.get("stages", []):
        name   = stage.get("name", "").lower().strip()
        status = stage.get("status", "UNKNOWN")

        # Normalize Jenkins status to lowercase
        normalized = status.lower()
        if normalized == "success":
            normalized = "success"
        elif normalized in ("failed", "failure"):
            normalized = "failed"
        elif normalized == "unstable":
            normalized = "failed"
        elif normalized == "aborted":
            normalized = "canceled"
        elif normalized in ("not_executed", "skipped"):
            normalized = "skipped"

        key = stage_mapping.get(name)
        if key:
            # For config_status: only update if not already set to failed
            # (nested stages — take the worst status)
            existing = job_statuses.get(key)
            if existing != "failed":
                job_statuses[key] = normalized
            print(f"  Stage '{name}' -> {key} = {normalized}")
        else:
            print(f"  Stage '{name}' -> no mapping (status: {normalized})")

        # Also check nested stages (stageFlowNodes)
        for nested in stage.get("stageFlowNodes", []):
            nested_name = nested.get("name", "").lower().strip()
            nested_status = nested.get("status", "UNKNOWN").lower()
            if nested_status in ("failed", "failure"):
                nested_status = "failed"
            nested_key = stage_mapping.get(nested_name)
            if nested_key and nested_status == "failed":
                job_statuses[nested_key] = "failed"
                print(f"  Nested stage '{nested_name}' -> {nested_key} = failed")

    print("Fetched stage statuses:", job_statuses)

except Exception as e:
    print(f"WARNING: Could not fetch stage statuses via Jenkins API: {e}")
    # Fall back to overall build result
    if overall_result in ("FAILURE", "UNSTABLE"):
        job_statuses["build_status"] = "failed"
    print(f"Fallback: using overall_result={overall_result}")

# Post-processing: always runs regardless of whether the API call succeeded.
# If the overall pipeline failed but config_validation.log records a FAILURE,
# it means Pre-Pipeline (M8/M9) was the culprit, not the Build stage.
# Jenkins wfapi sometimes reports nested stage failures under the parent, and
# in the fallback path the API is unavailable so config_status is never set.
if (job_statuses.get("build_status") == "failed"
        and "config_status" not in job_statuses
        and os.path.exists("config_validation.log")):
    with open("config_validation.log") as f:
        config_log = f.read()
    if "FAILURE" in config_log or "failed" in config_log.lower():
        print("  Post-processing: config_validation.log contains failures")
        print("  Reclassifying build_status=failed -> config_status=failed")
        job_statuses["config_status"] = "failed"
        job_statuses["build_status"]  = "skipped"

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
