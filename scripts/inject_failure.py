#!/usr/bin/env python3
"""
Failure Injection Script
========================
Injects failure scenarios into the Student Grade Management pipeline
for self-healing mechanism evaluation.

Usage:
    python inject_failure.py <failure_type>
    python inject_failure.py restore

Single-fault types (E1–E5):
    compilation     - Syntax error in StudentController.java
    test            - Wrong assertion value in StudentControllerTest.java
    flaky           - Stateful marker-file flakiness in StudentControllerTest.java
    configuration   - Invalid server.port in application.properties
    infrastructure  - Non-existent Maven dependency in pom.xml

Infrastructure variants (E5b–E5i):
    oom             - Memory exhaustion via -Xmx1m JVM flag
    network         - Network instability simulation (DNS failures)
    port_conflict   - Port conflict via duplicate server.port binding
    deadlock        - Deadlock / timeout via infinite-loop test
    disk            - Disk exhaustion via large file creation
    artifact        - Corrupted JAR artifact via invalid bytes in pom.xml plugin config
    external        - External service unavailable via unreachable URL in test
    race            - Race condition via shared mutable static state in test

Multi-cause pairs (E6, E7, E9–E12):
    compilation_configuration   - E6:  Compilation + Configuration
    flaky_infrastructure        - E7:  Flaky test + Infrastructure
    compilation_infrastructure  - E9:  Compilation + Infrastructure
    test_configuration          - E10: Test assertion + Configuration
    configuration_infrastructure - E11: Configuration + Infrastructure
    flaky_configuration         - E12: Flaky test + Configuration

Multi-cause triples/quad (E13–E15):
    compilation_test_infrastructure        - E13: Compilation + Test + Infrastructure
    test_configuration_infrastructure      - E14: Test + Configuration + Infrastructure
    all                                    - E15: Compilation + Test + Configuration + Infrastructure
"""

import sys
import os
import re
import shutil

# ─── File paths ───────────────────────────────────────────────────────────────

CONTROLLER      = "src/main/java/com/university/grades/controller/StudentController.java"
CONTROLLER_TEST = "src/test/java/com/university/grades/controller/StudentControllerTest.java"
SERVICE_TEST    = "src/test/java/com/university/grades/service/StudentServiceTest.java"
INFRA_SIMULATOR = "src/test/java/com/university/grades/infra/InfrastructureSimulatorTest.java"
APP_PROPS       = "src/main/resources/application.properties"
POM_XML         = "pom.xml"
JVM_CONFIG      = ".mvn/jvm.config"

BACKUP_SUFFIX = ".backup"

# Sentinel value used when inject creates a NEW file (so restore deletes it)
CREATED_SENTINEL = "__CREATED_BY_INJECTOR__"

# ─── Utilities ────────────────────────────────────────────────────────────────

def backup(path):
    """Create a backup of the original file before injection.

    FIX: Returns False (and prints a clear error) if the source file does not
    exist, rather than crashing with an unhandled FileNotFoundError.
    """
    if not os.path.exists(path):
        print(f"  ERROR: Cannot back up '{path}' — file does not exist.")
        return False
    backup_path = path + BACKUP_SUFFIX
    if not os.path.exists(backup_path):
        shutil.copy2(path, backup_path)
        print(f"  Backed up: {path} -> {backup_path}")
    else:
        print(f"  Backup already exists: {backup_path}")
    return True


def backup_new_file(path):
    """
    Mark a file that did not exist before injection, so restore() deletes it
    rather than trying to copy back a non-existent original.
    """
    backup_path = path + BACKUP_SUFFIX
    if not os.path.exists(backup_path):
        with open(backup_path, "w") as f:
            f.write(CREATED_SENTINEL)
        print(f"  Marked new file for deletion on restore: {path}")


def restore(path):
    """Restore the original file from backup, or delete it if it was created by injector."""
    backup_path = path + BACKUP_SUFFIX
    if os.path.exists(backup_path):
        with open(backup_path, "r", errors="ignore") as f:
            content = f.read()
        if content.strip() == CREATED_SENTINEL:
            if os.path.exists(path):
                os.remove(path)
                print(f"  Deleted injector-created file: {path}")
            os.remove(backup_path)
        else:
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


def _append_test_method(content, method_code):
    """
    Insert method_code before the last closing brace of a Java test class.

    FIX: Replaces the fragile content.rstrip().endswith('}') pattern used
    throughout the original. That pattern silently corrupts files that have
    trailing whitespace or comments after the final brace. This helper
    finds the last '}' in the file regardless of trailing whitespace.
    """
    # Find the position of the last closing brace
    last_brace = content.rfind("}")
    if last_brace == -1:
        return None, "Could not find closing brace of test class."
    return content[:last_brace] + method_code + "\n}", None


# ═══════════════════════════════════════════════════════════════════════════════
# SINGLE-FAULT INJECTIONS (E1–E5) — ORIGINAL, UNCHANGED
# ═══════════════════════════════════════════════════════════════════════════════

# ─── Failure 1: Compilation Failure (E1) ──────────────────────────────────────

def inject_compilation_failure():
    """
    Removes the closing brace of the getAllStudents method in
    StudentController.java, producing a Java compilation error.
    Failure category: Compilation
    Expected mechanism response: M1 (retry), M3 (notification + lockout)
    """
    print("\n[INJECT] Compilation failure -> StudentController.java")
    if not backup(CONTROLLER):
        return False

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

# ─── Failure 2: Test Failure (E2) ────────────────────────────────────────────

def inject_test_failure():
    """
    Replaces a correct assertion value with an incorrect one in
    StudentControllerTest.java, causing a JUnit assertion failure.
    Failure category: Test failure
    Expected mechanism response: M4 (retry), M6 (trend analysis)
    """
    print("\n[INJECT] Test failure -> StudentControllerTest.java")
    if not backup(CONTROLLER_TEST):
        return False

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

# ─── Failure 3: Flaky Test (E3) ──────────────────────────────────────────────

def inject_flaky_test():
    """
    Introduces stateful marker-file flakiness into the
    getAllStudents_shouldReturn200WithStudentList test.
    Failure category: Flaky test
    Expected mechanism response: M4 (retry), M5 (quarantine), M6 (trend)
    """
    print("\n[INJECT] Flaky test -> StudentControllerTest.java")
    if not backup(CONTROLLER_TEST):
        return False

    content = read_file(CONTROLLER_TEST)

    guard_block = """\
        // INJECTED: Stateful marker-file flakiness (Experiment 3)
        java.io.File _flaky_marker = new java.io.File(
                System.getProperty("java.io.tmpdir"), "flaky_marker.tmp");
        if (!_flaky_marker.exists()) {
            try { _flaky_marker.createNewFile(); } catch (java.io.IOException _ignored) {}
            throw new RuntimeException(
                "Simulated transient failure: cold-start instability detected");
        }
        // END INJECTED
"""

    pattern = (
        r"(@Test\s*\n"
        r"\s*void getAllStudents_shouldReturn200WithStudentList\(\)"
        r"(?:\s*throws\s+\w+(?:\s*,\s*\w+)*)?"
        r"\s*\{)"
    )

    def replacer(m):
        return m.group(1) + "\n" + guard_block

    injected, n = re.subn(pattern, replacer, content, flags=re.DOTALL)

    if n == 0:
        print("  WARNING: Injection target not found. File may have changed.")
        return False

    write_file(CONTROLLER_TEST, injected)
    print("  Injected: marker-file guard into getAllStudents_shouldReturn200WithStudentList")
    print("  Expected behaviour: RuntimeException on attempt 1, passes on attempt 2")
    return True

# ─── Failure 3b: Flaky Test — GitLab/Jenkins variant (E3) ────────────────────

def inject_flaky_test_gitlab():
    """
    GitLab/Jenkins-compatible flaky test injection.
    Uses target/ directory which persists across retries within the same job.
    Failure category: Flaky test
    Expected mechanism response: M4 (retry), M5 (quarantine), M6 (trend)
    """
    print("\n[INJECT] Flaky test (GitLab/Jenkins variant) -> StudentControllerTest.java")
    if not backup(CONTROLLER_TEST):
        return False

    content = read_file(CONTROLLER_TEST)

    guard_block = """\
        // INJECTED: Workspace-based flakiness (GitLab/Jenkins variant, Experiment 3)
        java.io.File _targetDir = new java.io.File("target");
        if (!_targetDir.exists()) { _targetDir.mkdirs(); }
        java.io.File _flaky_marker = new java.io.File("target", "flaky_retry_count.tmp");
        if (!_flaky_marker.exists()) {
            try { _flaky_marker.createNewFile(); } catch (java.io.IOException _ignored) {}
            throw new RuntimeException(
                "Simulated transient failure: cold-start instability detected (workspace marker)");
        }
        // END INJECTED
"""

    pattern = (
        r"(@Test\s*\n"
        r"\s*void getAllStudents_shouldReturn200WithStudentList\(\)"
        r"(?:\s*throws\s+\w+(?:\s*,\s*\w+)*)?"
        r"\s*\{)"
    )

    def replacer(m):
        return m.group(1) + "\n" + guard_block

    injected, n = re.subn(pattern, replacer, content, flags=re.DOTALL)

    if n == 0:
        print("  WARNING: Injection target not found. File may have changed.")
        return False

    write_file(CONTROLLER_TEST, injected)
    print("  Injected: workspace marker-file guard into getAllStudents_shouldReturn200WithStudentList")
    print("  Expected behaviour: RuntimeException on attempt 1, passes on attempt 2")
    print("  NOTE: Uses target/ directory — persists across retries on GitLab and Jenkins")
    return True

# ─── Failure 4: Configuration Failure (E4) ───────────────────────────────────

def inject_configuration_failure():
    """
    Corrupts application.properties with an invalid server.port value.
    Failure category: Configuration failure
    Expected mechanism response: M7 (rollback), M8 (validation gate), M9 (env verify)
    """
    print("\n[INJECT] Configuration failure -> application.properties")
    if not backup(APP_PROPS):
        return False

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

# ─── Failure 5: Infrastructure Failure (E5) ──────────────────────────────────

def inject_infrastructure_failure():
    """
    Introduces a non-existent Maven dependency into pom.xml.
    Failure category: Infrastructure failure
    Expected mechanism response: M1 (retry), M10 (fresh container), M11 (cache invalidation)
    """
    print("\n[INJECT] Infrastructure failure -> pom.xml")
    if not backup(POM_XML):
        return False

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

# ═══════════════════════════════════════════════════════════════════════════════
# INFRASTRUCTURE VARIANTS (E5b–E5i)
#
# All eight variants are implemented as @Test methods inside
# InfrastructureSimulatorTest.java.  That class has NO Spring context, so
# the full JVM heap is available (critical for OOM), and each test runs
# without web-layer overhead competing for resources.
#
# Every method in the simulator is guarded by:
#   assumeTrue("E5x".equals(ACTIVE_EXPERIMENT))
# so on a clean baseline ALL tests are skipped and the pipeline passes.
#
# Injection: one string replacement — ACTIVE_EXPERIMENT = "NONE" -> "E5B" etc.
# Restore  : the backup/restore mechanism handles the whole file as usual.
# ═══════════════════════════════════════════════════════════════════════════════

# Sentinel: the exact Java constant declaration line in InfrastructureSimulatorTest.
# Must match the static field line precisely — the Javadoc also contains
# ACTIVE_EXPERIMENT = "NONE" in examples, so we anchor to the full declaration.
_SIMULATOR_NONE = '    static final String ACTIVE_EXPERIMENT = "NONE";'


def _inject_simulator(experiment_id: str) -> bool:
    """
    Enable exactly one experiment in InfrastructureSimulatorTest by replacing
    the ACTIVE_EXPERIMENT constant value.  A single string replacement is the
    only change made to the file — no code is appended or deleted.
    """
    exp = experiment_id.upper()
    print(f"\n[INJECT] InfrastructureSimulatorTest -> ACTIVE_EXPERIMENT = {exp!r}")

    if not backup(INFRA_SIMULATOR):
        return False

    content = read_file(INFRA_SIMULATOR)
    target = f'    static final String ACTIVE_EXPERIMENT = "{exp}";'

    if _SIMULATOR_NONE not in content:
        print(f"  WARNING: clean sentinel not found in simulator file.")
        print(f"  Expected: {_SIMULATOR_NONE!r}")
        print("  File may already have an active experiment or have been modified.")
        return False

    injected = content.replace(_SIMULATOR_NONE, target, 1)
    write_file(INFRA_SIMULATOR, injected)
    print(f"  Enabled: {exp} test in InfrastructureSimulatorTest.java")
    return True


# ─── E5b: Memory Exhaustion (OOM) ────────────────────────────────────────────

def inject_oom():
    """
    Enables the OOM test in InfrastructureSimulatorTest.
    The test allocates 64 MB chunks then 1 MB chunks until OutOfMemoryError.
    No Spring context runs, so the full JVM heap is available.
    Failure category: Infrastructure
    Expected mechanism response: M1 (retry), M10 (fresh container)
    """
    return _inject_simulator("E5B")


# ─── E5c: Network Instability ─────────────────────────────────────────────────

def inject_network():
    """
    Enables the network instability test in InfrastructureSimulatorTest.
    The test resolves a .invalid TLD host — guaranteed UnknownHostException.
    Failure category: Infrastructure
    Expected mechanism response: M1 (retry), M10 (fresh container)
    """
    return _inject_simulator("E5C")


# ─── E5d: Port Conflict ───────────────────────────────────────────────────────

def inject_port_conflict():
    """
    Enables the port conflict test in InfrastructureSimulatorTest.
    Uses ServerSocket(0) to get a free OS-assigned port, then binds it twice.
    Failure category: Infrastructure
    Expected mechanism response: M10 (fresh container)
    """
    return _inject_simulator("E5D")


# ─── E5e: Deadlock / Timeout ──────────────────────────────────────────────────

def inject_deadlock():
    """
    Enables the deadlock test in InfrastructureSimulatorTest.
    The test spins in an infinite loop; JUnit @Timeout(30s) interrupts it.
    Failure category: Infrastructure
    Expected mechanism response: M12 (timeout cancellation)
    """
    return _inject_simulator("E5E")


# ─── E5f: Disk Exhaustion ────────────────────────────────────────────────────

def inject_disk():
    """
    Enables the disk exhaustion test in InfrastructureSimulatorTest.
    Queries actual usable /tmp space, writes that amount + 64 MB with sync().
    Failure category: Infrastructure
    Expected mechanism response: M10 (fresh container), M12 (timeout fallback)
    """
    return _inject_simulator("E5F")


# ─── E5g: Corrupted Artifact ──────────────────────────────────────────────────

def inject_artifact():
    """
    Injects a maven-antrun-plugin into pom.xml that deletes target/ during
    the package phase so no JAR is produced.
    This one stays in pom.xml because it targets the Package stage, not the
    Test stage, and cannot be expressed as a JUnit test method.
    Failure category: Infrastructure
    Expected mechanism response: M7 (rollback), M10 (fresh container)
    """
    print("\n[INJECT] Corrupted artifact -> pom.xml")
    if not backup(POM_XML):
        return False

    content = read_file(POM_XML)

    corrupt_plugin = """
            <!-- INJECTED: Corrupted artifact simulation (E5g) -->
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-antrun-plugin</artifactId>
                <executions>
                    <execution>
                        <id>corrupt-artifact</id>
                        <phase>package</phase>
                        <goals><goal>run</goal></goals>
                        <configuration>
                            <target>
                                <delete dir="target" includeemptydirs="true" quiet="true"/>
                                <echo message="INJECTED: e5g corrupted artifact — no JAR artifact found in target/ (target directory deleted to simulate corrupted artifact)"/>
                            </target>
                        </configuration>
                    </execution>
                </executions>
            </plugin>
"""

    target = "        </plugins>"
    if target not in content:
        print("  WARNING: '</plugins>' target not found in pom.xml. File may have changed.")
        return False

    injected = content.replace(target, corrupt_plugin + target, 1)
    if injected == content:
        print("  WARNING: Injection target not found. File may have changed.")
        return False

    write_file(POM_XML, injected)
    print("  Injected: antrun plugin deletes target/ during package phase into pom.xml")
    print("  Expected error: 'No JAR artifact found in target/' in pipeline package stage")
    return True


# ─── E5h: External Service Unavailable ───────────────────────────────────────

def inject_external():
    """
    Enables the external service unavailability test in InfrastructureSimulatorTest.
    Connects to 192.0.2.1:9999 (RFC 5737 TEST-NET), catches IOException.
    Failure category: Infrastructure
    Expected mechanism response: M1 (retry), M10 (fresh container)
    """
    return _inject_simulator("E5H")


# ─── E5i: Race Condition ──────────────────────────────────────────────────────

def inject_race():
    """
    Enables the race condition test in InfrastructureSimulatorTest.
    100 threads increment a non-atomic counter; fallback throw guarantees failure.
    Failure category: Infrastructure
    Expected mechanism response: M4 (retry), M5 (flaky detection)
    """
    return _inject_simulator("E5I")


# ═══════════════════════════════════════════════════════════════════════════════
# MULTI-CAUSE PAIRS (E6, E7, E9–E12)
# Each function calls existing single injectors in sequence.
# Both are always attempted; the return value reflects both results.
# A single `restore` cleans all touched files.
# ═══════════════════════════════════════════════════════════════════════════════

def inject_compilation_configuration():
    """E6: Compilation + Configuration"""
    print("\n[INJECT] Multi-cause: Compilation + Configuration (E6)")
    ok1 = inject_compilation_failure()
    ok2 = inject_configuration_failure()
    return ok1 and ok2

def inject_flaky_infrastructure():
    """E7: Flaky test + Infrastructure"""
    print("\n[INJECT] Multi-cause: Flaky test + Infrastructure (E7)")
    ok1 = inject_flaky_test()
    ok2 = inject_infrastructure_failure()
    return ok1 and ok2

def inject_flaky_infrastructure_gitlab():
    """E7 GitLab/Jenkins variant: Flaky test (workspace) + Infrastructure
    FIX: Was a lambda using short-circuit 'and', meaning inject_infrastructure_failure()
    was silently skipped if inject_flaky_test_gitlab() returned False.
    Now a proper function so both injectors always run.
    """
    print("\n[INJECT] Multi-cause: Flaky test (GitLab/Jenkins) + Infrastructure (E7 gitlab)")
    ok1 = inject_flaky_test_gitlab()
    ok2 = inject_infrastructure_failure()
    return ok1 and ok2

def inject_compilation_infrastructure():
    """E9: Compilation + Infrastructure"""
    print("\n[INJECT] Multi-cause: Compilation + Infrastructure (E9)")
    ok1 = inject_compilation_failure()
    ok2 = inject_infrastructure_failure()
    return ok1 and ok2

def inject_test_configuration():
    """E10: Test assertion + Configuration"""
    print("\n[INJECT] Multi-cause: Test assertion + Configuration (E10)")
    ok1 = inject_test_failure()
    ok2 = inject_configuration_failure()
    return ok1 and ok2

def inject_configuration_infrastructure():
    """E11: Configuration + Infrastructure"""
    print("\n[INJECT] Multi-cause: Configuration + Infrastructure (E11)")
    ok1 = inject_configuration_failure()
    ok2 = inject_infrastructure_failure()
    return ok1 and ok2

def inject_flaky_configuration():
    """E12: Flaky test + Configuration"""
    print("\n[INJECT] Multi-cause: Flaky test + Configuration (E12)")
    ok1 = inject_flaky_test()
    ok2 = inject_configuration_failure()
    return ok1 and ok2

def inject_flaky_configuration_gitlab():
    """E12 GitLab/Jenkins variant: Flaky test (workspace) + Configuration
    FIX: Was a lambda using short-circuit 'and'. Now a proper function.
    """
    print("\n[INJECT] Multi-cause: Flaky test (GitLab/Jenkins) + Configuration (E12 gitlab)")
    ok1 = inject_flaky_test_gitlab()
    ok2 = inject_configuration_failure()
    return ok1 and ok2

# ═══════════════════════════════════════════════════════════════════════════════
# MULTI-CAUSE TRIPLES + QUAD (E13–E15)
# ═══════════════════════════════════════════════════════════════════════════════

def inject_compilation_test_infrastructure():
    """E13: Compilation + Test + Infrastructure"""
    print("\n[INJECT] Multi-cause triple: Compilation + Test + Infrastructure (E13)")
    ok1 = inject_compilation_failure()
    ok2 = inject_test_failure()
    ok3 = inject_infrastructure_failure()
    return ok1 and ok2 and ok3

def inject_test_configuration_infrastructure():
    """E14: Test + Configuration + Infrastructure"""
    print("\n[INJECT] Multi-cause triple: Test + Configuration + Infrastructure (E14)")
    ok1 = inject_test_failure()
    ok2 = inject_configuration_failure()
    ok3 = inject_infrastructure_failure()
    return ok1 and ok2 and ok3

def inject_all():
    """E15: All four — Compilation + Test + Configuration + Infrastructure"""
    print("\n[INJECT] Multi-cause quad: ALL (Compilation + Test + Configuration + Infrastructure) (E15)")
    ok1 = inject_compilation_failure()
    ok2 = inject_test_failure()
    ok3 = inject_configuration_failure()
    ok4 = inject_infrastructure_failure()
    return ok1 and ok2 and ok3 and ok4

# ═══════════════════════════════════════════════════════════════════════════════
# RESTORE ALL
# ═══════════════════════════════════════════════════════════════════════════════

def restore_all():
    """Restores all injected files to their original state from backups."""
    print("\n[RESTORE] Restoring all files to original state...")
    files = [CONTROLLER, CONTROLLER_TEST, SERVICE_TEST, INFRA_SIMULATOR, APP_PROPS, POM_XML, JVM_CONFIG]
    for f in files:
        restore(f)
    print("\nAll files restored. Pipeline is back to clean baseline.")

# ═══════════════════════════════════════════════════════════════════════════════
# FAILURE_TYPES REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════

FAILURE_TYPES = {
    # ── Single-fault (E1–E5) ──────────────────────────────────────────────────
    "compilation":    inject_compilation_failure,
    "test":           inject_test_failure,
    "flaky":          inject_flaky_test,
    "flaky_gitlab":   inject_flaky_test_gitlab,
    "configuration":  inject_configuration_failure,
    "infrastructure": inject_infrastructure_failure,

    # ── Infrastructure variants (E5b–E5i) ─────────────────────────────────────
    "oom":            inject_oom,
    "network":        inject_network,
    "port_conflict":  inject_port_conflict,
    "deadlock":       inject_deadlock,
    "disk":           inject_disk,
    "artifact":       inject_artifact,
    "external":       inject_external,
    "race":           inject_race,

    # ── Multi-cause pairs (E6, E7, E9–E12) ────────────────────────────────────
    "compilation_configuration":    inject_compilation_configuration,
    "flaky_infrastructure":         inject_flaky_infrastructure,
    "flaky_infrastructure_gitlab":  inject_flaky_infrastructure_gitlab,  # FIX: was lambda
    "compilation_infrastructure":   inject_compilation_infrastructure,
    "test_configuration":           inject_test_configuration,
    "configuration_infrastructure": inject_configuration_infrastructure,
    "flaky_configuration":          inject_flaky_configuration,
    "flaky_configuration_gitlab":   inject_flaky_configuration_gitlab,   # FIX: was lambda

    # ── Multi-cause triples + quad (E13–E15) ──────────────────────────────────
    "compilation_test_infrastructure":       inject_compilation_test_infrastructure,
    "test_configuration_infrastructure":     inject_test_configuration_infrastructure,
    "all":                                   inject_all,
}

# ─── Main ─────────────────────────────────────────────────────────────────────

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
        print(f"Valid types:\n  {chr(10).join(FAILURE_TYPES.keys())}\n  restore")
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
        # FIX: exit with code 1 on injection failure so run_experiment.ps1
        # can detect the problem and abort instead of pushing a broken state.
        print(f"\nInjection failed — check the WARNING above.")
        sys.exit(1)

if __name__ == "__main__":
    main()
