# Gate a CI pipeline on an agentce assertions.json with Conftest/OPA (SPEC §9.4).
#
# Run with: conftest test --policy spec/report/examples/policy/conftest \
#   spec/report/examples/assertions.example.json
#
# Mirrors the same policy the engine's own --fail-on flag can express in-process
# (SPEC §8.5): deny any assertion that is non-conformant or insufficient_evidence
# on a high-severity control. Adjust the severity/family comparisons below to match
# your own gating policy; both fields are present on every record (never omitted,
# never a constant placeholder).
package agentce

import future.keywords.in

# input is the parsed assertions.json array (one record per (control, subject)).

deny[msg] {
	assertion := input[_]
	assertion.outcome in {"non-conformant", "insufficient_evidence"}
	assertion.severity == "high"
	msg := sprintf(
		"%s on %s is %s (severity=%s, family=%s): fails the high-severity gate",
		[assertion.control, assertion.subject, assertion.outcome, assertion.severity, assertion.family],
	)
}

violation[msg] {
	msg := deny[_]
}
