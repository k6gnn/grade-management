#!/usr/bin/env bash
set -uo pipefail

MAX_RETRIES="${MAX_RETRIES:-3}"
ARTIFACT_DIR="artifacts/test-attempts"
SUREFIRE_DIR="target/surefire-reports"

mkdir -p "$ARTIFACT_DIR"
: > test.log

attempt_outcomes_json=""
attempt_failed_tests_json=""
success_after_retry=false
final_exit=1

collect_failed_tests() {
  local report_dir="$1"
  python3 - "$report_dir" <<'PY'
import json
import sys
from pathlib import Path

report_dir = Path(sys.argv[1])
tests = []

if report_dir.exists():
    for p in sorted(report_dir.glob("*.txt")):
        text = p.read_text(encoding="utf-8", errors="ignore")
        current = None
        for line in text.splitlines():
            if line.startswith("Test set: "):
                current = line.split("Test set: ", 1)[1].strip()
            elif line.startswith("Tests run:"):
                has_failure = "Failures: 0" not in line
                has_error = "Errors: 0" not in line
                if current and (has_failure or has_error):
                    tests.append(current)

seen = set()
ordered = []
for t in tests:
    if t not in seen:
        seen.add(t)
        ordered.append(t)

print(json.dumps(ordered))
PY
}

for attempt in $(seq 1 "$MAX_RETRIES"); do
  echo "=== Test attempt ${attempt}/${MAX_RETRIES} ===" | tee -a test.log

  rm -rf "$SUREFIRE_DIR"
  mkdir -p "$ARTIFACT_DIR/attempt-${attempt}"

  set +e
  mvn test -B --no-transfer-progress -Dci.attempt="${attempt}" 2>&1     | tee "$ARTIFACT_DIR/attempt-${attempt}/test.log"     | tee -a test.log
  exit_code=${PIPESTATUS[0]}
  set -e

  echo "$exit_code" > "$ARTIFACT_DIR/attempt-${attempt}/exit_code.txt"

  if [ -d "$SUREFIRE_DIR" ]; then
    cp -R "$SUREFIRE_DIR" "$ARTIFACT_DIR/attempt-${attempt}/surefire-reports"
  fi

  failed_tests_json=$(collect_failed_tests "$ARTIFACT_DIR/attempt-${attempt}/surefire-reports")
  echo "$failed_tests_json" > "$ARTIFACT_DIR/attempt-${attempt}/failed_tests.json"

  if [ -n "$attempt_outcomes_json" ]; then
    attempt_outcomes_json+=", "
    attempt_failed_tests_json+=", "
  fi

  if [ "$exit_code" -eq 0 ]; then
    outcome="pass"
  else
    outcome="fail"
  fi

  attempt_outcomes_json+="{\"attempt\": ${attempt}, \"outcome\": \"${outcome}\", \"exit_code\": ${exit_code}}"
  attempt_failed_tests_json+="{\"attempt\": ${attempt}, \"failed_tests\": ${failed_tests_json}}"

  if [ "$exit_code" -eq 0 ]; then
    final_exit=0
    if [ "$attempt" -gt 1 ]; then
      success_after_retry=true
    fi
    break
  fi

  final_exit="$exit_code"
done

python3 - <<PY
import json
from pathlib import Path

attempt_outcomes = json.loads('[${attempt_outcomes_json}]')
attempt_failed = json.loads('[${attempt_failed_tests_json}]')

failed_by_attempt = {
    item["attempt"]: set(item["failed_tests"])
    for item in attempt_failed
}

previous_failed = set()
failed_then_passed = set()

for item in attempt_outcomes:
    attempt = item["attempt"]
    if item["outcome"] == "fail":
        previous_failed |= failed_by_attempt.get(attempt, set())
    elif item["outcome"] == "pass" and previous_failed:
        failed_then_passed |= previous_failed

final_failed = set()
if attempt_outcomes and attempt_outcomes[-1]["outcome"] == "fail":
    final_failed = failed_by_attempt.get(attempt_outcomes[-1]["attempt"], set())

summary = {
    "attempt_count": len(attempt_outcomes),
    "max_retries_configured": int("${MAX_RETRIES}"),
    "success_after_retry": ${success_after_retry},
    "flaky_detected": bool(failed_then_passed),
    "attempt_outcomes": attempt_outcomes,
    "failed_then_passed_tests": sorted(failed_then_passed),
    "always_failed_tests": sorted(final_failed - failed_then_passed),
    "notes": "Option B: attempt 1 always fails, attempt 2 may pass, attempt 3 must pass."
}

Path("${ARTIFACT_DIR}/flaky_summary.json").write_text(
    json.dumps(summary, indent=2),
    encoding="utf-8",
)
print(json.dumps(summary, indent=2))
PY

exit "$final_exit"
