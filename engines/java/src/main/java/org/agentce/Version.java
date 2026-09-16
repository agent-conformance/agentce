package org.agentce;

/**
 * Engine identity constants (SPEC §9.5): the implementation name, spec version, and package version
 * the reports and manifests record. The name is distinct per engine ({@code agentce-java}); the spec
 * version and package version match the reference so shared artifacts (OSCAL, canonical assertions)
 * stay byte-identical across engines.
 */
public final class Version {
    private Version() {}

    public static final String ENGINE_NAME = "agentce-java";
    public static final String SPEC_VERSION = "0.6";
    public static final String ENGINE_VERSION = "0.0.1";
}
