# bench/

What is left of the earlier benchmark harness (removed 2026-10-12; see git history before that
date and `docs/results/`, `docs/archive/` for the experiments). Everything here is used by a
live test:

- task corpora and reference solutions (`tasks.py`, `api_tasks.py`, `bench2_*.py`, ...);
- test helpers (`compare.py`, `api_runner.py`, `sandbox.py`, `metrics.py`, ...);
- fixture apps the importer and spec tests read as source (`template_app/`, `import_repos/`,
  `imports/`, `specs/`);
- the `edit_slice/` modules those tests import.

Do not add experiment runners here.
