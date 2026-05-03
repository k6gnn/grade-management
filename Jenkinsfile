// -----------------------------------------------------------------------------
// Jenkinsfile - CI/CD Pipeline with Self-Healing Mechanisms M1-M14
// Thesis: Evaluation of Self-Healing Mechanisms in CI/CD Pipelines
// Student: Kanan Badalov, RTU
//
// M14 runs first as a warning-only proactive risk predictor.
// M13 runs last (post stage, always) as an ML-based failure classifier.
// -----------------------------------------------------------------------------

pipeline {

    agent none  // Each stage declares its own agent/image

    // -------------------------------------------------------------------------
    // Global environment variables  (M9 verifies these are all set)
    // -------------------------------------------------------------------------
    environment {
        JAVA_VERSION           = '21'
        MAVEN_OPTS             = '-Xmx1024m -Dmaven.repo.local=${WORKSPACE}/.m2/repository'
        APP_NAME               = 'grade-management'
        BUILD_TIMEOUT_MINUTES  = '10'
        TEST_TIMEOUT_MINUTES   = '10'
        MAX_RETRIES            = '2'
        MAVEN_CLI_OPTS         = '-B --no-transfer-progress'
        M14_THRESHOLD_MODE     = 'balanced'
        M14_MODE               = 'warning_only'
    }

    options {
        // Discard old builds - keep 30 days of artifacts, last 20 builds
        buildDiscarder(logRotator(daysToKeepStr: '30', numToKeepStr: '20'))
        timestamps()
        ansiColor('xterm')
    }

    stages {

        // =====================================================================
        // STAGE: Risk Assessment  (M14)
        // Warning-only - never blocks the pipeline.
        // =====================================================================
        stage('M14 - Risk Assessment') {
            agent {
                docker { image 'python:3.11-slim' }
            }
            options {
                timeout(time: 5, unit: 'MINUTES')
            }
            steps {
                script {
                    // Stash workspace so later stages can unstash artifacts
                    try {
                        sh '''
                            echo "=== M14: Proactive Failure Risk Assessment ==="
                            export HOME=/tmp
                            python -m pip install --upgrade pip -q --user
                            pip install -q numpy scikit-learn joblib requests --user
                            export PYTHONPATH=$(python -c "import site; print(site.getusersitepackages())")
                            export PATH="$HOME/.local/bin:$PATH"

                            if [ ! -f "scripts/m14_predict.py" ] || \
                               [ ! -f "models/m14_model.pkl" ]   || \
                               [ ! -f "models/m14_config.pkl" ]; then
                                echo "WARNING: M14 model files not found - skipping prediction (warning-only mode)"
                                echo \'{"status":"skipped","reason":"model_files_not_found"}\' > m14_risk_report.json
                                cat m14_risk_report.json
                                exit 0
                            fi

                            python scripts/m14_predict.py \
                                --platform  jenkins \
                                --repository "${JOB_NAME}" \
                                --commit     "${GIT_COMMIT}" \
                                --branch     "${GIT_BRANCH}" \
                                --event      "push" \
                                --model      models/m14_model.pkl \
                                --config     models/m14_config.pkl \
                                --output     m14_risk_report.json

                            cat m14_risk_report.json
                        '''
                    } catch (err) {
                        // allow_failure: true equivalent
                        echo "M14 risk assessment failed (non-blocking): ${err.message}"
                        sh "echo '{\"status\":\"error\",\"reason\":\"assessment_failed\"}' > m14_risk_report.json"
                    }
                }
            }
            post {
                always {
                    archiveArtifacts artifacts: 'm14_risk_report.json', allowEmptyArchive: true
                    stash name: 'm14-report', includes: 'm14_risk_report.json', allowEmpty: true
                }
            }
        }

        // =====================================================================
        // STAGE: Pre-Pipeline  (M9 + M8)
        // =====================================================================
        stage('Pre-Pipeline') {
            agent {
                docker { image 'ubuntu:22.04' }
            }
            options {
                timeout(time: 4, unit: 'MINUTES')
            }
            stages {

                // -------------------------------------------------------------
                // M9 - Environment Variable Verification
                // -------------------------------------------------------------
                stage('M9 - Environment Verification') {
                    steps {
                        sh '''
                            echo "=== M9 Environment Variable Verification ==="

                            check_var() {
                                name="$1"
                                value="$2"
                                if [ -z "$value" ]; then
                                    echo "MISSING: $name"
                                    return 1
                                else
                                    echo "OK: $name = $value"
                                    return 0
                                fi
                            }

                            MISSING=0
                            check_var "JAVA_VERSION"          "$JAVA_VERSION"          || MISSING=$((MISSING+1))
                            check_var "MAVEN_OPTS"            "$MAVEN_OPTS"            || MISSING=$((MISSING+1))
                            check_var "APP_NAME"              "$APP_NAME"              || MISSING=$((MISSING+1))
                            check_var "BUILD_TIMEOUT_MINUTES" "$BUILD_TIMEOUT_MINUTES" || MISSING=$((MISSING+1))
                            check_var "TEST_TIMEOUT_MINUTES"  "$TEST_TIMEOUT_MINUTES"  || MISSING=$((MISSING+1))

                            if [ "$MISSING" -gt 0 ]; then
                                echo "FAILURE: $MISSING required variable(s) are missing."
                                exit 1
                            fi

                            echo "All environment variables verified successfully."
                        '''
                    }
                }

                // -------------------------------------------------------------
                // M8 - Configuration Validation Gate
                // -------------------------------------------------------------
                stage('M8 - Configuration Validation') {
                    steps {
                        sh '''
                            echo "=== M8 Configuration Validation Gate ==="

                            echo "--- Validating pom.xml ---"
                            if [ ! -f "pom.xml" ]; then
                                echo "FAILURE: pom.xml not found"
                                exit 1
                            fi

                            for element in "groupId" "artifactId" "version" "dependencies" "build"; do
                                if grep -q "<$element>" pom.xml; then
                                    echo "OK: <$element> found"
                                else
                                    echo "FAILURE: <$element> missing from pom.xml"
                                    exit 1
                                fi
                            done

                            echo "--- Validating application.properties ---"
                            PROPS="src/main/resources/application.properties"
                            if [ ! -f "$PROPS" ]; then
                                echo "FAILURE: application.properties not found"
                                exit 1
                            fi

                            for key in "spring.application.name" "server.port" "spring.datasource.url"; do
                                if grep -q "^$key=" "$PROPS"; then
                                    echo "OK: $key found"
                                else
                                    echo "FAILURE: Required key '$key' missing"
                                    exit 1
                                fi
                            done

                            PORT=$(grep "^server.port=" "$PROPS" | head -1 | cut -d= -f2- | tr -d "[:space:]")
                            if ! echo "$PORT" | grep -Eq "^[0-9]+$"; then
                                echo "FAILURE: server.port must be numeric, got '$PORT'"
                                exit 1
                            fi
                            if [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
                                echo "FAILURE: server.port out of range: $PORT"
                                exit 1
                            fi

                            echo "Configuration validation passed."
                        '''
                    }
                }
            }
        }

        // =====================================================================
        // STAGE: Build  (M1, M2, M3, M11)
        // =====================================================================
        stage('Build') {
            agent {
                docker { image 'maven:3.9-eclipse-temurin-21' }
            }
            options {
                timeout(time: 10, unit: 'MINUTES')
                retry(2)    // M2 - automatic retry on failure
            }
            steps {
                // M11 - Maven dependency cache via Jenkins cache plugin or local .m2
                cache(maxCacheSize: 512, caches: [
                    arbitraryFileCache(
                        path: '.m2/repository',
                        cacheValidityDecidingFile: 'pom.xml'
                    )
                ]) {
                    sh '''
                        echo "=== Stage 1: Build ==="
                        if [ -d ".m2/repository" ]; then
                            echo "M11: Cache present - using cached dependencies"
                        else
                            echo "M11: Cache absent - downloading fresh dependencies"
                        fi

                        mvn compile $MAVEN_CLI_OPTS > build.log 2>&1 || {
                            EXIT_CODE=$?
                            cat build.log
                            exit $EXIT_CODE
                        }
                        cat build.log
                        echo "Build completed successfully."
                    '''
                }
            }
            post {
                always {
                    archiveArtifacts artifacts: 'build.log', allowEmptyArchive: true
                    stash name: 'build-log', includes: 'build.log', allowEmpty: true
                }
                failure {
                    // M3 - Build failure notification
                    echo "=== M3: Build Failure Notification ==="
                    echo "Branch:   ${env.GIT_BRANCH}"
                    echo "Commit:   ${env.GIT_COMMIT}"
                    echo "Build:    ${env.BUILD_URL}"
                    echo "ACTION REQUIRED: Compilation failed after retries."
                }
            }
        }

        // =====================================================================
        // STAGE: Test  (M4, M5, M6)
        // =====================================================================
        stage('Test') {
            agent {
                docker { image 'maven:3.9-eclipse-temurin-21' }
            }
            options {
                timeout(time: 10, unit: 'MINUTES')
                retry(2)    // M4 - automatic retry (flaky test resilience)
            }
            steps {
                cache(maxCacheSize: 512, caches: [
                    arbitraryFileCache(
                        path: '.m2/repository',
                        cacheValidityDecidingFile: 'pom.xml'
                    )
                ]) {
                    sh '''
                        echo "=== Stage 2: Test ==="
                        mvn test $MAVEN_CLI_OPTS > test.log 2>&1 || {
                            EXIT_CODE=$?
                            TIMESTAMP=$(date -u +%H:%M:%S)
                            echo "ATTEMPT_FAILED at $TIMESTAMP (exit_code=$EXIT_CODE)" >> flaky_failure_log.txt
                            if [ -d "target/surefire-reports" ]; then
                                grep -h "FAILED\\|ERROR\\|RuntimeException\\|Simulated transient\\|instability\\|ApplicationContext\\|INVALID_PORT_VALUE\\|NumberFormatException" \
                                    target/surefire-reports/*.txt 2>/dev/null >> flaky_failure_log.txt || true
                            fi
                            cat test.log
                            exit $EXIT_CODE
                        }
                        cat test.log
                        touch flaky_failure_log.txt
                    '''
                }
            }
            post {
                always {
                    // Publish JUnit results (M6 - test trend analysis)
                    junit testResults: 'target/surefire-reports/*.xml', allowEmptyResults: true

                    sh '''
                        echo "=== M5: Flaky Test Quarantine Analysis ==="
                        REPORT_DIR="target/surefire-reports"
                        FLAG_FILE="flaky_failure_log.txt"

                        if [ ! -d "$REPORT_DIR" ]; then
                            echo "No Surefire reports found - skipping flakiness analysis"
                        elif [ -f "$FLAG_FILE" ] && [ -s "$FLAG_FILE" ]; then
                            echo "M5: FLAKY TEST DETECTED"
                            cat "$FLAG_FILE"
                            echo "Classification: FLAKY CANDIDATE"
                        else
                            FAILED_REPORTS=$(find "$REPORT_DIR" -name "*.txt" -exec grep -l "FAILED\\|ERROR" {} \\; 2>/dev/null || true)
                            if [ -n "$FAILED_REPORTS" ]; then
                                echo "M5: TEST FAILURES DETECTED (deterministic - not flaky)"
                                echo "$FAILED_REPORTS"
                            else
                                echo "M5: All tests passed - no flaky test candidates detected"
                            fi
                        fi

                        echo "=== M6: Test Result Trend Analysis ==="
                        if [ ! -d "$REPORT_DIR" ]; then
                            echo "No Surefire reports found"
                        else
                            TOTAL=0; FAILED=0; ERRORS=0
                            while IFS= read -r file; do
                                [ -f "$file" ] || continue
                                t=$(grep -oE "Tests run: [0-9]+" "$file" 2>/dev/null | head -1 | grep -oE "[0-9]+" || echo 0)
                                f=$(grep -oE "Failures: [0-9]+" "$file" 2>/dev/null | head -1 | grep -oE "[0-9]+" || echo 0)
                                e=$(grep -oE "Errors: [0-9]+" "$file" 2>/dev/null | head -1 | grep -oE "[0-9]+" || echo 0)
                                TOTAL=$((TOTAL + t))
                                FAILED=$((FAILED + f))
                                ERRORS=$((ERRORS + e))
                            done << EOF
$(find "$REPORT_DIR" -name "*.txt" 2>/dev/null)
EOF

                            PASSED=$((TOTAL - FAILED - ERRORS))
                            echo "Tests run: $TOTAL | Passed: $PASSED | Failed: $FAILED | Errors: $ERRORS"
                            if [ "$FAILED" -gt 0 ] || [ "$ERRORS" -gt 0 ]; then
                                echo "TREND ALERT: Test failures detected."
                            else
                                echo "TREND OK: All tests passed in this run."
                            fi
                        fi
                    '''

                    archiveArtifacts artifacts: 'test.log, flaky_failure_log.txt', allowEmptyArchive: true
                    archiveArtifacts artifacts: 'target/surefire-reports/**', allowEmptyArchive: true
                    stash name: 'test-artifacts',
                          includes: 'test.log,flaky_failure_log.txt',
                          allowEmpty: true
                }
            }
        }

        // =====================================================================
        // STAGE: Package  (M10, M12)
        // =====================================================================
        stage('Package') {
            agent {
                docker { image 'maven:3.9-eclipse-temurin-21' }
            }
            options {
                timeout(time: 5, unit: 'MINUTES')
            }
            steps {
                cache(maxCacheSize: 512, caches: [
                    arbitraryFileCache(
                        path: '.m2/repository',
                        cacheValidityDecidingFile: 'pom.xml'
                    )
                ]) {
                    sh '''
                        echo "=== Stage 3: Package ==="
                        mvn package -DskipTests $MAVEN_CLI_OPTS

                        JAR=$(find target -name "*.jar" -not -name "*sources*" | head -1)
                        if [ -z "$JAR" ]; then
                            echo "FAILURE: No JAR artifact found in target/"
                            exit 1
                        fi
                        echo "JAR artifact produced: $JAR"
                        ls -lh "$JAR"
                    '''
                }
            }
            post {
                always {
                    archiveArtifacts artifacts: 'target/*.jar', allowEmptyArchive: true
                    stash name: 'jar-artifact', includes: 'target/*.jar', allowEmpty: true
                }
            }
        }

        // =====================================================================
        // STAGE: Deploy  (M7, M12)
        // Only runs on the main branch.
        // =====================================================================
        stage('Deploy') {
            // M7 - only deploy from main
            when {
                branch 'main'
            }
            agent {
                docker { image 'maven:3.9-eclipse-temurin-21' }
            }
            options {
                timeout(time: 5, unit: 'MINUTES')
            }
            steps {
                unstash 'jar-artifact'
                sh '''
                    echo "=== Stage 4: Deploy ==="
                    JAR=$(find target -name "*.jar" -not -name "*sources*" | head -1)
                    echo "Deploying artifact: $JAR"
                    echo "Build:  ${BUILD_NUMBER}"
                    echo "Commit: ${GIT_COMMIT}"
                    echo "Deployment successful."
                '''
            }
            post {
                failure {
                    // M7 - Automated rollback notification
                    sh '''
                        echo "=== M7: Automated Rollback Initiated ==="
                        echo "Deployment failed. In production this would revert to the last known good state."
                        git log --oneline -5 || true
                    '''
                }
            }
        }

    }   // end stages

    // =========================================================================
    // POST (always)  -  M13: ML Failure Classification
    // Runs after all stages regardless of pipeline outcome.
    // =========================================================================
     post {
            always {
                node('built-in') {
                    script {
                        // Use python via docker for the post step
                        docker.image('python:3.11-slim').inside {
                        try { unstash 'm14-report'    } catch (e) { echo "No M14 report stash: ${e.message}" }
                        try { unstash 'build-log'     } catch (e) { echo "No build-log stash: ${e.message}" }
                        try { unstash 'test-artifacts'} catch (e) { echo "No test-artifacts stash: ${e.message}" }

                        sh """
                            echo "=== M13: ML Failure Classification ==="
                            export HOME=/tmp
                            python -m pip install --upgrade pip -q --user
                            pip install -q pandas numpy scikit-learn joblib requests --user
                            export PYTHONPATH=\$(python -c "import site; print(site.getusersitepackages())")
                            export PATH="\$HOME/.local/bin:\$PATH"

                            mkdir -p logs
                            [ -f build.log ]             && cp build.log             logs/build.log             || true
                            [ -f test.log ]              && cp test.log              logs/test.log              || true
                            [ -f flaky_failure_log.txt ] && cp flaky_failure_log.txt logs/flaky_failure_log.txt || true

                            cat > pipeline_status.json << 'ENDJSON'
{
  "platform": "jenkins",
  "job_name": "${env.JOB_NAME}",
  "build_number": "${env.BUILD_NUMBER}",
  "build_url": "${env.BUILD_URL}",
  "commit": "${env.GIT_COMMIT ?: 'unknown'}",
  "branch": "${env.GIT_BRANCH ?: 'unknown'}",
  "current_job_status": "${currentBuild.currentResult}"
}
ENDJSON
                            cat pipeline_status.json

                            if [ ! -f "scripts/m13_predict.py" ] || [ ! -f "models/m13_model_bundle.pkl" ]; then
                                echo "WARNING: M13 model files not found - skipping ML classification"
                                echo '{"status":"skipped","reason":"model_files_not_found"}' > m13_classification_report.json
                                cat m13_classification_report.json
                            else
                                python scripts/m13_predict.py \\
                                    --status pipeline_status.json \\
                                    --logs   logs \\
                                    --model-bundle models/m13_model_bundle.pkl \\
                                    --output m13_classification_report.json
                                cat m13_classification_report.json
                            fi

                            [ -f m14_risk_report.json ] || echo '{"status":"not_available"}' > m14_risk_report.json
                        """

                        archiveArtifacts artifacts: '''
                            m13_classification_report.json,
                            pipeline_status.json,
                            m14_risk_report.json,
                            logs/**
                        ''', allowEmptyArchive: true
                        }   // end docker.image().inside
                    }
                }
            }
        }
    }

}
