#!/usr/bin/env bash
set -uo pipefail

MAX_RETRIES="${MAX_RETRIES:-3}"
ATTEMPT_ROOT="artifacts/test-attempts"
SUMMARY_JSON="$ATTEMPT_ROOT/flaky_summary.json"
mkdir -p "$ATTEMPT_ROOT"

TEST_TARGETS=()
collect_failed_tests() {
  local report_dir="$1"
  TEST_TARGETS=()
  if [ ! -d "$report_dir" ]; then
    return 0
  fi

  while IFS= read -r file; do
    [ -f "$file" ] || continue
    local class_name
    class_name=$(basename "$file" .txt)

    mapfile -t methods < <(grep -Eo '<<< (FAILURE|ERROR)! - in [A-Za-z0-9_$.]+' "$file" \
      | sed -E 's/^<<< (FAILURE|ERROR)! - in //' \
      | sort -u)

    if [ "${#methods[@]}" -eq 0 ]; then
      if grep -Eq 'Failures: [1-9]|Errors: [1-9]|TestTimedOutException|AssertionError|FAILED|ERROR' "$file"; then
        TEST_TARGETS+=("$class_name")
      fi
    else
      TEST_TARGETS+=("${methods[@]}")
    fi
  done < <(find "$report_dir" -maxdepth 1 -name '*.txt' -type f | sort)
}

summarise_attempts() {
python3 - <<'PY'
import json
import os
import re
from pathlib import Path

root = Path("artifacts/test-attempts")
attempts = []
for attempt_dir in sorted(root.glob("attempt-*")):
    report_dir = attempt_dir / "surefire-reports"
    tests = {}
    run_failed = False
    if report_dir.is_dir():
        for txt in sorted(report_dir.glob("*.txt")):
            content = txt.read_text(encoding="utf-8", errors="replace")
            class_name = txt.stem
            failed = bool(re.search(r"Failures:\s*[1-9]|Errors:\s*[1-9]|TestTimedOutException|AssertionError|<<< FAILURE!|<<< ERROR!", content))
            tests[class_name] = {
                "failed": failed,
                "timeout_signal": bool(re.search(r"TestTimedOutException|test timed out after|The test run has exceeded", content, re.I)),
                "interrupted_signal": bool(re.search(r"InterruptedException", content, re.I)),
            }
            run_failed = run_failed or failed
    exit_code_file = attempt_dir / "exit_code.txt"
    exit_code = int(exit_code_file.read_text().strip()) if exit_code_file.exists() else 999
    attempts.append({
        "attempt": int(attempt_dir.name.split("-")[-1]),
        "exit_code": exit_code,
        "run_failed": run_failed or exit_code != 0,
        "tests": tests,
    })

all_names = sorted({name for a in attempts for name in a["tests"].keys()})
failed_then_passed = []
always_failed = []
timeout_observed = False
for name in all_names:
    states = []
    for a in attempts:
        info = a["tests"].get(name)
        if info is None:
            continue
        states.append(info["failed"])
        timeout_observed = timeout_observed or info.get("timeout_signal") or info.get("interrupted_signal")
    if not states:
        continue
    if True in states and False in states:
        first_fail = states.index(True)
        if any(not s for s in states[first_fail + 1:]):
            failed_then_passed.append(name)
    elif all(states):
        always_failed.append(name)

success_after_retry = len(attempts) > 1 and attempts[-1]["exit_code"] == 0 and any(a["exit_code"] != 0 for a in attempts[:-1])
flaky_detected = bool(failed_then_passed or (success_after_retry and timeout_observed))

report = {
    "attempt_count": len(attempts),
    "attempts": attempts,
    "success_after_retry": success_after_retry,
    "timeout_observed": timeout_observed,
    "failed_then_passed_tests": failed_then_passed,
    "always_failed_tests": always_failed,
    "flaky_detected": flaky_detected,
    "detection_basis": "attempt_history" if flaky_detected else "none",
}

(root / "flaky_summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
PY
}

attempt=1
success=0
while [ "$attempt" -le "$MAX_RETRIES" ]; do
  attempt_dir="$ATTEMPT_ROOT/attempt-$attempt"
  mkdir -p "$attempt_dir/surefire-reports"

  echo "=== Test attempt $attempt/$MAX_RETRIES ==="
  rm -rf target/surefire-reports
  mkdir -p target/surefire-reports

  set +e
  mvn test -B --no-transfer-progress 2>&1 | tee "$attempt_dir/test.log"
  exit_code=${PIPESTATUS[0]}
  set -e

  echo "$exit_code" > "$attempt_dir/exit_code.txt"

  if [ -d target/surefire-reports ]; then
    cp -R target/surefire-reports/. "$attempt_dir/surefire-reports/" 2>/dev/null || true
  fi

  if [ "$exit_code" -eq 0 ]; then
    success=1
    echo "Attempt $attempt succeeded."
    break
  fi

  echo "Attempt $attempt failed with exit code $exit_code."
  collect_failed_tests "$attempt_dir/surefire-reports"
  if [ "${#TEST_TARGETS[@]}" -gt 0 ]; then
    echo "Failed tests in attempt $attempt:"
    printf '  - %s\n' "${TEST_TARGETS[@]}"
  else
    echo "No test-level identifiers found; failure may have happened before report generation."
  fi

  attempt=$((attempt + 1))
done

if [ "$success" -eq 1 ]; then
  last_attempt_dir="$ATTEMPT_ROOT/attempt-$attempt"
  cp "$last_attempt_dir/test.log" test.log
  rm -rf target/surefire-reports
  mkdir -p target/surefire-reports
  cp -R "$last_attempt_dir/surefire-reports/." target/surefire-reports/ 2>/dev/null || true
else
  last_attempt_dir="$ATTEMPT_ROOT/attempt-$MAX_RETRIES"
  cp "$last_attempt_dir/test.log" test.log 2>/dev/null || true
fi

summarise_attempts

if [ "$success" -eq 1 ]; then
  exit 0
fi
exit 1
