import org.gradle.api.tasks.testing.logging.TestExceptionFormat

// AgentCE Java engine (SPEC §5.3). A deterministic, read-only, model-free port of the reference
// engine: the module set needed for a byte-identical Engine Conformance Suite run over the corpus.
plugins {
    java
    application
    jacoco
}

group = "org.agentce"
version = "0.1.0"

repositories {
    mavenCentral()
}

// Exact, pinned versions (repository convention: no ranges). Dependency locking (gradle.lockfile)
// guards reproducibility and feeds the no_ml dependency scan.
dependencies {
    implementation("com.fasterxml.jackson.core:jackson-databind:2.18.2")
    implementation("org.yaml:snakeyaml:2.3")
    implementation("com.networknt:json-schema-validator:1.5.4")
    runtimeOnly("org.slf4j:slf4j-nop:2.0.16")

    testImplementation("org.junit.jupiter:junit-jupiter:5.11.3")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher:1.11.3")
}

dependencyLocking {
    lockAllConfigurations()
}

application {
    mainClass = "org.agentce.Cli"
    applicationName = "agentce"
}

tasks.withType<JavaCompile>().configureEach {
    options.encoding = "UTF-8"
    options.release = 21
    options.compilerArgs.add("-Xlint:all")
}

tasks.test {
    useJUnitPlatform()
    testLogging {
        events("failed")
        exceptionFormat = TestExceptionFormat.FULL
    }
    finalizedBy(tasks.jacocoTestReport)
}

jacoco {
    toolVersion = "0.8.12"
}

tasks.jacocoTestReport {
    dependsOn(tasks.test)
    reports {
        xml.required.set(true)
        html.required.set(false)
    }
}

// Per-package coverage floor (a ratchet that may only rise). Wired into `check` so a coverage
// regression fails the build, not only a test failure.
tasks.jacocoTestCoverageVerification {
    dependsOn(tasks.jacocoTestReport)
    violationRules {
        rule {
            limit {
                // Floor set at today's measured instruction coverage (~77%). It is a ratchet:
                // raise it as coverage improves toward the >=90% target; never lower it.
                counter = "INSTRUCTION"
                minimum = "0.77".toBigDecimal()
            }
        }
    }
}

tasks.check {
    dependsOn(tasks.jacocoTestCoverageVerification)
}

// The runnable artifact: one jar carrying the engine and its runtime dependencies, so
// `java -jar agentce-<version>-all.jar` works from any directory with nothing else installed. The
// entries are ordered and undated so two builds of the same sources produce the same bytes.
val runnableJar by tasks.registering(Jar::class) {
    group = "build"
    description = "Assembles the engine and its runtime dependencies into one runnable jar."
    archiveClassifier.set("all")
    manifest { attributes["Main-Class"] = "org.agentce.Cli" }
    from(sourceSets.main.get().output)
    from({
        configurations.runtimeClasspath.get()
            .filter { it.name.endsWith(".jar") }
            .map { zipTree(it) }
    })
    exclude("META-INF/*.SF", "META-INF/*.DSA", "META-INF/*.RSA", "module-info.class")
    duplicatesStrategy = DuplicatesStrategy.EXCLUDE
    isPreserveFileTimestamps = false
    isReproducibleFileOrder = true
}

tasks.assemble {
    dependsOn(runnableJar)
}
