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
APP_PROPS       = "src/main/resources/application.properties"
POM_XML         = "pom.xml"
JVM_CONFIG      = ".mvn/jvm.config"

BACKUP_SUFFIX = ".backup"

# Sentinel value used when inject creates a NEW file (so restore deletes it)
CREATED_SENTINEL = "__CREATED_BY_INJECTOR__"

# ─── Utilities ────────────────────────────────────────────────────────────────

def backup(path):
    """Create a backup of the original file before injection."""
    backup_path = path + BACKUP_SUFFIX
    if not os.path.exists(backup_path):
        shutil.copy2(path, backup_path)
        print(f"  Backed up: {path} -> {backup_path}")
    else:
        print(f"  Backup already exists: {backup_path}")


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

# ─── Failure 2: Test Failure (E2) ────────────────────────────────────────────

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

# ─── Failure 3: Flaky Test (E3) ──────────────────────────────────────────────

def inject_flaky_test():
    """
    Introduces stateful marker-file flakiness into the
    getAllStudents_shouldReturn200WithStudentList test.
    Failure category: Flaky test
    Expected mechanism response: M4 (retry), M5 (quarantine), M6 (trend)
    """
    print("\n[INJECT] Flaky test -> StudentControllerTest.java")
    backup(CONTROLLER_TEST)

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

    The original marker-file approach uses /tmp which is wiped between
    retries in GitLab (fresh container per retry) and Jenkins (clean
    workspace per retry). This variant writes the marker to the project
    workspace directory (target/), which persists across retries within
    the same job on both platforms.

    Uses an atomic retry counter:
      - target/flaky_retry_count.tmp does not exist -> attempt 1 -> fail
      - file exists -> attempt 2+ -> pass

    Failure category: Flaky test
    Expected mechanism response: M4 (retry), M5 (quarantine), M6 (trend)
    """
    print("\n[INJECT] Flaky test (GitLab/Jenkins variant) -> StudentControllerTest.java")
    backup(CONTROLLER_TEST)

    content = read_file(CONTROLLER_TEST)

    guard_block = """\
        // INJECTED: Workspace-based flakiness (GitLab/Jenkins variant, Experiment 3)
        // Uses target/ directory which persists across retries in the same job,
        // unlike /tmp which is wiped between retries in fresh-container platforms.
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

def inject_configuration_failure():
    """
    Corrupts application.properties with an invalid server.port value.
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

# ─── Failure 5: Infrastructure Failure (E5) ──────────────────────────────────

def inject_infrastructure_failure():
    """
    Introduces a non-existent Maven dependency into pom.xml.
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

# ═══════════════════════════════════════════════════════════════════════════════
# INFRASTRUCTURE VARIANTS (E5b–E5i)
# ═══════════════════════════════════════════════════════════════════════════════

# ─── E5b: Memory Exhaustion (OOM) ────────────────────────────────────────────

def inject_oom():
    """
    Creates .mvn/jvm.config with -Xmx1m to force an OutOfMemoryError
    during Maven/JVM startup.
    Failure category: Infrastructure
    Expected mechanism response: M1 (retry), M10 (fresh container)
    """
    print("\n[INJECT] OOM — Memory exhaustion -> .mvn/jvm.config")

    os.makedirs(".mvn", exist_ok=True)

    if os.path.exists(JVM_CONFIG):
        backup(JVM_CONFIG)
    else:
        # File did not exist — mark for deletion on restore
        backup_new_file(JVM_CONFIG)

    write_file(JVM_CONFIG, "-Xmx1m\n")
    print("  Injected: -Xmx1m JVM heap limit into .mvn/jvm.config")
    print("  Expected error: java.lang.OutOfMemoryError during Maven build/test phase")
    return True

# ─── E5c: Network Instability ─────────────────────────────────────────────────

def inject_network():
    """
    Injects a test that attempts to resolve an unreachable host with a
    very short timeout, simulating network instability / DNS failure.
    Failure category: Infrastructure
    Expected mechanism response: M1 (retry), M10 (fresh container)
    """
    print("\n[INJECT] Network instability -> StudentControllerTest.java")
    backup(CONTROLLER_TEST)

    content = read_file(CONTROLLER_TEST)

    network_test = """
    @Test
    void simulateNetworkInstability_shouldFailOnUnreachableHost() throws Exception {
        // INJECTED: Simulates network instability (E5c)
        // Attempts to connect to an unresolvable host — will throw UnknownHostException.
        try {
            java.net.InetAddress.getByName("this.host.does.not.exist.invalid");
            throw new AssertionError(
                "Expected UnknownHostException but no exception was thrown — network injection failed");
        } catch (java.net.UnknownHostException e) {
            throw new RuntimeException(
                "Simulated network instability: DNS resolution failed — " + e.getMessage(), e);
        }
    }
"""

    # Insert before the last closing brace of the class
    injected = content.rstrip()
    if not injected.endswith("}"):
        print("  WARNING: Could not find closing brace of test class.")
        return False

    injected = injected[:-1] + network_test + "\n}"
    write_file(CONTROLLER_TEST, injected)
    print("  Injected: network instability test (UnknownHostException) into StudentControllerTest.java")
    print("  Expected error: RuntimeException — Simulated network instability: DNS resolution failed")
    return True

# ─── E5d: Port Conflict ───────────────────────────────────────────────────────

def inject_port_conflict():
    """
    Sets server.port=0 in application.properties AND injects a test
    that explicitly binds to port 8080 first, then attempts to bind again,
    simulating a port conflict / EADDRINUSE condition.
    Failure category: Infrastructure
    Expected mechanism response: M8 (config gate catches port=0), M10 (fresh container)

    Note: port=0 lets Spring pick a random port, so the ApplicationContext
    starts. The test itself then simulates the conflict by attempting a
    duplicate socket bind on a fixed port.
    """
    print("\n[INJECT] Port conflict -> StudentControllerTest.java")
    backup(CONTROLLER_TEST)

    content = read_file(CONTROLLER_TEST)

    port_test = """
    @Test
    void simulatePortConflict_shouldFailOnDuplicateBinding() throws Exception {
        // INJECTED: Simulates port conflict (E5d)
        // Binds a ServerSocket to a port, then tries to bind again — EADDRINUSE.
        java.net.ServerSocket firstSocket = null;
        java.net.ServerSocket secondSocket = null;
        try {
            firstSocket = new java.net.ServerSocket(19876);
            secondSocket = new java.net.ServerSocket(19876);  // duplicate — should throw
            throw new AssertionError(
                "Expected BindException but no exception was thrown — port conflict injection failed");
        } catch (java.io.IOException e) {
            throw new RuntimeException(
                "Simulated port conflict: address already in use — " + e.getMessage(), e);
        } finally {
            if (firstSocket != null) try { firstSocket.close(); } catch (Exception ignored) {}
            if (secondSocket != null) try { secondSocket.close(); } catch (Exception ignored) {}
        }
    }
"""

    injected = content.rstrip()
    if not injected.endswith("}"):
        print("  WARNING: Could not find closing brace of test class.")
        return False

    injected = injected[:-1] + port_test + "\n}"
    write_file(CONTROLLER_TEST, injected)
    print("  Injected: port conflict test (duplicate ServerSocket bind) into StudentControllerTest.java")
    print("  Expected error: RuntimeException — Simulated port conflict: address already in use")
    return True

# ─── E5e: Deadlock / Timeout ──────────────────────────────────────────────────

def inject_deadlock():
    """
    Injects a test that enters an infinite loop, causing the test job
    to exceed TEST_TIMEOUT_MINUTES and be cancelled by M12.
    Failure category: Infrastructure
    Expected mechanism response: M12 (timeout cancellation)
    """
    print("\n[INJECT] Deadlock / timeout -> StudentControllerTest.java")
    backup(CONTROLLER_TEST)

    content = read_file(CONTROLLER_TEST)

    deadlock_test = """
    @Test
    @org.junit.jupiter.api.Timeout(value = 30, unit = java.util.concurrent.TimeUnit.SECONDS)
    void simulateDeadlock_shouldTimeoutAndFail() throws Exception {
        // INJECTED: Simulates deadlock / infinite-loop timeout (E5e)
        // Spins indefinitely — JUnit @Timeout or M12 pipeline timeout will interrupt.
        long start = System.currentTimeMillis();
        while (true) {
            // Busy-wait to simulate a deadlocked thread
            if (System.currentTimeMillis() - start > 60_000) {
                // Safety valve: give up after 60 s if JUnit timeout is not configured
                throw new RuntimeException(
                    "Simulated deadlock: thread did not terminate within expected time");
            }
            Thread.sleep(100);
        }
    }
"""

    injected = content.rstrip()
    if not injected.endswith("}"):
        print("  WARNING: Could not find closing brace of test class.")
        return False

    injected = injected[:-1] + deadlock_test + "\n}"
    write_file(CONTROLLER_TEST, injected)
    print("  Injected: infinite-loop test into StudentControllerTest.java")
    print("  Expected error: JUnit TimeoutException or M12 pipeline timeout cancellation")
    return True

# ─── E5f: Disk Exhaustion ────────────────────────────────────────────────────

def inject_disk():
    """
    Injects a test that writes a large number of bytes to the temp
    directory to simulate disk exhaustion.
    On GitHub Actions runners (~14 GB free) this will not actually fill
    the disk, but it will write ~2 GB and then assert that it succeeded,
    causing a realistic slow-then-fail scenario.

    For a deterministic failure we instead write until IOException
    (or a quota limit) and throw; if the disk does not fill, we still
    throw to guarantee pipeline failure.
    Failure category: Infrastructure
    Expected mechanism response: M12 (timeout), M10 (fresh container)
    """
    print("\n[INJECT] Disk exhaustion -> StudentControllerTest.java")
    backup(CONTROLLER_TEST)

    content = read_file(CONTROLLER_TEST)

    disk_test = """
    @Test
    void simulateDiskExhaustion_shouldFailWithIOException() throws Exception {
        // INJECTED: Simulates disk exhaustion (E5f)
        // Writes large chunks to a temp file until IOException or 4 GB.
        java.io.File tmpFile = java.io.File.createTempFile("disk_exhaust_", ".tmp");
        tmpFile.deleteOnExit();
        long bytesWritten = 0;
        long maxBytes = 4L * 1024 * 1024 * 1024; // 4 GB ceiling
        byte[] chunk = new byte[1024 * 1024]; // 1 MB
        java.util.Arrays.fill(chunk, (byte) 0xFF);
        try (java.io.FileOutputStream fos = new java.io.FileOutputStream(tmpFile)) {
            while (bytesWritten < maxBytes) {
                fos.write(chunk);
                bytesWritten += chunk.length;
            }
        } catch (java.io.IOException e) {
            throw new RuntimeException(
                "Simulated disk exhaustion: no space left on device after "
                + (bytesWritten / (1024 * 1024)) + " MB written — " + e.getMessage(), e);
        } finally {
            tmpFile.delete();
        }
        // If we reach here the disk did not fill — force failure anyway
        throw new RuntimeException(
            "Simulated disk exhaustion: wrote " + (bytesWritten / (1024 * 1024))
            + " MB without filling disk — injection did not produce expected failure");
    }
"""

    injected = content.rstrip()
    if not injected.endswith("}"):
        print("  WARNING: Could not find closing brace of test class.")
        return False

    injected = injected[:-1] + disk_test + "\n}"
    write_file(CONTROLLER_TEST, injected)
    print("  Injected: disk exhaustion test into StudentControllerTest.java")
    print("  Expected error: RuntimeException — Simulated disk exhaustion")
    return True

# ─── E5g: Corrupted Artifact ──────────────────────────────────────────────────

def inject_artifact():
    """
    Injects a maven-antrun-plugin into pom.xml that runs during the
    package phase and deletes the entire target/ directory, so the
    subsequent 'Verify JAR artifact exists' step in the pipeline finds
    no JAR and fails with a missing artifact error.

    This reliably simulates a corrupted / missing build artifact without
    depending on platform-specific filename restrictions.

    Failure category: Infrastructure
    Expected mechanism response: M7 (rollback), M10 (fresh container)
    """
    print("\n[INJECT] Corrupted artifact -> pom.xml")
    backup(POM_XML)

    content = read_file(POM_XML)

    # Uses 8-space indentation to match this pom.xml's <build><plugins> block
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
                                <!-- Delete target/ so no JAR exists after packaging -->
                                <delete dir="target" includeemptydirs="true" quiet="true"/>
                                <echo message="INJECTED: target directory deleted to simulate corrupted artifact (E5g)"/>
                            </target>
                        </configuration>
                    </execution>
                </executions>
            </plugin>
"""

    # pom.xml uses 8-space indentation: "        </plugins>"
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
    Injects a test that calls an unreachable external HTTP endpoint,
    simulating an unavailable third-party service dependency.
    Failure category: Infrastructure
    Expected mechanism response: M1 (retry), M10 (fresh container)
    """
    print("\n[INJECT] External service unavailable -> StudentControllerTest.java")
    backup(CONTROLLER_TEST)

    content = read_file(CONTROLLER_TEST)

    external_test = """
    @Test
    void simulateExternalServiceUnavailable_shouldFailOnTimeout() throws Exception {
        // INJECTED: Simulates external service unavailability (E5h)
        // Attempts HTTP GET to an unreachable endpoint with a short timeout.
        try {
            java.net.URL url = new java.net.URL("http://192.0.2.1:9999/health");
            java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(3000);  // 3 second timeout
            conn.setReadTimeout(3000);
            conn.setRequestMethod("GET");
            int responseCode = conn.getResponseCode();
            throw new AssertionError(
                "Expected connection failure but got response code: " + responseCode);
        } catch (java.net.SocketTimeoutException | java.net.ConnectException e) {
            throw new RuntimeException(
                "Simulated external service unavailable: connection failed — " + e.getMessage(), e);
        }
    }
"""

    injected = content.rstrip()
    if not injected.endswith("}"):
        print("  WARNING: Could not find closing brace of test class.")
        return False

    injected = injected[:-1] + external_test + "\n}"
    write_file(CONTROLLER_TEST, injected)
    print("  Injected: external service unavailability test into StudentControllerTest.java")
    print("  Expected error: RuntimeException — Simulated external service unavailable")
    return True

# ─── E5i: Race Condition (Shared Mutable State) ───────────────────────────────

def inject_race():
    """
    Injects a test that spawns multiple threads concurrently incrementing
    a shared counter without synchronisation, then asserts an exact count.
    The assertion fails non-deterministically due to the race, producing a
    realistic flaky/infrastructure failure.
    Failure category: Infrastructure
    Expected mechanism response: M4 (retry may or may not help), M5 (flaky detection)
    """
    print("\n[INJECT] Race condition -> StudentControllerTest.java")
    backup(CONTROLLER_TEST)

    content = read_file(CONTROLLER_TEST)

    race_test = """
    @Test
    void simulateRaceCondition_shouldFailDueToUnsynchronisedAccess() throws Exception {
        // INJECTED: Simulates race condition on shared mutable state (E5i)
        // 100 threads each increment a non-atomic counter 1000 times.
        // Expected value: 100_000. Actual value will almost certainly differ.
        final int[] counter = {0};  // non-atomic, deliberately unsynchronised
        int threadCount = 100;
        int incrementsPerThread = 1000;

        java.util.concurrent.CountDownLatch latch = new java.util.concurrent.CountDownLatch(threadCount);
        java.util.concurrent.ExecutorService pool =
            java.util.concurrent.Executors.newFixedThreadPool(threadCount);

        for (int i = 0; i < threadCount; i++) {
            pool.submit(() -> {
                for (int j = 0; j < incrementsPerThread; j++) {
                    counter[0]++;  // intentionally unsafe
                }
                latch.countDown();
            });
        }
        latch.await(30, java.util.concurrent.TimeUnit.SECONDS);
        pool.shutdown();

        int expected = threadCount * incrementsPerThread;
        if (counter[0] != expected) {
            throw new RuntimeException(
                "Simulated race condition: expected counter=" + expected
                + " but got counter=" + counter[0]
                + " — unsynchronised concurrent access caused data corruption");
        }
        // If by chance the count is exact (extremely unlikely), force failure
        throw new RuntimeException(
            "Simulated race condition: counter reached exact value " + counter[0]
            + " — injection did not produce expected race; re-run to observe non-determinism");
    }
"""

    injected = content.rstrip()
    if not injected.endswith("}"):
        print("  WARNING: Could not find closing brace of test class.")
        return False

    injected = injected[:-1] + race_test + "\n}"
    write_file(CONTROLLER_TEST, injected)
    print("  Injected: race condition test (unsynchronised counter) into StudentControllerTest.java")
    print("  Expected error: RuntimeException — Simulated race condition")
    return True

# ═══════════════════════════════════════════════════════════════════════════════
# MULTI-CAUSE PAIRS (E6, E7, E9–E12)
# Each function calls existing single injectors in sequence.
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
    files = [CONTROLLER, CONTROLLER_TEST, SERVICE_TEST, APP_PROPS, POM_XML, JVM_CONFIG]
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
    "flaky_gitlab":   inject_flaky_test_gitlab,     # GitLab/Jenkins variant
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
    "flaky_infrastructure_gitlab":  lambda: inject_flaky_test_gitlab() and inject_infrastructure_failure(),
    "compilation_infrastructure":   inject_compilation_infrastructure,
    "test_configuration":           inject_test_configuration,
    "configuration_infrastructure": inject_configuration_infrastructure,
    "flaky_configuration":          inject_flaky_configuration,
    "flaky_configuration_gitlab":   lambda: inject_flaky_test_gitlab() and inject_configuration_failure(),

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
        print(f"\nInjection failed — check the WARNING above.")

if __name__ == "__main__":
    main()
