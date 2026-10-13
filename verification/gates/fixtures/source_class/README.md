# source_class

Fixtures for `VG-SOURCE-CLASS` (18.127), run by `verification/gates/source_class.sh` in Python, TypeScript and
Java. Implements SPEC HR-5 and §6.4 (a control's `min_source_class` holds per assertion, in the order self_report <
enforcement_point < independent_system; below it the outcome is `insufficient_evidence`, never conformant), §7.1
(`evaluation.min_source_class` and `evaluation.source_class_from`), §7.7.4 and §7.7.5 (CND-04 and CND-05 judge the
acted-on instruction, which counts as enforcement-point evidence only when an enforcement-point record attests it),
§10.4 (the twin and PolicyDecision witnesses), §6.2.3 (a Ref resolves only by its graph IRI) and Appendix F (the
authorised instruction classes).

`cases.json` holds five kinds of case:

- `probe-*`: the audit probes A to J, each beside an `*-attested-*` twin that an enforcement-point record attests and
  that stays conformant. `control` names the control judged; `expected` is its outcome, failing event ids and the
  assess exit code.
- `hostile-*`: corroboration that must not lift a self_report instruction. Each differs by one field from a
  `hostile-base-attested-*` case, which is conformant.
- `unknown-class-*`: no events. The gate copies `spec/catalogs/overlays/conduct`, sets CND-05's `min_source_class` to
  the value given (or removes it), and every engine must refuse the catalog at load with
  `catalog.control.min_source_class_unknown` and exit 3; Python's `catalog lint` must refuse it too.
- `decision-no-instruction-*`: the agent's own consequential decision names no instruction. It is judged through the
  enforcement-point calls that execute it, and as the self_report record it is when none of them acts on an attested
  instruction; it is never left unjudged.
- `source-class-from-unknown-*`: no events. As `unknown-class-*`, but CND-05's `source_class_from` is set to the value
  given, and the refusal key is `catalog.control.source_class_from_unknown`.

`applicability.yaml` names the baseline catalog and the Conduct overlay and two subjects (the other one holds the
twin recorded under another subject); `domain.linkml.yaml` makes `dom:CreditDecision` consequential.
