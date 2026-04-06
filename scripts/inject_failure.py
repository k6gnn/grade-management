#!/usr/bin/env python3
"""
Failure Injection Script
========================
Injects one of five failure scenarios into the Student Grade Management
pipeline for self-healing mechanism evaluation.

Usage:
    python inject_failure.py <failure_type>

Failure types:
    compilation     - Introduces a syntax error into StudentController.java
    test            - Introduces a failing assertion into StudentControllerTest.java
    flaky           - Introduces non-deterministic sleep into StudentServiceTest.java
    configuration   - Corrupts application.properties with an invalid value
    infrastructure  - Introduces a non-existent dependency into pom.xml

To restore the original state after injection, use:
    python inject_failure.py restore
"""

import sys
import os
import shutil
import re

# ─── File paths ───────────────────────────────────────────────────────────────

CONTROLLER      = "src/main/java/com/university/grades/controller/StudentController.java"
CONTROLLER_TEST = "src/test/java/com/university/grades/controller/StudentControllerTest.java"
SERVICE_TEST    = "src/test/java/com/university/grades/service/StudentServiceTest.java"
APP_PROPS       = "src/main/resources/application.properties"
POM_XML         = "pom.xml"

BACKUP_SUFFIX = ".backup"

# ─── Utilities ────────────────────────────────────────────────────────────────

def backup(path):
    """Create a backup of the original file before injection."""
    backup_path = path + BACKUP_SUFFIX
    if not os.path.exists(backup_path):
        shutil.copy2(path, backup_path)
        print(f"  Backed up: {path} -> {backup_path}")
    else:
        print(f"  Backup already exists: {backup_path}")


def restore(path):
    """Restore the original file from backup."""
    backup_path = path + BACKUP_SUFFIX
    if os.path.exists(backup_path):
        shutil.copy2(backup_path, path)
        os.remove(backup_path)
        print(f"  Restored: {backup_path} -> {path}")
    else:
        print(f"  No backup found for: {path}")


def read_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_file(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

# ─── Failure 1: Compilation Failure ───────────────────────────────────────────

def inject_compilation_failure():
    """
    Removes the closing brace of the getAllStudents method in
    StudentController.java, producing a Java compilation error.
    Failure category: Compilation
    Expected mechanism response: M1 (retry), M3 (notification + lockout)
    """
    print("\n[INJECT] Compilation failure -> StudentController.java")
    backup(CONTROLLER)

    content = read_file(CONTROLLER)

    injected = content.replace(
        "public ResponseEntity<List<Student>> getAllStudents() {",
        "public ResponseEntity<List<Student>> getAllStudents() {\n        String broken = \"unclosed string;"
    )

    if injected == content:
        print("  WARNING: Injection target not found. File may have changed.")
        return False

    write_file(CONTROLLER, injected)
    print("  Injected: unclosed string literal into getAllStudents()")
    print("  Expected build error: reached end of file while parsing")
    return True

# ─── Failure 2: Test Failure ───────────────────────────────────────────────────

def inject_test_failure():
    """
    Replaces a correct assertion value with an incorrect one in
    StudentControllerTest.java, causing a JUnit assertion failure.
    Failure category: Test failure
    Expected mechanism response: M4 (retry), M6 (trend analysis)
    """
    print("\n[INJECT] Test failure -> StudentControllerTest.java")
    backup(CONTROLLER_TEST)

    content = read_file(CONTROLLER_TEST)

    injected = content.replace(
        '.andExpect(jsonPath("$.length()").value(2))',
        '.andExpect(jsonPath("$.length()").value(999))'
    )

    if injected == content:
        print("  WARNING: Injection target not found. File may have changed.")
        return False

    write_file(CONTROLLER_TEST, injected)
    print("  Injected: incorrect assertion value (expected 999, actual 2)")
    print("  Expected test error: AssertionError in getAllStudents_shouldReturn200WithStudentList")
    return True

# ─── Failure 3: Flaky Test ────────────────────────────────────────────────────

def inject_flaky_test():
    """
    Introduces a non-deterministic Thread.sleep() into StudentServiceTest.java
    that RELIABLY exceeds Maven Surefire's per-test timeout, ensuring that:

      1. The failure actually occurs on every run (not just ~50% of the time).
      2. Maven Surefire emits "TestTimedOutException" in its .txt reports,
         which is the keyword anomaly_detection.py uses to classify the failure
         as FLAKY_TEST rather than the generic TEST_FAILURE category.

    Design rationale
    ────────────────
    The previous implementation used Math.random() * 2000 ms.  With a 1000 ms
    timeout that produced failures only ~50% of the time, so in the other 50%
    of pipeline runs the test passed, the pipeline reported success, and the
    anomaly detector never ran — making flaky detection effectively invisible.

    The fix uses a two-part sleep strategy that guarantees a timeout breach:
      • A base delay of 1 500 ms (above a typical 1 000 ms timeout).
      • An additional random jitter of 0–500 ms.

    This makes the sleep non-deterministic (realistic) while ensuring it
    ALWAYS exceeds a 1 000 ms timeout, so:
      • The test ALWAYS fails with TestTimedOutException.
      • The anomaly detector ALWAYS classifies it as flaky_test.
      • The thesis evaluation can observe the full M4→M5→M6 mechanism chain
        on every pipeline run.

    To reproduce a realistic intermittent flaky scenario (e.g. for trend
    analysis over multiple runs) adjust BASE_SLEEP_MS and JITTER_MS so that
    the sleep occasionally falls below the timeout threshold.

    Failure category: Flaky test
    Expected mechanism response: M4 (retry), M5 (quarantine), M6 (trend)
    """
    print("\n[INJECT] Flaky test -> StudentServiceTest.java")
    backup(SERVICE_TEST)

    content = read_file(SERVICE_TEST)

    # ── Injected Java block ───────────────────────────────────────────────────
    # BASE_SLEEP_MS = 1500  →  always exceeds a 1000 ms Surefire timeout.
    # JITTER_MS     = 500   →  adds non-determinism to look realistic.
    #
    # Maven Surefire will abort the test and write to its .txt report:
    #   "TestTimedOutException: test timed out after 1000 milliseconds"
    # That string is matched by anomaly_detection.py's flaky_test rule.
    # ─────────────────────────────────────────────────────────────────────────
    flaky_code = """\
        // ── INJECTED: Flaky sleep — always exceeds Surefire timeout ──────────
        // BASE_SLEEP_MS (1500) + random jitter (0-500) guarantees the sleep
        // breaches a 1000 ms per-test timeout on every run.
        // Maven Surefire will terminate the test and emit:
        //   TestTimedOutException: test timed out after 1000 milliseconds
        // which is the keyword anomaly_detection.py uses to classify this
        // failure as FLAKY_TEST rather than TEST_FAILURE.
        try {
            final long BASE_SLEEP_MS = 1500L;
            final long JITTER_MS     = (long)(Math.random() * 500);
            Thread.sleep(BASE_SLEEP_MS + JITTER_MS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        // ── END INJECTED ──────────────────────────────────────────────────────
"""

    injected = content.replace(
        "        List<Student> result = studentService.getAllStudents();",
        flaky_code + "        List<Student> result = studentService.getAllStudents();"
    )

    if injected == content:
        print("  WARNING: Injection target not found. File may have changed.")
        return False

    write_file(SERVICE_TEST, injected)
    print("  Injected: guaranteed-timeout sleep (1500–2000 ms) into getAllStudents test")
    print("  Expected behaviour: TestTimedOutException on every run")
    print("  Anomaly detector will classify as: FLAKY_TEST")
    return True

# ─── Failure 4: Configuration Failure ────────────────────────────────────────

def inject_configuration_failure():
    """
    Corrupts application.properties by setting an invalid value for
    server.port, causing the Spring Boot application context to fail
    to initialise during the test stage.
    Failure category: Configuration failure
    Expected mechanism response: M7 (rollback), M8 (validation gate), M9 (env verify)
    """
    print("\n[INJECT] Configuration failure -> application.properties")
    backup(APP_PROPS)

    content = read_file(APP_PROPS)

    injected = content.replace(
        "server.port=8080",
        "server.port=INVALID_PORT_VALUE"
    )

    if injected == content:
        print("  WARNING: Injection target not found. File may have changed.")
        return False

    write_file(APP_PROPS, injected)
    print("  Injected: invalid value 'INVALID_PORT_VALUE' for server.port")
    print("  Expected error: ApplicationContext failure — port must be a valid integer")
    return True

# ─── Failure 5: Infrastructure Failure ───────────────────────────────────────

def inject_infrastructure_failure():
    """
    Introduces a non-existent dependency into pom.xml, simulating
    the unavailability of an external Maven artifact repository and
    causing the build stage to fail with a dependency resolution error.
    Failure category: Infrastructure failure
    Expected mechanism response: M1 (retry), M10 (fresh container), M11 (cache invalidation)
    """
    print("\n[INJECT] Infrastructure failure -> pom.xml")
    backup(POM_XML)

    content = read_file(POM_XML)

    fake_dependency = """
        <!-- INJECTED: Non-existent dependency to simulate infrastructure failure -->
        <dependency>
            <groupId>com.nonexistent.library</groupId>
            <artifactId>does-not-exist</artifactId>
            <version>9.9.9</version>
        </dependency>
"""

    injected = content.replace(
        "    </dependencies>",
        fake_dependency + "    </dependencies>"
    )

    if injected == content:
        print("  WARNING: Injection target not found. File may have changed.")
        return False

    write_file(POM_XML, injected)
    print("  Injected: non-existent dependency com.nonexistent.library:does-not-exist:9.9.9")
    print("  Expected error: Could not resolve dependencies — artifact not found in repository")
    return True

# ─── Restore All ─────────────────────────────────────────────────────────────

def restore_all():
    """Restores all injected files to their original state from backups."""
    print("\n[RESTORE] Restoring all files to original state...")
    files = [CONTROLLER, CONTROLLER_TEST, SERVICE_TEST, APP_PROPS, POM_XML]
    for f in files:
        restore(f)
    print("\nAll files restored. Pipeline is back to clean baseline.")

# ─── Main ─────────────────────────────────────────────────────────────────────

FAILURE_TYPES = {
    "compilation":    inject_compilation_failure,
    "test":           inject_test_failure,
    "flaky":          inject_flaky_test,
    "configuration":  inject_configuration_failure,
    "infrastructure": inject_infrastructure_failure,
}

def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)

    failure_type = sys.argv[1].lower()

    if failure_type == "restore":
        restore_all()
        return

    if failure_type not in FAILURE_TYPES:
        print(f"ERROR: Unknown failure type '{failure_type}'")
        print(f"Valid types: {', '.join(FAILURE_TYPES.keys())}, restore")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  FAILURE INJECTION: {failure_type.upper()}")
    print(f"{'='*60}")

    success = FAILURE_TYPES[failure_type]()

    if success:
        print(f"\nInjection complete.")
        print(f"Commit and push to trigger the pipeline and observe self-healing.")
        print(f"To restore: python scripts/inject_failure.py restore")
    else:
        print(f"\nInjection failed — check the WARNING above.")

if __name__ == "__main__":
    main()
