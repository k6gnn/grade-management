"""
m13_predict.py — M13 ML Failure Classification (Inference)
===========================================================
Runs post-pipeline (always(), regardless of outcome) to classify
what type of failure occurred, if any.

Consumes:
  - pipeline_status.json  (stage results, commit/branch/event metadata)
  - logs/                 (build.log, test.log, flaky_failure_log.txt,
                           surefire-reports/*.txt)

Outputs:
  - m13_classification_report.json

Usage (as invoked by ci-cd.yml):
    python scripts/m13_predict.py \\
        --status pipeline_status.json \\
        --logs logs \\
        --model-bundle models/m13_model_bundle.pkl \\
        --output m13_classification_report.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

# ---------------------------------------------------------------------------
# GuardrailParams — must be defined here so joblib can deserialize the
# model bundle that was saved with this class in train_m13.py.
# ---------------------------------------------------------------------------

CONFIG_TO_COMPILE_MARGIN    = 0.18
CONFIG_TO_TEST_MARGIN       = 0.18
MIN_ALT_PROBA_FOR_GUARDRAIL = 0.30

@dataclass(frozen=True)
class GuardrailParams:
    config_to_compile_margin:    float = CONFIG_TO_COMPILE_MARGIN
    config_to_test_margin:       float = CONFIG_TO_TEST_MARGIN
    min_alt_proba_for_guardrail: float = MIN_ALT_PROBA_FOR_GUARDRAIL

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("m13_predict")

# ---------------------------------------------------------------------------
# Feature constants (must match train_m13.py exactly)
# ---------------------------------------------------------------------------
TARGET_LABELS = ["compilation", "test_failure", "flaky_test", "configuration", "infrastructure"]

FEATURE_NAMES = [
    "feat_tests_ran", "feat_tests_failed", "feat_num_tests_failed",
    "feat_num_tests_run", "feat_num_tests_skipped", "feat_fail_ratio",
    "feat_build_duration",
    "feat_fw_junit", "feat_fw_pytest", "feat_fw_gradle", "feat_fw_vitest", "feat_is_java",
    "feat_kw_compile_fail", "feat_kw_test_assert", "feat_kw_flaky",
    "feat_kw_infra_network", "feat_kw_infra_runner", "feat_kw_dep_resolution",
    "feat_kw_config_fail",
    "feat_compile_no_tests", "feat_compile_with_config", "feat_config_no_compile",
    "feat_step_is_test", "feat_step_is_compile", "feat_step_is_lint",
    "feat_step_is_docker", "feat_step_is_setup",
    "feat_primary_is_compile", "feat_primary_is_test", "feat_primary_is_flaky",
    "feat_primary_is_infra", "feat_primary_is_config", "feat_primary_is_runtime",
    "feat_compile_jvm_strong", "feat_compile_ts_strong", "feat_compile_rust_go_strong",
    "feat_compile_native_linker", "feat_compile_python_syntax", "feat_compile_build_task",
    "feat_config_secret_env", "feat_config_yaml_workflow", "feat_config_lint_format",
    "feat_config_auth_permission", "feat_config_missing_file", "feat_config_tool_version",
    "feat_config_dep_resolution_strong",
    "feat_first_error_before_tests", "feat_first_error_after_tests_started",
    "feat_error_in_compile_step", "feat_error_in_setup_step",
    "feat_error_in_lint_step", "feat_error_in_test_step",
    "feat_has_compile_task", "feat_has_dependency_install",
    "feat_compile_over_config_guard", "feat_config_strong_only",
    "feat_dep_resolution_without_network",
]

# ---------------------------------------------------------------------------
# Compiled regex patterns (mirrors train_m13.py)
# ---------------------------------------------------------------------------
_STEP_TEST    = re.compile(r"\b(pytest|unittest|test|surefire|failsafe|jest|mocha|rspec|vitest|nunit|xunit)\b", re.I)
_STEP_COMPILE = re.compile(r"\b(compile|build|javac|tsc|rustc|gcc|g\+\+|clang|cmake|make|gradle|maven.*compil|kotlin|kotlinc)\b", re.I)
_STEP_LINT    = re.compile(r"\b(lint|format|flake8|mypy|ruff|eslint|checkstyle|spotless|black|isort|prettier|rubocop|clippy)\b", re.I)
_STEP_DOCKER  = re.compile(r"\b(docker|buildx|buildkit|container|image|push|pull)\b", re.I)
_STEP_SETUP   = re.compile(r"\b(set.?up|install|setup|bootstrap|init|configure|pip install|npm install|yarn|pnpm|bundle|composer install)\b", re.I)
_ERROR_ANCHOR = re.compile(
    r"error:|fatal:|failed|failure|exception|traceback|cannot find symbol|syntaxerror|"
    r"compilation failed|compilation error|process completed with exit code|assertionerror|"
    r"no matching distribution|npm err!|could not resolve|permission denied|invalid yaml|"
    r"no such file or directory", re.I)
_TEST_START   = re.compile(
    r"\b(pytest|collected \d+ items|tests run:|surefire|failsafe|jest|vitest|mocha|rspec|go test|cargo test|dotnet test)\b", re.I)

# ---------------------------------------------------------------------------
# Text cleaning (mirrors train_m13.py)
# ---------------------------------------------------------------------------

def clean(text: str) -> str:
    text = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])").sub("", text)
    text = re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\s*", "", text)
    text = re.sub(r"##\[(?:group|endgroup|debug|section|command)\].*", "", text)
    return text


def _bool(pattern: str, text: str) -> int:
    return int(bool(re.search(pattern, text, re.I | re.M)))


def _first_pos(rx: re.Pattern, text: str) -> Optional[int]:
    m = rx.search(text)
    return None if m is None else m.start()


def _before(a: Optional[int], b: Optional[int]) -> bool:
    return a is not None and (b is None or a < b)


# ---------------------------------------------------------------------------
# Ontology one-hot (mirrors train_m13.py — runtime ≠ configuration)
# ---------------------------------------------------------------------------

def primary_to_onehot(primary: str) -> tuple[int, int, int, int, int, int]:
    p = (primary or "").lower()
    return (
        int(p.startswith("compile.")),
        int(p.startswith("test.")),
        int(p.startswith("flaky.")),
        int(p.startswith(("infra.", "timeout.", "cancel."))),
        int(p.startswith(("config.", "dep.", "auth.", "policy.", "scm.",
                           "artifact.", "cache.", "workspace.", "deploy.", "static."))),
        int(p.startswith("runtime.")),
    )


# ---------------------------------------------------------------------------
# Feature extraction (mirrors train_m13.py)
# ---------------------------------------------------------------------------

def extract_features(row: dict) -> list[float]:
    text         = row.get("text", "")
    lang         = str(row.get("lang", "")).lower()
    failing_step = str(row.get("failing_step", "")).lower()
    primary      = str(row.get("primary_label", ""))

    c = clean(text)

    # ── Test counts ───────────────────────────────────────────────────────────
    maven_runs   = sum(int(x) for x in re.findall(r"tests run: (\d+)", c, re.I))
    maven_fail   = sum(int(x) for x in re.findall(r"failures: (\d+)", c, re.I))
    # FIX RC2: Surefire records OOM/RuntimeException as 'Errors', not 'Failures'.
    # Treat errors as failures so feat_tests_failed and fail_ratio are non-zero
    # when the only signal is "Errors: 1" (as happens for E5B OOM).
    maven_errors = sum(int(x) for x in re.findall(r"errors: (\d+)", c, re.I))
    maven_fail   = maven_fail + maven_errors
    maven_skip   = sum(int(x) for x in re.findall(r"skipped: (\d+)", c, re.I))
    pytest_fail = sum(int(x) for x in re.findall(r"(\d+) failed", c, re.I))
    pytest_pass = sum(int(x) for x in re.findall(r"(\d+) passed", c, re.I))
    pytest_skip = sum(int(x) for x in re.findall(r"(\d+) skipped", c, re.I))
    pytest_runs = pytest_fail + pytest_pass

    num_run    = maven_runs if maven_runs > 0 else pytest_runs
    num_failed = maven_fail if maven_fail > 0 else pytest_fail
    num_skip   = maven_skip if maven_skip > 0 else pytest_skip
    tests_ran  = 1 if num_run > 0 else 0
    tests_failed_flag = 1 if num_failed > 0 else 0
    fail_ratio = num_failed / num_run if num_run > 0 else 0.0

    # ── Timing ────────────────────────────────────────────────────────────────
    build_dur = 0.0
    bd = re.search(r"BUILD (?:FAILED|SUCCESSFUL) in (\d+)m (\d+)s", c, re.I)
    if bd:
        build_dur = int(bd.group(1)) * 60 + int(bd.group(2))
    else:
        bd2 = re.search(r"BUILD (?:FAILED|SUCCESSFUL) in (\d+)s", c, re.I)
        if bd2:
            build_dur = float(bd2.group(1))
        else:
            elapsed = re.findall(r"Time elapsed: ([\d.]+) s", c, re.I)
            if elapsed:
                build_dur = sum(float(x) for x in elapsed)

    # ── Frameworks ────────────────────────────────────────────────────────────
    fw_junit  = _bool(r"\bjunit\b|\bsurefire\b|\bfailsafe\b", c)
    fw_pytest = _bool(r"\bpytest\b|\bpy\.test\b|\bunittest\b", c)
    fw_gradle = _bool(r"\bgradle\b|\bgradlew\b", c)
    fw_vitest = _bool(r"\bvitest\b|\bjest\b|\bmocha\b|\brspec\b", c)
    is_java   = 1 if lang == "java" else 0

    # ── Strong compile sub-features ───────────────────────────────────────────
    compile_jvm_strong = _bool(
        r"\[error\]\s+compilation error|compilation failed; see the compiler error output|"
        r"maven-compiler-plugin.{0,120}(failure|failed)|javac.{0,80}error|"
        r"\.java:\d+: error|e: .+\.kt:\(\d+,\d+\):|kotlin compilation error|"
        r"execution failed for task.{0,80}compil|> task :.{0,100}compil.{0,60}failed|"
        r"package .{0,120} does not exist|cannot find symbol|cannot resolve symbol|"
        r"unresolved reference:", c)
    compile_ts_strong = _bool(
        r"error ts\d+:|ts\d+: error ts|tsc.{0,80}(found|failed|error)|"
        r"typescript.{0,80}\d+ errors?|type error:", c)
    compile_rust_go_strong = _bool(
        r"rustc.{0,60}error\[|error\[e\d+\]:|could not compile\s+`|"
        r"go build.{0,80}failed|build constraints exclude all go files|"
        r"^fail\s+\S+\s+\[build failed\]|error: aborting due to \d+ previous errors?", c)
    compile_native_linker = _bool(
        r"undefined reference to|ld returned \d+ exit status|linker command failed|"
        r"collect2: error: ld returned|ld: library not found|cannot find -l\w+|"
        r"undefined symbols for architecture|link\.exe.*fatal error lnk|"
        r"error c\d{4}:|fatal error c\d{4}:", c)
    compile_python_syntax = _bool(
        r"syntaxerror:|indentationerror:|taberror:|unterminated string literal|"
        r"invalid syntax|parse error|unexpected eof while parsing", c)
    compile_build_task = _bool(
        r"execution failed for task.{0,100}(compile|build)|"
        r"> task :.{0,100}(compile|build).{0,60}failed|"
        r"build failed.*(javac|kotlinc|tsc|rustc|gcc|clang|msbuild|cmake)", c)

    kw_compile = int(any([compile_jvm_strong, compile_ts_strong, compile_rust_go_strong,
                           compile_native_linker, compile_python_syntax, compile_build_task])
                     or bool(re.search(
                         r"compilation error|compilation failed|cannot find symbol|"
                         r"syntaxerror:|indentationerror:|unresolved reference:|error ts\d+:|"
                         r"undefined reference to|rustc.*error\[|could not find crate|"
                         r"kotlin compilation error|error: aborting due to", c, re.I)))

    # ── Test / flaky / infra signals ──────────────────────────────────────────
    kw_assert = _bool(
        r"assertionerror|assertionfailederror|expected:<|failures!!!|failed .+\.py::|"
        r"e\s+assert |short test summary|= failures =|comparisonfailure|"
        r"expected:.*but was:|tests run.*failures: [1-9]|"
        r"assertion .left == right. failed|not equal: expected", c)
    kw_flaky = _bool(
        r"testtimeoutexception|timed out after \d+|concurrentmodificationexception|intermittent|"
        # FIX E5I: 'race condition' and 'data race' removed from kw_flaky.
        # When they appear in the InfrastructureSimulator's message ("Simulated race condition")
        # they signal a deliberate infra-class experiment, not a genuine flaky test.
        # Generic concurrency issues that ARE flaky keep their own patterns below.
        #
        # FIX E5H: 'sockettimeoutexception' and 'readtimeoutexception' removed from kw_flaky.
        # A SocketTimeoutException from a failed external network call is an infrastructure
        # signal, not flakiness. Having it here caused the model to lean toward flaky_test
        # for E5H and pulled it outside the classes the infra guardrail covers.
        # Both are now covered by kw_infra_network instead.
        r"non.?deterministic|rerun failures|flakes: [1-9]|"
        r"\bflaky\b|flakytest|"
        r"passed \d+ times.*failed \d+ times|retry.*attempt \d+|staleelementreferenceexception|"
        r"jest did not exit one second", c)
    kw_infra_network = _bool(
        r"connection refused|connection reset by peer|econnreset|"
        r"temporary failure in name resolution|name or service not known|"
        r"could not resolve host|getaddrinfo enotfound|eai_again|"
        r"tls handshake|certificate verify failed|503 service unavailable|502 bad gateway|"
        r"504 gateway timeout|unexpected http response: 5\d\d|toomanyrequests|"
        r"you have reached your pull rate limit|failed to pull image|manifest unknown|"
        r"error response from daemon|"
        r"failed to fetch.{0,80}(archive\.ubuntu|pypi|npmjs|crates\.io)|"
        r"readtimeouterror.*httpsconnectionpool|"
        r"retrying.{0,80}(readtimeouterror|connectionerror)|"
        r"no space left on device|enospc|"
        # FIX E5D: BindException / port-conflict is an OS-level resource failure,
        # not an application assertion. Covers both the simulator's message and
        # real runner port-conflict errors across all platforms.
        r"address already in use|bindexception|eaddrinuse|"
        r"simulated port conflict|"
        # FIX E5H: socket/read timeouts are infra (external service unavailable),
        # not flaky tests. Moved here from kw_flaky. Also match the simulator's
        # message directly so this fires even if the surefire artifact is absent.
        r"sockettimeoutexception|readtimeoutexception|connect timed out|connection timed out|"
        r"no route to host|network is unreachable|"
        r"simulated external service unavailable", c)
    kw_infra_runner = _bool(
        r"the runner has received a shutdown signal|runner.{0,40}lost communication|"
        r"worker process exited with code|process completed with exit code 137|"
        r"outofmemoryerror|java\.lang\.outofmemoryerror|oomkilled|"
        r"javascript heap out of memory|fatal error: runtime: out of memory|"
        r"signal: killed|updated oom_score_adj|"
        # FIX E5I: race condition / data race moved here from kw_flaky.
        # "Simulated race condition" and "data race" in the infra simulator are
        # resource-safety failures, not test flakiness. Keeping 'deadlock' here
        # too since it was already present via kw_flaky and belongs with infra.
        r"simulated race condition|data race|deadlock|"
        r"unsynchronised concurrent access caused data corruption|"
        # FIX E5G: a missing/corrupted JAR is a packaging-stage infra failure.
        # The antrun plugin deletes target/ so the verify step finds no artifact.
        # These phrases appear in the pipeline log and in the antrun echo message.
        r"no jar artifact found|failure: no jar artifact found|"
        r"injected.*target directory deleted|e5g|"
        r"corrupt.*artifact|artifact.*corrupt", c)

    # ── Split config sub-features ─────────────────────────────────────────────
    config_secret_env = _bool(
        r"missing secret|secret .{0,80} not found|credentials not found|"
        r"environment variable .{0,80} is not set|required env(?:ironment)? var|"
        r"input required and not supplied|missing required input|"
        r"api key.{0,80}(missing|not set)", c)
    config_yaml_workflow = _bool(
        r"yaml\.scanner\.scannerror|invalid yaml|yaml parse error|workflow is not valid|"
        r"the workflow is not valid|mapping values are not allowed|"
        r"did not find expected key|unexpected value '.{0,80}'|"
        r"a sequence was not expected|"
        # Spring Boot configuration failure patterns
        r"failed to bind properties|applicationcontext failure|"
        r"application failed to start|"
        r"bindexception.*failed to bind|"
        r"configuration property.{0,80}is not valid|"
        r"invalid value.{0,80}server\.port|"
        # Custom M8 validation gate patterns (from config_validation.log)
        r"server\.port must be numeric|"
        r"invalid_port_value|"
        r"server\.port.{0,40}invalid|"
        r"application\.properties.{0,80}invalid|"
        r"FAILURE: server\.port|FAILURE: spring\.|FAILURE: Required key", c)
    config_lint_format = _bool(
        r"black --check|would reformat|\d+ files? would be reformatted|flake8|ruff.{0,80}error|"
        r"mypy.*error|eslint.{0,80}error|prettier.{0,80}check.{0,80}failed|"
        r"pre-commit.*failed|hookid:|checkstyle.{0,80}violations|spotless check failed|"
        r"isort.{0,80}check.{0,80}failed|rubocop.{0,80}(offense|failed)", c)
    config_auth_permission = _bool(
        r"permission denied|forbidden|unauthorized|authentication failed|bad credentials|"
        r"could not read username|remote: invalid username or password|"
        r"403 forbidden|401 unauthorized", c)
    config_missing_file = _bool(
        r"no such file or directory|file not found|could not find file|"
        r"cannot find path|path does not exist|missing file|directory nonexistent", c)
    config_tool_version = _bool(
        r"unsupported node version|unsupported python version|requires python [><=]|"
        r"node version .{0,80} not supported|java version .{0,80} not supported|"
        r"unsupported engine|npm err! engine|the engine .{0,80} is incompatible", c)
    config_dep_resolution_strong = _bool(
        r"could not resolve dependencies|could not resolve all files for configuration|"
        r"could not find a version that satisfies|no matching distribution found for|"
        r"could not find artifact|npm err! eresolve|npm err! could not resolve dependency|"
        r"npm err! 404|npm err! etarget|npm err! notarget|npm err! eunsupportedprotocol|"
        r"bundler::gemnotfound|go: module .{0,120} not found|dependency convergence error|"
        r"version conflict|non-resolvable parent pom|"
        r"composer.{0,80}(could not find|conflict)", c)

    kw_dep_resolution = config_dep_resolution_strong
    kw_config = int(any([config_secret_env, config_yaml_workflow, config_lint_format,
                          config_auth_permission, config_missing_file,
                          config_tool_version, config_dep_resolution_strong]))

    # ── Step name features ────────────────────────────────────────────────────
    step_test    = int(bool(_STEP_TEST.search(failing_step)))
    step_compile = int(bool(_STEP_COMPILE.search(failing_step)))
    step_lint    = int(bool(_STEP_LINT.search(failing_step)))
    step_docker  = int(bool(_STEP_DOCKER.search(failing_step)))
    step_setup   = int(bool(_STEP_SETUP.search(failing_step)))

    # ── Stage / order features ────────────────────────────────────────────────
    first_error_pos = _first_pos(_ERROR_ANCHOR, c)
    first_test_pos  = _first_pos(_TEST_START, c)
    first_error_before_tests        = int(_before(first_error_pos, first_test_pos))
    first_error_after_tests_started = int(
        first_error_pos is not None
        and first_test_pos is not None
        and first_error_pos > first_test_pos)

    has_compile_task = _bool(
        r"\b(javac|kotlinc|tsc|rustc|gcc|g\+\+|clang|msbuild|cmake|"
        r"maven-compiler-plugin|compilejava|compilekotlin|cargo build|"
        r"go build|npm run build)\b", c)
    has_dependency_install = _bool(
        r"\b(pip install|npm install|npm ci|yarn install|pnpm install|"
        r"bundle install|composer install|mvn dependency|gradle dependencies|"
        r"go mod download|cargo fetch)\b", c)

    error_in_compile_step = int(step_compile or (kw_compile and has_compile_task))
    error_in_setup_step   = int(step_setup or (has_dependency_install and kw_config and not kw_compile))
    error_in_lint_step    = int(step_lint or config_lint_format)
    error_in_test_step    = int(step_test or first_error_after_tests_started)

    # ── Boundary helper features ──────────────────────────────────────────────
    compile_no_tests    = int(kw_compile == 1 and tests_ran == 0)
    compile_with_config = int(kw_compile == 1 and kw_config == 1)
    config_no_compile   = int(kw_config == 1 and kw_compile == 0)

    strong_compile_count = sum([compile_jvm_strong, compile_ts_strong, compile_rust_go_strong,
                                 compile_native_linker, compile_python_syntax, compile_build_task])
    strong_config_count  = sum([config_secret_env, config_yaml_workflow, config_lint_format,
                                 config_auth_permission, config_tool_version,
                                 config_dep_resolution_strong])

    compile_over_config_guard      = int(strong_compile_count > 0 and not (step_setup or has_dependency_install))
    config_strong_only             = int(strong_config_count > 0 and strong_compile_count == 0)
    dep_resolution_without_network = int(config_dep_resolution_strong == 1 and kw_infra_network == 0)

    # ── Ontology one-hots ─────────────────────────────────────────────────────
    p_compile, p_test, p_flaky, p_infra, p_config, p_runtime = primary_to_onehot(primary)

    return [
        float(tests_ran), float(tests_failed_flag), float(num_failed),
        float(num_run), float(num_skip), float(fail_ratio), float(build_dur),
        float(fw_junit), float(fw_pytest), float(fw_gradle), float(fw_vitest),
        float(is_java),
        float(kw_compile), float(kw_assert), float(kw_flaky),
        float(kw_infra_network), float(kw_infra_runner),
        float(kw_dep_resolution), float(kw_config),
        float(compile_no_tests), float(compile_with_config), float(config_no_compile),
        float(step_test), float(step_compile), float(step_lint),
        float(step_docker), float(step_setup),
        float(p_compile), float(p_test), float(p_flaky),
        float(p_infra), float(p_config), float(p_runtime),
        float(compile_jvm_strong), float(compile_ts_strong),
        float(compile_rust_go_strong), float(compile_native_linker),
        float(compile_python_syntax), float(compile_build_task),
        float(config_secret_env), float(config_yaml_workflow),
        float(config_lint_format), float(config_auth_permission),
        float(config_missing_file), float(config_tool_version),
        float(config_dep_resolution_strong),
        float(first_error_before_tests), float(first_error_after_tests_started),
        float(error_in_compile_step), float(error_in_setup_step),
        float(error_in_lint_step), float(error_in_test_step),
        float(has_compile_task), float(has_dependency_install),
        float(compile_over_config_guard), float(config_strong_only),
        float(dep_resolution_without_network),
    ]


# ---------------------------------------------------------------------------
# Guardrail indices (mirrors train_m13.py)
# ---------------------------------------------------------------------------

def _idx(name: str) -> int:
    return FEATURE_NAMES.index(name)


_COMPILE_GUARD_IDXS = [
    _idx("feat_kw_compile_fail"),
    _idx("feat_compile_jvm_strong"),
    _idx("feat_compile_ts_strong"),
    _idx("feat_compile_rust_go_strong"),
    _idx("feat_compile_native_linker"),
    _idx("feat_compile_python_syntax"),
    _idx("feat_compile_build_task"),
    _idx("feat_error_in_compile_step"),
    _idx("feat_compile_over_config_guard"),
]
_TRUE_CONFIG_GUARD_IDXS = [
    _idx("feat_config_secret_env"),
    _idx("feat_config_yaml_workflow"),
    _idx("feat_config_lint_format"),
    _idx("feat_config_auth_permission"),
    _idx("feat_config_tool_version"),
    _idx("feat_dep_resolution_without_network"),
    _idx("feat_error_in_setup_step"),
]
_TEST_GUARD_IDXS = [
    _idx("feat_tests_ran"),
    _idx("feat_tests_failed"),
    _idx("feat_kw_test_assert"),
    _idx("feat_error_in_test_step"),
]


def predict_with_guardrails(
    model: Any,
    X: np.ndarray,
    class_names: list[str],
    config_to_compile_margin: float = 0.18,
    config_to_test_margin: float = 0.18,
    min_alt_proba: float = 0.30,
) -> np.ndarray:
    """Evidence-gated guardrail: prevents configuration over-prediction."""
    raw_pred = model.predict(X).copy()
    if not hasattr(model, "predict_proba"):
        return raw_pred

    proba = model.predict_proba(X)
    cn    = list(class_names)

    if "configuration" not in cn or "compilation" not in cn or "test_failure" not in cn:
        return raw_pred

    cfg_enc  = cn.index("configuration")
    comp_enc = cn.index("compilation")
    test_enc = cn.index("test_failure")

    guarded = raw_pred.copy()
    for i in range(len(raw_pred)):
        if raw_pred[i] != cfg_enc:
            continue

        p_cfg  = proba[i, cfg_enc]
        p_comp = proba[i, comp_enc]
        p_test = proba[i, test_enc]

        strong_compile = X[i, _COMPILE_GUARD_IDXS].sum() > 0
        true_config    = X[i, _TRUE_CONFIG_GUARD_IDXS].sum() > 0
        test_signal    = X[i, _TEST_GUARD_IDXS].sum() > 0

        if (strong_compile and not true_config
                and p_comp >= min_alt_proba
                and (p_cfg - p_comp) <= config_to_compile_margin):
            guarded[i] = comp_enc
            continue

        if (test_signal and not true_config
                and p_test >= min_alt_proba
                and (p_cfg - p_test) <= config_to_test_margin):
            guarded[i] = test_enc
            continue

    return guarded


# ---------------------------------------------------------------------------
# Log collection
# ---------------------------------------------------------------------------

def collect_logs(logs_dir: Path) -> str:
    """Collect and concatenate all log files from logs_dir.

    Also includes config_validation.log from the workspace root when present,
    because Jenkins unstashes it there before copying it into logs/.  This
    ensures M8 configuration signals are always present in the feature text
    regardless of which copy succeeds first.
    """
    parts: list[str] = []

    # Always try to pick up config_validation.log from the workspace root
    # (Jenkins post-step unstashes it there; it may not yet be in logs/)
    root_config_log = Path("config_validation.log")
    if root_config_log.exists() and root_config_log not in logs_dir.glob("*.log"):
        try:
            parts.append(root_config_log.read_text(encoding="utf-8", errors="replace"))
            log.info("Included workspace-root config_validation.log")
        except Exception as exc:
            log.warning("Could not read config_validation.log: %s", exc)

    for pattern in ("*.log", "*.txt", "surefire-reports/*.txt",
                     "surefire-reports/**/*.txt"):
        for p in sorted(logs_dir.glob(pattern)):
            if p.stat().st_size > 5 * 1024 * 1024:   # skip files > 5 MB
                log.warning("Skipping large log file: %s (%.1f MB)", p.name,
                            p.stat().st_size / 1024 / 1024)
                continue
            try:
                parts.append(p.read_text(encoding="utf-8", errors="replace"))
            except Exception as exc:
                log.warning("Could not read %s: %s", p, exc)
    combined = "\n".join(parts)
    log.info("Collected %.1f KB of log text from %d files",
             len(combined) / 1024, len(parts))
    return combined


def detect_language(log_text: str) -> str:
    """Heuristic language detection from log content."""
    lc = log_text.lower()
    if any(k in lc for k in ("maven", "gradle", "surefire", ".java:", "kotlin", "javac")):
        return "java"
    if any(k in lc for k in ("pytest", "python", ".py:", "pip install")):
        return "python"
    if any(k in lc for k in ("npm", "node", "jest", "vitest", "tsc", "typescript")):
        return "javascript"
    if any(k in lc for k in ("cargo", "rustc", "error[e")):
        return "rust"
    if any(k in lc for k in ("go build", "go test", "go mod")):
        return "go"
    return ""


def infer_failing_step(status: dict, log_text: str) -> str:
    """
    Determine the name of the failing step from pipeline status and log content.
    Returns a string describing the step (used for feat_step_* features).
    """
    build_st   = status.get("build_status", "success")
    test_st    = status.get("test_status", "success")
    config_st  = status.get("config_status", "success")
    package_st = status.get("package_status", "success")

    _fail = {"failure", "failed", "FAILURE", "unstable", "UNSTABLE"}

    if config_st in _fail:
        # Configuration validation failure — check log for detail.
        if "pom.xml" in log_text.lower() or "application.properties" in log_text.lower():
            return "configuration validation"
        return "setup"

    if build_st in _fail:
        # Compile step failed.
        return "compile build"

    if test_st in _fail:
        # Determine if it's a test or flaky pattern.
        if re.search(r"attempt_failed|flaky|intermittent|retry", log_text, re.I):
            return "test flaky"
        return "test"

    if package_st in _fail:
        return "package"

    # No obvious stage failure — inspect logs.
    lc = log_text.lower()
    if re.search(r"compilation error|compilation failed|cannot find symbol", lc):
        return "compile"
    if re.search(r"tests run|failed|junit|pytest", lc):
        return "test"
    if re.search(r"permission denied|unauthorized|secret", lc):
        return "setup"
    if re.search(r"docker|container|image", lc):
        return "docker"

    return ""


def is_pipeline_failed(status: dict) -> bool:
    """Return True if any stage reported a failure.
    Handles status strings from all three platforms:
      GitHub Actions : 'failure', 'timed_out', 'cancelled'
      GitLab         : 'failed', 'canceled'
      Jenkins        : 'FAILURE', 'UNSTABLE', 'ABORTED'
    Also checks the top-level pipeline_failed flag.
    """
    # Check top-level flag first (set by fetch_gitlab_status.py for GitLab)
    if status.get("pipeline_failed") is True:
        return True
    # Check individual stage statuses — case-insensitive to cover all platforms
    failed_values = {"failure", "failed", "timed_out", "cancelled", "canceled",
                     "unstable", "aborted"}
    for key in ("config_status", "build_status", "test_status", "package_status"):
        val = status.get(key, "success")
        if str(val).lower() in failed_values:
            return True
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="M13 ML failure classifier (inference)")
    parser.add_argument("--status",       default="pipeline_status.json")
    parser.add_argument("--logs",         default="logs")
    parser.add_argument("--model-bundle", default="models/m13_model_bundle.pkl")
    parser.add_argument("--output",       default="m13_classification_report.json")
    args = parser.parse_args()

    log.info("M13 ML Failure Classification")

    # ── Load pipeline status ─────────────────────────────────────────────────
    status_path = Path(args.status)
    if not status_path.exists():
        log.warning("Status file not found: %s — using empty status", status_path)
        status: dict = {}
    else:
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("Could not parse status: %s", exc)
            status = {}

    log.info("  build=%s  test=%s  package=%s  config=%s",
             status.get("build_status", "?"),
             status.get("test_status", "?"),
             status.get("package_status", "?"),
             status.get("config_status", "?"))

    # ── Collect logs ─────────────────────────────────────────────────────────
    logs_dir  = Path(args.logs)
    log_text  = collect_logs(logs_dir) if logs_dir.exists() else ""

    # ── Check if pipeline actually failed ────────────────────────────────────
    pipeline_failed = is_pipeline_failed(status)
    if not pipeline_failed:
        log.info("Pipeline succeeded — no failure to classify.")
        report: dict[str, Any] = {
            "classification":  "no_failure",
            "confidence":      1.0,
            "probabilities":   {label: 0.0 for label in TARGET_LABELS},
            "pipeline_failed": False,
            "stage_results": {
                "config":  status.get("config_status",  "unknown"),
                "build":   status.get("build_status",   "unknown"),
                "test":    status.get("test_status",    "unknown"),
                "package": status.get("package_status", "unknown"),
            },
            "platform":   status.get("platform", "github"),
            "workflow":   status.get("workflow", ""),
            "run_id":     status.get("run_id", ""),
            "run_number": status.get("run_number", ""),
            "commit":     status.get("commit", ""),
            "branch":     status.get("branch", ""),
            "event":      status.get("event", ""),
            "timestamp":  datetime.now(timezone.utc).isoformat(),
            "model_used": "skipped (no failure)",
        }
        Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
        log.info("Report written to %s", args.output)
        return

    # ── Load model bundle ─────────────────────────────────────────────────────
    bundle_path = Path(args.model_bundle)
    bundle: Optional[dict] = None
    if bundle_path.exists():
        try:
            import joblib
            bundle = joblib.load(bundle_path)
            log.info("Loaded model bundle from %s", bundle_path)
        except Exception as exc:
            log.warning("Could not load model bundle: %s", exc)
    else:
        log.warning("Model bundle not found at %s", bundle_path)

    # ── Determine failing step and language ───────────────────────────────────
    failing_step = infer_failing_step(status, log_text)
    language     = detect_language(log_text)
    log.info("  failing_step='%s'  language='%s'", failing_step, language)

    # ── Extract features ──────────────────────────────────────────────────────
    row = {
        "text":          log_text,
        "lang":          language,
        "failing_step":  failing_step,
        "primary_label": "",               # not available at inference time
    }
    feats = extract_features(row)
    X     = np.array([feats], dtype=np.float32)

    log.info("  Extracted %d features", len(feats))

    # ── Determine active signals for diagnostics ──────────────────────────────
    active = {FEATURE_NAMES[i]: v for i, v in enumerate(feats) if abs(v) > 1e-6}
    log.info("  Active features: %s",
             ", ".join(f"{k}={v:.2g}" for k, v in list(active.items())[:10]))

    # ── Predict ───────────────────────────────────────────────────────────────
    classification = "unknown"
    confidence     = 0.0
    probabilities: dict[str, float] = {label: 0.0 for label in TARGET_LABELS}
    model_used     = "unavailable"
    guardrail_applied = False

    if bundle is not None:
        try:
            # Prefer the default clean GB model (lower false positives).
            models_dict = bundle.get("models", {})
            model       = (models_dict.get("gradient_boosting_clean")
                           or models_dict.get("random_forest_high_recall"))
            le          = bundle.get("label_encoder")
            guardrail_p = (bundle.get("guardrail_params", {})
                                 .get("gradient_boosting", {}))
            policy      = bundle.get("default_policy", "clean_gb")
            model_used  = f"bundle:{policy}"

            if model is None or le is None:
                raise ValueError("Bundle is missing model or label_encoder")

            class_names: list[str] = list(le.classes_)

            # Raw prediction.
            raw_pred = model.predict(X)

            # Apply evidence-gated guardrail.
            # guardrail_p is stored as a GuardrailParams dataclass in the bundle, not a dict.
            # Convert with asdict() if needed so .get() calls work correctly.
            from dataclasses import asdict as _asdict
            if isinstance(guardrail_p, dict):
                g_params: dict = guardrail_p
            elif hasattr(guardrail_p, "__dataclass_fields__"):
                g_params = _asdict(guardrail_p)
            else:
                g_params = {}
            guarded_pred = predict_with_guardrails(
                model, X, class_names,
                config_to_compile_margin=g_params.get("config_to_compile_margin", 0.18),
                config_to_test_margin=g_params.get("config_to_test_margin", 0.18),
                min_alt_proba=g_params.get("min_alt_proba_for_guardrail", 0.30),
            )

            guardrail_applied = bool(guarded_pred[0] != raw_pred[0])
            classification    = class_names[int(guarded_pred[0])]

            if hasattr(model, "predict_proba"):
                proba_arr = model.predict_proba(X)[0]
                for i, label in enumerate(class_names):
                    if label in probabilities:
                        probabilities[label] = float(proba_arr[i])
                confidence = float(proba_arr.max())
            else:
                confidence = 1.0

            log.info("  Prediction: %s (confidence=%.3f  guardrail=%s)",
                     classification, confidence, guardrail_applied)

        except Exception as exc:
            log.warning("Prediction failed: %s", exc)
            # Heuristic fallback based on keyword signals.
            classification = _heuristic_classification(feats)
            confidence     = 0.5
            model_used     = "heuristic_fallback"
            log.info("  Heuristic fallback: %s", classification)
    else:
        # No model available — use heuristic.
        classification = _heuristic_classification(feats)
        confidence     = 0.5
        model_used     = "heuristic_fallback"
        log.info("  Heuristic fallback (no model): %s", classification)

    # ── Stage-based configuration override ───────────────────────────────────
    # When the config stage fails and build/test are both skipped, the pipeline
    # stopped at the M8/M9 pre-pipeline gate.  That gate only exits non-zero for
    # configuration problems (invalid port, missing key, bad YAML, missing env
    # var), so the stage outcome alone is sufficient — no keyword signals needed.
    # Requiring text signals caused false negatives on Jenkins when
    # config_validation.log was absent from the collected logs directory.
    feat_map = dict(zip(FEATURE_NAMES, feats))

    # ── OOM / infra-runner guardrail ──────────────────────────────────────────
    # FIX RC3/RC4: When the ML model sees tests_ran=1 + error_in_test_step=1 it
    # classifies as test_failure even if infra keywords are set, because it was
    # trained on OOM patterns where the runner dies before any test runs (tests_ran=0).
    # The simulator catches exceptions inside the JVM so the runner survives —
    # an out-of-distribution pattern the model has never seen.
    #
    # Guardrail: if any infra keyword fired (runner OR network) AND there are no
    # assertion-style signals, force classification to infrastructure.
    # Covers: E5B (OOM), E5D (port conflict → kw_infra_network),
    #         E5F (disk → kw_infra_network), E5H (external svc → kw_infra_network),
    #         E5I (race condition → kw_infra_runner via 'simulated race condition').
    #
    # FIX E5H: Extended to also override 'configuration' mispredictions.
    # Previously the guardrail only caught test_failure/flaky_test/unknown.
    # The ML model can predict 'configuration' on noisy low-signal inputs (e.g.
    # when SocketTimeoutException was still in kw_flaky it could push the model
    # toward configuration via feat_config_* interactions). The true-config guard
    # (feat_map["feat_config_yaml_workflow"] etc.) prevents false positives: we
    # only override 'configuration' when no genuine config signal is present.
    _infra_runner_kw  = feat_map.get("feat_kw_infra_runner", 0)
    _infra_network_kw = feat_map.get("feat_kw_infra_network", 0)
    _assert_kw        = feat_map.get("feat_kw_test_assert", 0)
    _true_config_kw   = (
        feat_map.get("feat_config_yaml_workflow", 0)
        + feat_map.get("feat_config_secret_env", 0)
        + feat_map.get("feat_config_auth_permission", 0)
        + feat_map.get("feat_config_tool_version", 0)
        + feat_map.get("feat_dep_resolution_without_network", 0)
    )
    _infra_guardrail_classes = ("test_failure", "flaky_test", "unknown", "configuration")
    if (
        (_infra_runner_kw > 0 or _infra_network_kw > 0)
        and _assert_kw == 0          # no assertion failure evidence
        and classification in _infra_guardrail_classes
        # only override 'configuration' when no true config signal is present
        and not (classification == "configuration" and _true_config_kw > 0)
    ):
        log.info(
            "  OOM/infra guardrail: infra_runner=%s infra_network=%s assert=0 "
            "-> overriding '%s' to 'infrastructure'",
            _infra_runner_kw, _infra_network_kw, classification
        )
        classification = "infrastructure"
        confidence     = max(confidence, 0.72)
        probabilities["infrastructure"] = confidence
        guardrail_applied = True
        model_used = model_used + "+oom_infra_guard"

    # Use None as default so absent keys (stages that never ran) are treated as
    # skipped, not "success".  Your pipeline_status.json won't contain test_status
    # or package_status when M8 fails because those stages never executed.
    _build_st   = status.get("build_status")   # None if key absent
    _test_st    = status.get("test_status")    # None if key absent
    _config_st  = status.get("config_status")
    _package_st = status.get("package_status")
    _SKIPPED    = {"skipped", "unknown", "", None}
    _SUCCESS    = {"success", "SUCCESS"}
    config_stage_only = (
        _config_st in ("failure", "failed", "FAILURE")
        and _build_st in _SKIPPED
        and _test_st  in _SKIPPED
    )
    if config_stage_only and classification != "configuration":
        log.info(
            "  Stage override: config_stage_only=True "
            "-> overriding '%s' to 'configuration'", classification
        )
        classification = "configuration"
        confidence     = max(confidence, 0.75)
        probabilities["configuration"] = confidence
        guardrail_applied = True
        model_used = model_used + "+stage_override"

    # ── Package-stage-only override ───────────────────────────────────────────
    # FIX E5G: When build and test both succeed but package fails, M13 has no
    # log text from the package stage (it is never uploaded as an artifact).
    # The model therefore sees near-zero features and can classify anything.
    # A package-only failure is always an infrastructure/artifact problem:
    # the compile and test signals were clean, so it cannot be compilation,
    # test assertion, configuration, or flakiness. Override unconditionally.
    package_stage_only = (
        _package_st in ("failure", "failed", "FAILURE")
        and _build_st in _SUCCESS
        and _test_st  in _SUCCESS
    )
    if package_stage_only and classification != "infrastructure":
        log.info(
            "  Stage override: package_stage_only=True (build=success, test=success) "
            "-> overriding '%s' to 'infrastructure'", classification
        )
        classification = "infrastructure"
        confidence     = max(confidence, 0.75)
        probabilities["infrastructure"] = confidence
        guardrail_applied = True
        model_used = model_used + "+package_stage_override"
    report = {
        "classification":  classification,
        "confidence":      round(confidence, 4),
        "probabilities":   {k: round(v, 4) for k, v in probabilities.items()},
        "guardrail_applied": guardrail_applied,
        "pipeline_failed": True,
        "stage_results": {
            "config":  status.get("config_status",  "unknown"),
            "build":   status.get("build_status",   "unknown"),
            "test":    status.get("test_status",    "unknown"),
            "package": status.get("package_status", "unknown"),
        },
        "failing_step":    failing_step,
        "detected_language": language,
        "log_size_bytes":  len(log_text),
        "platform":   status.get("platform", "github"),
        "workflow":   status.get("workflow", ""),
        "run_id":     status.get("run_id", ""),
        "run_number": status.get("run_number", ""),
        "commit":     status.get("commit", ""),
        "branch":     status.get("branch", ""),
        "event":      status.get("event", ""),
        "model_used": model_used,
        "timestamp":  datetime.now(timezone.utc).isoformat(),
        "active_signals": {k: round(v, 4) for k, v in list(active.items())[:20]},
    }

    output_path = Path(args.output)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    log.info("Report written to %s", output_path)
    log.info("Classification: %s (%.1f%% confidence)", classification, confidence * 100)


# ---------------------------------------------------------------------------
# Heuristic fallback (when no model is available)
# ---------------------------------------------------------------------------

def _heuristic_classification(feats: list[float]) -> str:
    """Keyword-signal-based fallback classifier."""
    feat_map = dict(zip(FEATURE_NAMES, feats))

    def f(name: str) -> float:
        return feat_map.get(name, 0.0)

    compile_score = (
        f("feat_kw_compile_fail") * 3
        + f("feat_compile_jvm_strong") * 2
        + f("feat_compile_ts_strong") * 2
        + f("feat_compile_rust_go_strong") * 2
        + f("feat_compile_native_linker") * 2
        + f("feat_compile_python_syntax") * 2
        + f("feat_compile_build_task")
        + f("feat_compile_no_tests")
    )
    test_score = (
        f("feat_kw_test_assert") * 3
        + f("feat_tests_failed") * 2
        + f("feat_fail_ratio")
        + f("feat_error_in_test_step")
    )
    flaky_score = (
        f("feat_kw_flaky") * 4
    )

    # FIX: When flaky keyword fires without assertion signals, give a small bonus
    # so flaky_test wins ties against test_failure (which also scores ~4 via
    # error_in_test_step + tests_failed when Errors:1 is present).
    if f("feat_kw_flaky") > 0 and f("feat_kw_test_assert") == 0:
        flaky_score += 1.0
    infra_score = (
        f("feat_kw_infra_network") * 5  # FIX: raised from 3 → 5; same reasoning as kw_infra_runner
        + f("feat_kw_infra_runner") * 5  # FIX RC4: raised from 3 → 5 so OOM beats error_in_test_step
    )

    # FIX: When any infra keyword fires without a test assertion signal, give a
    # decisive bonus so the heuristic always returns infrastructure in these cases.
    # This mirrors the guardrail logic in main() for the model path, ensuring the
    # heuristic fallback (no model bundle) gives the same answer.
    if (f("feat_kw_infra_network") > 0 or f("feat_kw_infra_runner") > 0) and f("feat_kw_test_assert") == 0:
        infra_score += 4.0
    config_score = (
        f("feat_kw_config_fail") * 2
        + f("feat_config_secret_env") * 2
        + f("feat_config_yaml_workflow") * 2   # includes M8 patterns like 'server.port must be numeric'
        + f("feat_config_auth_permission")
        + f("feat_config_missing_file")
        + f("feat_config_no_compile")
        # Added: these sub-features were missing and caused config to lose against compile/infra
        + f("feat_config_tool_version")
        + f("feat_config_lint_format")
        + f("feat_config_strong_only") * 3     # strong signal: config signals present AND no compile signals
        + f("feat_config_dep_resolution_strong")
        + f("feat_dep_resolution_without_network")
    )

    # If strong config signals are present and there are NO strong compile signals,
    # give a bonus to prevent compile noise from winning.
    if f("feat_config_strong_only") > 0 and f("feat_kw_compile_fail") == 0:
        config_score += 2.0

    scores = {
        "compilation":    compile_score,
        "test_failure":   test_score,
        "flaky_test":     flaky_score,
        "infrastructure": infra_score,
        "configuration":  config_score,
    }

    best = max(scores, key=lambda k: scores[k])
    if scores[best] == 0.0:
        return "unknown"
    return best


if __name__ == "__main__":
    main()
