#!/usr/bin/env python3
"""
fetch_gitlab_status.py
Fetches real job statuses from the GitLab API for the current pipeline
and writes pipeline_status.json for M13 to consume.

Called by .gitlab-ci.yml anomaly-detection job.
Uses CI_JOB_TOKEN (automatically available in all GitLab CI jobs).
"""
import json
import os
import sys
try:
    import requests
except ImportError:
    print("ERROR: requests not installed")
    sys.exit(1)

pipeline_id = os.getenv("CI_PIPELINE_ID")
project_id  = os.getenv("CI_PROJECT_ID")
token       = os.getenv("CI_JOB_TOKEN")

job_statuses = {}
try:
    url  = f"https://gitlab.com/api/v4/projects/{project_id}/pipelines/{pipeline_id}/jobs"
    resp = requests.get(url, headers={"JOB-TOKEN": token}, params={"per_page": 50})
    resp.raise_for_status()
    mapping = {
        "build":                    "build_status",
        "test":                     "test_status",
        "package":                  "package_status",
        "configuration-validation": "config_status",
        "deploy":                   "deploy_status",
    }
    for job in resp.json():
        key = mapping.get(job.get("name", ""))
        if key:
            job_statuses[key] = job.get("status", "unknown")
    print("Fetched job statuses:", job_statuses)
except Exception as e:
    print(f"WARNING: Could not fetch job statuses via API: {e}")

pipeline_failed = any(v in ("failed", "canceled") for v in job_statuses.values())

d = {
    "platform":        "gitlab",
    "project_path":    os.getenv("CI_PROJECT_PATH"),
    "project_id":      project_id,
    "pipeline_id":     pipeline_id,
    "pipeline_iid":    os.getenv("CI_PIPELINE_IID"),
    "pipeline_url":    os.getenv("CI_PIPELINE_URL"),
    "commit":          os.getenv("CI_COMMIT_SHA"),
    "branch":          os.getenv("CI_COMMIT_REF_NAME"),
    "source":          os.getenv("CI_PIPELINE_SOURCE"),
    "pipeline_failed": pipeline_failed,
}
d.update(job_statuses)

with open("pipeline_status.json", "w") as f:
    json.dump(d, f, indent=2)

print("pipeline_status.json written:")
print(json.dumps(d, indent=2))
