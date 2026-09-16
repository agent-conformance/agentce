# Implementation-report registry

The registry of verified implementation reports (SPEC §11.5, §14.5 CP-1). A listing here is the only
form of conformance claim the project recognises: an engine or adapter is *conforming* only when a
verified report for it is committed to this directory.

## What gets listed

A report is added by pull request and accepted only when all of the following verify:

- the report shows `claim: full` and `no_ml: pass`;
- it was produced by the official Engine Conformance Suite runner at a tagged release;
- the **golden revision** it names is signed, and the signature attests exactly the golden digest the
  report names (SPEC §14.5 CP-4); and
- the engine package digest it names is present.

A report that does not verify is `unverified` and is **not** listed. `registry_check` enforces this;
`registry_check --self-test` proves the check by accepting a well-formed report and refusing one whose
golden digest does not match its signature.

## Report shape

```json
{
  "engine": {"impl": "<name>", "version": "<x.y.z>", "package_digest": "sha256:…"},
  "claim": "full",
  "no_ml": "pass",
  "golden": {"revision": "<dataset revision>", "digest": "sha256:…", "signature": {"…DSSE envelope…"}}
}
```

## Status

Empty until the general-availability update (`HUMAN_ACTIONS.md` GA-9): under the pre-GA guardrails no
implementation is published or listed. The acceptance workflow and `registry_check` are in place so
that the first listings are added the same way every later one is.
