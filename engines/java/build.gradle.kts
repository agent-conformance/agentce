import org.gradle.api.tasks.testing.logging.TestExceptionFormat

// AgentCE Java engine (SPEC §5.3). A deterministic, read-only, model-free port of the reference
// engine: the module set needed for a byte-identical Engine Conformance Suite run over the corpus.
plugins {
    java
    application
}

group = "org.agentce"
version = "0.0.1"

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
}
