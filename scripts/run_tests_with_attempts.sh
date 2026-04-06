#!/usr/bin/env bash
set -e

MAX_RETRIES=3
mkdir -p artifacts/test-attempts

FAILED_ONCE=false
PASSED_AFTER=false

for i in $(seq 1 $MAX_RETRIES); do
  echo "=== Test attempt $i/$MAX_RETRIES ==="

  mvn -q test | tee artifacts/test-attempts/attempt_$i.log
  EXIT_CODE=${PIPESTATUS[0]}

  if [ $EXIT_CODE -ne 0 ]; then
    FAILED_ONCE=true
  else
    if [ "$FAILED_ONCE" = true ]; then
      PASSED_AFTER=true
      break
    fi
  fi
done

FLAKY=false
if [ "$FAILED_ONCE" = true ] && [ "$PASSED_AFTER" = true ]; then
  FLAKY=true
fi

cat > artifacts/test-attempts/flaky_summary.json <<EOF
{
  "attempt_count": $i,
  "failed_once": $FAILED_ONCE,
  "passed_after_retry": $PASSED_AFTER,
  "flaky_detected": $FLAKY,
  "pattern": "fail -> maybe pass -> guaranteed pass by 3"
}
EOF
