#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# run_experiment.sh
# Automates a single experiment run: inject → commit → push → collect → restore
#
# Usage:
#   ./run_experiment.sh <experiment_id> <run_number> <injection_type> <platform>
#
# Examples:
#   ./run_experiment.sh E1 1 compilation github
#   ./run_experiment.sh E1 1 compilation gitlab
#   ./run_experiment.sh E1 1 compilation jenkins
#   ./run_experiment.sh E5b 1 oom github
#   ./run_experiment.sh E6 1 compilation_configuration github
#
# Platform push behaviour:
#   github  — pushes to origin (GitHub)
#   gitlab  — pushes to gitlab remote
#   jenkins — pushes to origin (Jenkins polls GitHub), then triggers Jenkins build
#
# Prerequisites:
#   - git remotes: origin (GitHub), gitlab (GitLab)
#   - Environment variables: GITHUB_TOKEN, GITLAB_TOKEN, JENKINS_TOKEN
#   - Python 3 with requests installed: pip install requests
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

EXPERIMENT_ID="${1:-}"
RUN_NUMBER="${2:-}"
INJECTION_TYPE="${3:-}"
PLATFORM="${4:-}"

# ─── Validate arguments ───────────────────────────────────────────────────────
if [[ -z "$EXPERIMENT_ID" || -z "$RUN_NUMBER" || -z "$INJECTION_TYPE" || -z "$PLATFORM" ]]; then
    echo "Usage: ./run_experiment.sh <experiment_id> <run_number> <injection_type> <platform>"
    echo ""
    echo "Examples:"
    echo "  ./run_experiment.sh E1 1 compilation github"
    echo "  ./run_experiment.sh E5b 1 oom github"
    echo "  ./run_experiment.sh E6 1 compilation_configuration github"
    exit 1
fi

PYTHON="${PYTHON:-python3}"
INJECT_SCRIPT="scripts/inject_failure.py"
COLLECT_SCRIPT="scripts/collect_results.py"
JENKINS_URL="http://localhost:8081"
JENKINS_USER="k6gnn"
JENKINS_JOB="grade-management"

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  EXPERIMENT: ${EXPERIMENT_ID}  RUN: ${RUN_NUMBER}  PLATFORM: ${PLATFORM}"
echo "  INJECTION:  ${INJECTION_TYPE}"
echo "════════════════════════════════════════════════════════════"

# ─── Step 1: Ensure clean baseline ────────────────────────────────────────────
echo ""
echo "[1/6] Ensuring clean baseline..."
$PYTHON "$INJECT_SCRIPT" restore 2>/dev/null || true

if [[ -n "$(git status --porcelain)" ]]; then
    echo "  WARNING: Working tree is not clean after restore. Check manually."
    git status --short
fi
echo "  Baseline clean."

# ─── Step 2: Inject failure ───────────────────────────────────────────────────
echo ""
echo "[2/6] Injecting failure: ${INJECTION_TYPE}..."
$PYTHON "$INJECT_SCRIPT" "$INJECTION_TYPE"

# ─── Step 3: Commit and push ──────────────────────────────────────────────────
echo ""
echo "[3/6] Committing and pushing to ${PLATFORM}..."

COMMIT_MSG="experiment: ${EXPERIMENT_ID} run ${RUN_NUMBER} - ${INJECTION_TYPE} - ${PLATFORM}"
git add -A
git commit -m "$COMMIT_MSG"

case "$PLATFORM" in
    github)
        git push origin main
        echo "  Pushed to GitHub (origin/main)"
        ;;
    gitlab)
        git push gitlab main
        echo "  Pushed to GitLab (gitlab/main)"
        ;;
    jenkins)
        # Jenkins polls GitHub — push to origin, then trigger build via API
        git push origin main
        echo "  Pushed to GitHub for Jenkins polling"
        echo "  Triggering Jenkins build..."
        curl -s -X POST \
            "${JENKINS_URL}/job/${JENKINS_JOB}/build" \
            --user "${JENKINS_USER}:${JENKINS_TOKEN}" \
            -o /dev/null \
            -w "  Jenkins trigger HTTP status: %{http_code}\n"
        ;;
    *)
        echo "ERROR: Unknown platform '${PLATFORM}'. Use: github | gitlab | jenkins"
        exit 1
        ;;
esac

# ─── Step 4: Collect results (waits for pipeline completion automatically) ────
echo ""
echo "[4/6] Waiting for pipeline and collecting results..."
$PYTHON "$COLLECT_SCRIPT" "$EXPERIMENT_ID" "$RUN_NUMBER" "$PLATFORM" "$INJECTION_TYPE"

# ─── Step 5: Restore and commit ───────────────────────────────────────────────
echo ""
echo "[5/6] Restoring to clean baseline..."
$PYTHON "$INJECT_SCRIPT" restore

RESTORE_MSG="restore: ${EXPERIMENT_ID} run ${RUN_NUMBER} - ${INJECTION_TYPE} - ${PLATFORM}"
git add -A
git commit -m "$RESTORE_MSG"

case "$PLATFORM" in
    github)
        git push origin main
        ;;
    gitlab)
        git push gitlab main
        ;;
    jenkins)
        git push origin main
        ;;
esac
echo "  Restore committed and pushed."

# ─── Step 6: Summary ──────────────────────────────────────────────────────────
echo ""
echo "[6/6] Run complete."
echo "  Results saved in: experiment_results/${EXPERIMENT_ID}/${PLATFORM}/run_${RUN_NUMBER}/"
echo "  CSV updated:      experiment_results/results.csv"
echo ""
