# Example rules (synthetic)

Eight demo rules for `sh examples/demo.sh`. The `SYNTHETIC-DATA` file records that none came
from real use. Copy one and edit it. `instinct-delivery test --rules-dir examples/instincts`
runs their cases.

- `no-print-debugging`, `secrets-in-config`, `run-tests-after-src-edit`: rules scoped to
  `Edit` and `Write`. A Bash command that visibly writes a file (`echo ... > f`) triggers them too.
- `force-push-main` and `warn-rm-rf-outside-tmp`: Bash rules that match the command text.
- `lockfile-hand-edit`: a path rule with `status: candidate`. It is logged when it matches and never shown.
- `long-release-checklist`: deliberately larger than the payload budget. `doctor` reports
  OVER BUDGET, and `sh examples/demo.sh --tour` shows it cut to fit.
- `old-retired-rule.yaml`: a negative fixture, not a template. `status: retired` means `doctor`
  counts it and the hook never delivers it. `test` does not run its cases, because a retired
  rule is not loaded.
