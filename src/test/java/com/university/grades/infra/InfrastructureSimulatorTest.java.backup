package com.university.grades.infra;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Timeout;

import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * Infrastructure Failure Simulator — E5b through E5i
 * =====================================================
 * This class is a DEDICATED test harness for infrastructure failure injection.
 * It deliberately uses NO Spring context (@WebMvcTest / @SpringBootTest) so
 * each experiment runs in a clean, lightweight JVM with predictable memory
 * and no web-layer overhead competing for resources.
 *
 * BASELINE BEHAVIOUR (clean repo):
 *   ACTIVE_EXPERIMENT = "NONE" — every test calls assumeTrue(false) and is
 *   SKIPPED. Surefire reports: Tests run: 8, Skipped: 8. Pipeline passes.
 *
 * INJECTED BEHAVIOUR:
 *   inject_failure.py sets ACTIVE_EXPERIMENT to one of the experiment IDs
 *   below. Exactly one test runs and fails deterministically. All others skip.
 *
 * Experiment IDs:
 *   E5B  — OOM / memory exhaustion
 *   E5C  — Network instability (DNS failure)
 *   E5D  — Port conflict (EADDRINUSE)
 *   E5E  — Deadlock / infinite-loop timeout
 *   E5F  — Disk exhaustion
 *   E5G  — Corrupted artifact  (handled via pom.xml, not this class)
 *   E5H  — External service unavailable
 *   E5I  — Race condition
 *
 * INJECTOR CONTRACT:
 *   inject_failure.py does ONE string replacement per injection:
 *     ACTIVE_EXPERIMENT = "NONE"  ->  ACTIVE_EXPERIMENT = "E5B"   (for OOM)
 *   restore does ONE string replacement:
 *     ACTIVE_EXPERIMENT = "E5B"   ->  ACTIVE_EXPERIMENT = "NONE"
 *   No code is ever appended or deleted — only this one constant changes.
 */
class InfrastructureSimulatorTest {

    // ── SINGLE INJECTION POINT ────────────────────────────────────────────────
    // inject_failure.py replaces the value of this constant. Do not rename it
    // or change its formatting — the injector matches the exact string below.
    static final String ACTIVE_EXPERIMENT = "NONE";
    // ─────────────────────────────────────────────────────────────────────────

    // ── E5B: OOM / Memory Exhaustion ─────────────────────────────────────────

    @Test
    void e5b_simulateOutOfMemoryError() {
        assumeTrue("E5B".equals(ACTIVE_EXPERIMENT), "E5B disabled — set ACTIVE_EXPERIMENT=E5B to enable");

        // Allocates byte arrays until OutOfMemoryError regardless of -Xmx value.
        // No Spring context is running, so the full heap is available for this test.
        // Phase 1: 64 MB chunks to consume most of the heap quickly.
        // Phase 2: 1 MB chunks to fill remaining gaps.
        // 'held = null' before the rethrow lets the RuntimeException constructor
        // allocate its message string without hitting OOM again.
        java.util.List<byte[]> held = new java.util.ArrayList<>();
        try {
            while (true) {
                held.add(new byte[64 * 1024 * 1024]);
            }
        } catch (OutOfMemoryError e1) {
            try {
                while (true) {
                    held.add(new byte[1024 * 1024]);
                }
            } catch (OutOfMemoryError e2) {
                held = null;
                throw new RuntimeException(
                    "Simulated infrastructure failure: "
                    + "java.lang.OutOfMemoryError: Java heap space exhausted", e2);
            }
        }
    }

    // ── E5C: Network Instability ──────────────────────────────────────────────

    @Test
    void e5c_simulateNetworkInstability() {
        assumeTrue("E5C".equals(ACTIVE_EXPERIMENT), "E5C disabled — set ACTIVE_EXPERIMENT=E5C to enable");

        // Resolves a guaranteed-unresolvable host (.invalid TLD per RFC 2606).
        // UnknownHostException is expected; we re-throw as RuntimeException so
        // Surefire records a clear test ERROR with the DNS failure message.
        try {
            java.net.InetAddress.getByName("this.host.does.not.exist.invalid");
            throw new AssertionError(
                "Expected UnknownHostException but no exception was thrown — network injection failed");
        } catch (java.net.UnknownHostException e) {
            throw new RuntimeException(
                "Simulated network instability: DNS resolution failed — " + e.getMessage(), e);
        }
    }

    // ── E5D: Port Conflict ────────────────────────────────────────────────────

    @Test
    void e5d_simulatePortConflict() {
        assumeTrue("E5D".equals(ACTIVE_EXPERIMENT), "E5D disabled — set ACTIVE_EXPERIMENT=E5D to enable");

        // Asks the OS for a free port via ServerSocket(0), then attempts to bind
        // the same port a second time while the first socket is still open.
        // Guaranteed BindException — no hardcoded port number that could collide.
        java.net.ServerSocket first = null;
        java.net.ServerSocket second = null;
        try {
            first = new java.net.ServerSocket(0);
            int port = first.getLocalPort();
            second = new java.net.ServerSocket(port);
            throw new AssertionError(
                "Expected BindException on port " + port + " but no exception thrown — injection failed");
        } catch (java.io.IOException e) {
            throw new RuntimeException(
                "Simulated port conflict: address already in use — " + e.getMessage(), e);
        } finally {
            if (first  != null) try { first.close();  } catch (Exception ignored) {}
            if (second != null) try { second.close(); } catch (Exception ignored) {}
        }
    }

    // ── E5E: Deadlock / Timeout ───────────────────────────────────────────────

    @Test
    @Timeout(value = 30, unit = TimeUnit.SECONDS)
    void e5e_simulateDeadlock() {
        assumeTrue("E5E".equals(ACTIVE_EXPERIMENT), "E5E disabled — set ACTIVE_EXPERIMENT=E5E to enable");

        // Spins in an infinite loop. JUnit 5 @Timeout(30s) interrupts the test
        // thread after 30 seconds, throwing a TimeoutException that Surefire
        // records as a test ERROR. The 60-second safety valve below handles the
        // edge case where @Timeout is somehow not configured on the runner.
        long start = System.currentTimeMillis();
        while (true) {
            if (System.currentTimeMillis() - start > 60_000) {
                throw new RuntimeException(
                    "Simulated deadlock: thread did not terminate within expected time");
            }
            try {
                Thread.sleep(100);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new RuntimeException("Simulated deadlock: interrupted after timeout", e);
            }
        }
    }

    // ── E5F: Disk Exhaustion ──────────────────────────────────────────────────

    @Test
    void e5f_simulateDiskExhaustion() {
        assumeTrue("E5F".equals(ACTIVE_EXPERIMENT), "E5F disabled — set ACTIVE_EXPERIMENT=E5F to enable");

        // Queries the actual usable space on the temp partition at runtime, then
        // writes available + 64 MB to guarantee exhaustion on any runner.
        // fos.getFD().sync() after each chunk forces OS to commit writes to disk
        // so IOException fires the moment the partition is full.
        java.io.File tmpDir = new java.io.File(System.getProperty("java.io.tmpdir"));
        long available = tmpDir.getUsableSpace();
        long target = available + (64L * 1024 * 1024);

        java.util.List<java.io.File> tmpFiles = new java.util.ArrayList<>();
        long bytesWritten = 0;
        byte[] chunk = new byte[4 * 1024 * 1024]; // 4 MB
        java.util.Arrays.fill(chunk, (byte) 0xAB);

        try {
            while (bytesWritten < target) {
                java.io.File f = java.io.File.createTempFile("disk_exhaust_", ".tmp");
                f.deleteOnExit();
                tmpFiles.add(f);
                try (java.io.FileOutputStream fos = new java.io.FileOutputStream(f)) {
                    for (int i = 0; i < 256; i++) { // 256 × 4 MB = 1 GB per file
                        fos.write(chunk);
                        bytesWritten += chunk.length;
                        fos.getFD().sync();
                    }
                }
            }
        } catch (java.io.IOException e) {
            throw new RuntimeException(
                "Simulated disk exhaustion: no space left on device after "
                + (bytesWritten / (1024 * 1024)) + " MB written — " + e.getMessage(), e);
        } finally {
            for (java.io.File f : tmpFiles) {
                try { f.delete(); } catch (Exception ignored) {}
            }
        }
    }

    // ── E5H: External Service Unavailable ────────────────────────────────────

    @Test
    void e5h_simulateExternalServiceUnavailable() {
        assumeTrue("E5H".equals(ACTIVE_EXPERIMENT), "E5H disabled — set ACTIVE_EXPERIMENT=E5H to enable");

        // Attempts HTTP GET to 192.0.2.1 (RFC 5737 TEST-NET — guaranteed unreachable).
        // Catches IOException as the base class to handle SocketTimeoutException,
        // ConnectException, AND NoRouteToHostException across all network environments.
        try {
            java.net.URL url = new java.net.URL("http://192.0.2.1:9999/health");
            java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(3000);
            conn.setReadTimeout(3000);
            conn.setRequestMethod("GET");
            int code = conn.getResponseCode();
            throw new AssertionError(
                "Expected connection failure but got response code: " + code);
        } catch (java.io.IOException e) {
            throw new RuntimeException(
                "Simulated external service unavailable: connection failed — " + e.getMessage(), e);
        }
    }

    // ── E5I: Race Condition ───────────────────────────────────────────────────

    @Test
    void e5i_simulateRaceCondition() throws InterruptedException {
        assumeTrue("E5I".equals(ACTIVE_EXPERIMENT), "E5I disabled — set ACTIVE_EXPERIMENT=E5I to enable");

        // 100 threads each increment a non-atomic int[] counter 1_000 times.
        // Expected value: 100_000. Actual value will almost certainly differ due
        // to lost updates from unsynchronised access. The fallback throw at the
        // end guarantees failure even on the rare JVM where the count is exact.
        final int[] counter = {0};
        int threadCount = 100;
        int incrementsPerThread = 1000;

        java.util.concurrent.CountDownLatch latch =
            new java.util.concurrent.CountDownLatch(threadCount);
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
        latch.await(30, TimeUnit.SECONDS);
        pool.shutdown();

        int expected = threadCount * incrementsPerThread;
        if (counter[0] != expected) {
            throw new RuntimeException(
                "Simulated race condition: expected counter=" + expected
                + " but got counter=" + counter[0]
                + " — unsynchronised concurrent access caused data corruption");
        }
        // Fallback: counter was exactly right (astronomically unlikely)
        throw new RuntimeException(
            "Simulated race condition: counter reached exact value " + counter[0]
            + " — re-run to observe non-determinism");
    }
}
