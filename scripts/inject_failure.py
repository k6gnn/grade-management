#!/usr/bin/env python3
"""
Failure Injection Script
========================
Revised for thesis realism:
- flaky injection is truly intermittent instead of guaranteed failure,
- injection fails fast when the target line is not found,
- random timing hovers around the timeout threshold so CI can observe
  fail-then-pass behaviour across retries.
"""

import os
import random
import shutil
import sys

CONTROLLER = "src/main/java/com/university/grades/controller/StudentController.java"
CONTROLLER_TEST = "src/test/java/com/university/grades/controller/StudentControllerTest.java"
SERVICE_TEST = "src/test/java/com/university/grades/service/StudentServiceTest.java"
APP_PROPS = "src/main/resources/application.properties"
POM_XML = "pom.xml"
BACKUP_SUFFIX = ".backup"


def backup(path):
    backup_path = path + BACKUP_SUFFIX
    if not os.path.exists(backup_path):
        shutil.copy2(path, backup_path)
        print(f"  Backed up: {path} -> {backup_path}")
    else:
        print(f"  Backup already exists: {backup_path}")


def restore(path):
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


def replace_once_or_raise(content, needle, replacement, label):
    if needle not in content:
        raise RuntimeError(
            f"Injection target not found for {label}. Expected to find exact text:\n{needle}"
        )
    return content.replace(needle, replacement, 1)


def inject_compilation_failure():
    print("\n[INJECT] Compilation failure -> StudentController.java")
    backup(CONTROLLER)
    content = read_file(CONTROLLER)
    injected = replace_once_or_raise(
        content,
        "public ResponseEntity<List<Student>> getAllStudents() {",
        "public ResponseEntity<List<Student>> getAllStudents() {\n        String broken = \"unclosed string;",
        "compilation",
    )
    write_file(CONTROLLER, injected)
    print("  Injected: unclosed string literal into getAllStudents()")
    print("  Expected build error: reached end of file while parsing")
    return True


def inject_test_failure():
    print("\n[INJECT] Test failure -> StudentControllerTest.java")
    backup(CONTROLLER_TEST)
    content = read_file(CONTROLLER_TEST)
    injected = replace_once_or_raise(
        content,
        '.andExpect(jsonPath("$.length()").value(2))',
        '.andExpect(jsonPath("$.length()").value(999))',
        "test",
    )
    write_file(CONTROLLER_TEST, injected)
    print("  Injected: incorrect assertion value (expected 999, actual 2)")
    print("  Expected test error: AssertionError in getAllStudents_shouldReturn200WithStudentList")
    return True


def inject_flaky_test():
    print("\n[INJECT] Flaky test -> StudentServiceTest.java")
    backup(SERVICE_TEST)
    content = read_file(SERVICE_TEST)

    flaky_code = """\
        // ── INJECTED: Realistic flaky timing near timeout threshold ─────────
        // This sleep hovers around the usual 1000 ms timeout boundary.
        // Some runs stay under the limit, others exceed it.
        // That produces true fail-then-pass behaviour across retries.
        try {
            final long BASE_SLEEP_MS = 700L;
            final long JITTER_MS     = (long)(Math.random() * 700L);
            Thread.sleep(BASE_SLEEP_MS + JITTER_MS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        // ── END INJECTED ─────────────────────────────────────────────────────
"""

    injected = replace_once_or_raise(
        content,
        "        List<Student> result = studentService.getAllStudents();",
        flaky_code + "        List<Student> result = studentService.getAllStudents();",
        "flaky",
    )
    write_file(SERVICE_TEST, injected)
    print("  Injected: intermittent timing jitter (700–1400 ms) near timeout boundary")
    print("  Expected behaviour: some attempts pass, some attempts timeout")
    print("  Expected CI signature: failed on one attempt, passed on later retry")
    return True


def inject_configuration_failure():
    print("\n[INJECT] Configuration failure -> application.properties")
    backup(APP_PROPS)
    content = read_file(APP_PROPS)
    injected = replace_once_or_raise(
        content,
        "server.port=8080",
        "server.port=INVALID_PORT_VALUE",
        "configuration",
    )
    write_file(APP_PROPS, injected)
    print("  Injected: invalid value 'INVALID_PORT_VALUE' for server.port")
    print("  Expected error: ApplicationContext failure — port must be a valid integer")
    return True


def inject_infrastructure_failure():
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
    injected = replace_once_or_raise(
        content,
        "    </dependencies>",
        fake_dependency + "    </dependencies>",
        "infrastructure",
    )
    write_file(POM_XML, injected)
    print("  Injected: non-existent dependency com.nonexistent.library:does-not-exist:9.9.9")
    print("  Expected error: Could not resolve dependencies — artifact not found in repository")
    return True


def restore_all():
    print("\n[RESTORE] Restoring all files to original state...")
    for path in [CONTROLLER, CONTROLLER_TEST, SERVICE_TEST, APP_PROPS, POM_XML]:
        restore(path)
    print("\nAll files restored. Pipeline is back to clean baseline.")


FAILURE_TYPES = {
    "compilation": inject_compilation_failure,
    "test": inject_test_failure,
    "flaky": inject_flaky_test,
    "configuration": inject_configuration_failure,
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

    print(f"\n{'=' * 60}")
    print(f"  FAILURE INJECTION: {failure_type.upper()}")
    print(f"{'=' * 60}")

    try:
        FAILURE_TYPES[failure_type]()
    except RuntimeError as exc:
        print(f"\nInjection failed: {exc}")
        sys.exit(1)

    print("\nInjection complete.")
    print("Commit and push to trigger the pipeline and observe self-healing.")
    print("To restore: python scripts/inject_failure.py restore")


if __name__ == "__main__":
    main()
