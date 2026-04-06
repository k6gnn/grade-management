#!/usr/bin/env bash
set -uo pipefail

MAX_RETRIES="${MAX_RETRIES:-3}"
FLAKY_PROBE_RUNS="${FLAKY_PROBE_RUNS:-2}"
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

run_attempt() {
  local attempt_num="$1"
  local role="$2"
  local attempt_dir="$ATTEMPT_ROOT/attempt-$attempt_num"

  mkdir -p "$attempt_dir/surefire-reports"
  printf '%s\n' "$role" > "$attempt_dir/role.txt"

  echo "=== Test attempt $attempt_num ($role) ==="
  rm -rf target/surefire-reports
  mkdir -p target/surefire-reports

  set +e
  mvn test -B --no-transfer-progress 2>&1 | tee "$attempt_dir/test.log"
  local exit_code=${PIPESTATUS[0]}
  set -e

  echo "$exit_code" > "$attempt_dir/exit_code.txt"

  if [ -d target/surefire-reports ]; then
    cp -R target/surefire-reports/. "$attempt_dir/surefire-reports/" 2>/dev/null || true
  fi

  if [ "$exit_code" -ne 0 ]; then
    collect_failed_tests "$attempt_dir/surefire-reports"
    if [ "${#TEST_TARGETS[@]}" -gt 0 ]; then
      echo "Failed tests in attempt $attempt_num:"
      printf '  - %s\n' "${TEST_TARGETS[@]}"
    else
      echo "No test-level identifiers found; failure may have happened before report generation."
    fi
  else
    echo "Attempt $attempt_num succeeded."
  fi

  return "$exit_code"
}

summarise_attempts() {
python3 - <<'PY2'
import json
import re
from pathlib import Path

root = Path("artifacts/test-attempts")
attempts = []

for attempt_dir in sorted(root.glob("attempt-*"), key=lambda p: int(p.name.split("-")[-1])):
    report_dir = attempt_dir / "surefire-reports"
    role = (attempt_dir / "role.txt").read_text().strip() if (attempt_dir / "role.txt").exists() else "unknown"
    tests = {}
    run_failed = False

    if report_dir.is_dir():
        for txt in sorted(report_dir.glob("*.txt")):
            content = txt.read_text(encoding="utf-8", errors="replace")
            class_name = txt.stem
            failed = bool(re.search(r"Failures:\s*[1-9]|Errors:\s*[1-9]|TestTimedOutException|AssertionError|<<< FAILURE!|<<< ERROR!", content))
            tests[class_name] = {
                "failed": failed,
                "timeout_signal": bool(re.search(r"TestTimedOutException|test timed out after|assertTimeoutPreemptively|The test run has exceeded", content, re.I)),
                "interrupted_signal": bool(re.search(r"InterruptedException", content, re.I)),
            }
            run_failed = run_failed or failed

    exit_code_file = attempt_dir / "exit_code.txt"
    exit_code = int(exit_code_file.read_text().strip()) if exit_code_file.exists() else 999
    attempts.append({
        "attempt": int(attempt_dir.name.split("-")[-1]),
        "role": role,
        "exit_code": exit_code,
        "run_failed": run_failed or exit_code != 0,
        "tests": tests,
    })

all_names = sorted({name for a in attempts for name in a["tests"].keys()})
intermittent_tests = []
failed_then_passed = []
passed_then_failed = []
always_failed = []
always_passed = []
timeout_observed = False
test_profiles = {}

for name in all_names:
    observations = []
    timeout_count = 0
    interrupted_count = 0
    for a in attempts:
        info = a["tests"].get(name)
        if info is None:
            continue
        observations.append(info["failed"])
        timeout_count += int(bool(info.get("timeout_signal")))
        interrupted_count += int(bool(info.get("interrupted_signal")))
        timeout_observed = timeout_observed or info.get("timeout_signal") or info.get("interrupted_signal")

    if not observations:
        continue

    fail_count = sum(1 for s in observations if s)
    pass_count = sum(1 for s in observations if not s)
    intermittent = fail_count > 0 and pass_count > 0
    test_profiles[name] = {
        "observations": len(observations),
        "fail_count": fail_count,
        "pass_count": pass_count,
        "failure_rate": round(fail_count / len(observations), 4),
        "intermittent": intermittent,
        "timeout_signal_count": timeout_count,
        "interrupted_signal_count": interrupted_count,
    }

    if intermittent:
        intermittent_tests.append(name)
        first_fail = observations.index(True)
        first_pass = observations.index(False)
        if any(not s for s in observations[first_fail + 1:]):
            failed_then_passed.append(name)
        if any(s for s in observations[first_pass + 1:]):
            passed_then_failed.append(name)
    elif all(observations):
        always_failed.append(name)
    else:
        always_passed.append(name)

gate_attempts = [a for a in attempts if a["role"] == "gate"]
probe_attempts = [a for a in attempts if a["role"] == "probe"]
gate_success_after_retry = bool(gate_attempts) and gate_attempts[-1]["exit_code"] == 0 and any(a["exit_code"] != 0 for a in gate_attempts[:-1])
probe_failures_observed = any(a["exit_code"] != 0 or a["run_failed"] for a in probe_attempts)
overall_failure_rate = round(sum(1 for a in attempts if a["run_failed"]) / len(attempts), 4) if attempts else 0.0

flaky_detected = bool(intermittent_tests or failed_then_passed or (gate_success_after_retry and timeout_observed) or probe_failures_observed)
if failed_then_passed:
    detection_basis = "failed_then_passed"
elif intermittent_tests and probe_failures_observed:
    detection_basis = "post_success_probe_intermittence"
elif intermittent_tests:
    detection_basis = "intermittent_across_attempts"
elif gate_success_after_retry and timeout_observed:
    detection_basis = "timeout_recovered_by_retry"
else:
    detection_basis = "none"

report = {
    "attempt_count": len(attempts),
    "gate_attempt_count": len(gate_attempts),
    "probe_attempt_count": len(probe_attempts),
    "attempts": attempts,
    "gate_success_after_retry": gate_success_after_retry,
    "probe_failures_observed": probe_failures_observed,
    "timeout_observed": timeout_observed,
    "intermittent_tests": intermittent_tests,
    "failed_then_passed_tests": failed_then_passed,
    "passed_then_failed_tests": passed_then_failed,
    "always_failed_tests": always_failed,
    "always_passed_tests": always_passed,
    "test_profiles": test_profiles,
    "overall_failure_rate": overall_failure_rate,
    "flaky_detected": flaky_detected,
    "detection_basis": detection_basis,
}

(root / "flaky_summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
PY2
}

attempt=1
gate_success=0
gate_success_attempt=0
last_gate_attempt=0

while [ "$attempt" -le "$MAX_RETRIES" ]; do
  last_gate_attempt="$attempt"
  if run_attempt "$attempt" "gate"; then
    gate_success=1
    gate_success_attempt="$attempt"
    break
  fi
  echo "Gate attempt $attempt failed."
  attempt=$((attempt + 1))
done

final_exit=1
selected_attempt="$last_gate_attempt"
if [ "$gate_success" -eq 1 ]; then
  final_exit=0
  selected_attempt="$gate_success_attempt"

  probe=1
  while [ "$probe" -le "$FLAKY_PROBE_RUNS" ]; do
    attempt=$((gate_success_attempt + probe))
    if run_attempt "$attempt" "probe"; then
      echo "Diagnostic probe $probe succeeded."
    else
      echo "Diagnostic probe $probe observed a failure signal."
    fi
    probe=$((probe + 1))
  done
fi

selected_attempt_dir="$ATTEMPT_ROOT/attempt-$selected_attempt"
cp "$selected_attempt_dir/test.log" test.log 2>/dev/null || true
rm -rf target/surefire-reports
mkdir -p target/surefire-reports
cp -R "$selected_attempt_dir/surefire-reports/." target/surefire-reports/ 2>/dev/null || true

summarise_attempts
exit "$final_exit"
