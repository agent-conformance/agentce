package org.agentce;

/**
 * An error that names the fix (SPEC §13.4 AX-6): a stable {@code key}, a one-sentence {@code reason},
 * and a {@code fix}. The CLI renders these deterministically. The field is named {@code reason} rather
 * than the reference's {@code cause} to avoid clashing with {@link Throwable#getCause()}.
 */
public class AgentceError extends RuntimeException {
    private static final long serialVersionUID = 1L;

    public final String key;
    public final String reason;
    public final String fix;
    public final int exitCode;

    public AgentceError(String key, String reason, String fix, int exitCode) {
        super(key + ": " + reason);
        this.key = key;
        this.reason = reason;
        this.fix = fix;
        this.exitCode = exitCode;
    }

    public AgentceError(String key, String reason, String fix) {
        this(key, reason, fix, ExitCode.INPUT_ERROR.code);
    }
}
