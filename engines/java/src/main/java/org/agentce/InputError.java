package org.agentce;

/** A missing, unreadable, or malformed input (exit code 3). */
public final class InputError extends AgentceError {
    private static final long serialVersionUID = 1L;

    public InputError(String key, String reason, String fix) {
        super(key, reason, fix, ExitCode.INPUT_ERROR.code);
    }
}
