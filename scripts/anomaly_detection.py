import json, os

p = "artifacts/test-attempts/flaky_summary.json"
if os.path.exists(p):
    d = json.load(open(p))
    print("FLAKY DETECTED" if d.get("flaky_detected") else "NO FLAKY")
else:
    print("NO DATA")
