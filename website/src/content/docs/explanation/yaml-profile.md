---
title: YAML profile
description: 'Spec refs: SPEC §8.7 (security of the engine itself)'
---

Spec refs: SPEC §8.7 (security of the engine itself)

AgentCE reads every YAML file under one YAML profile, the same in the Python, TypeScript and Java
engines. The profile is a plain subset of YAML that all three engines' YAML libraries read the same
way, so a catalog, profile or deviation register gives the same result whichever engine reads it.
Each engine checks a file against the profile before it parses it.

The profile covers every YAML file the engine reads: catalog files (`catalog.yaml`, controls,
checklists, crosswalks, interpretations), the applicability profile, the domain binding, the
deviation register, manual evaluation records and `.agentce.yaml`.

## What a file may hold

- One document. A leading `---` is fine.
- Block or flow mappings and sequences, nested at most 100 deep.
- Plain, single-quoted or double-quoted scalars, and literal (`|`) or folded (`>`) block scalars.
- Comments.
- Mapping keys that are text, each written once per mapping.

## What puts a file outside the profile

| Rule | Example | Write it as |
|---|---|---|
| `anchor`, `alias` | `control: &c DOC-01`, `other: *c` | the value written out in full each time |
| `merge_key` | `<<: *defaults` | the merged keys written out in full |
| `tag` | `name: !!str 10`, `id: !!binary aGk=` | no tag; quote the value to keep it as text: `name: "10"` |
| `directive` | `%YAML 1.1` | no directive line |
| `explicit_key` | `? control` then `: DOC-01` | `control: DOC-01` |
| `second_document` | a second `---` or a `...` line | one document per file |
| `tab` | a tab used for indentation | spaces |
| `non_string_key` | `2026-01-01: done`, `1: a`, `true: x` | a quoted key: `"2026-01-01": done` |
| `duplicate_key` | `owner:` written twice in one mapping | each key once |
| `yaml11_bool` | `answer: yes`, `active: on` | `answer: "yes"`, or `true` / `false` for a boolean |
| `underscore_number` | `limit: 1_000` | `limit: 1000`, or `"1_000"` for text |
| `sexagesimal` | `time: 1:30` | `time: "1:30"` |
| `octal` | `mode: 0755`, `mode: 0o755` | `mode: "0755"` |
| `exponent_no_dot` | `tokens: 1e3` | `tokens: 1000`, or `"1e3"` for text |
| `leading_zero` | `day: 08`, `code: 09` | `day: 8`, or `"08"` for text |
| `exponent_no_sign` | `tokens: 1.0e3` | `tokens: 1.0e+3`, or `"1.0e3"` for text |
| `flow_scalar_indicator_start` | `tools: [?x, :y]` | `tools: ["?x", ":y"]` |
| `flow_line_not_indented` | a `[...]` or `{...}` continued onto a line at or left of its key | indent the continuation past the key, or keep the list on one line |
| `depth_over_100` | mappings or lists nested 101 deep | at most 100 levels |

The YAML 1.1 forms in the table (`yes`, `on`, `1_000`, `1:30`, `0755`, `08`, `1e3`, `1.0e3`) are the ones YAML 1.1
and YAML 1.2 libraries read differently: one engine would read `yes` as true and another as the text
`yes`. Quoting the value makes every engine read the same text.

## What you see

The engine refuses a file outside the profile with exit code `3`, under the same error key it uses
for that file when it cannot read it. The message names the rule and the line, and the fix
says what to write instead:

```text
error [input.deviation_invalid]: the deviation register at 'deviations.yaml' is outside the YAML profile: yaml11_bool at line 3 (SPEC 8.7).
  fix: quote the value to keep it as text.
```

The [error catalogue](/explanation/errors/) lists the keys: `input.catalog_unreadable` for catalog
files, `input.profile_invalid` for the applicability profile, `input.domain_binding_invalid` for the
domain binding and `input.deviation_invalid` for the deviation register.

## Quoting `yes` and `no`

A value meant as the word `yes` or `no` is text, so write it quoted. The base catalogs' checklists
write their answers this way:

```yaml
answers: ["yes", "no", not_applicable]
```

## Tests

One shared case table, [`spec/rules/yaml-profile-cases.json`](https://github.com/agent-conformance/agentce/blob/main/spec/rules/yaml-profile-cases.json),
holds the rules and their edge cases. Each engine's tests read that table, so a case added there runs
in all three engines.
