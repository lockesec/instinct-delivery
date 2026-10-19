# Contributing

- `python -m pip install -e '.[test]' && python -m pytest -q`
- Every behaviour claim in the README has a test or a check; `tests/INVARIANTS.md` maps
  them. The quick-start transcript is checked line by line against the real demo
  output by `examples/check_readme.py` (CI runs it), and the "Your first rule" block
  by `tests/test_readme_first_rule.py`. A change that alters a claim changes its test in the same commit.
- A check needs a negative control: a test that fails if the check is removed.
- Example rules in `examples/instincts/` must be synthetic. Never add a rule
  copied from real use, or an activation log from a real session. Each example directory
  carries a `SYNTHETIC-DATA` file stating this; keep it, and add one to any new example directory.
