# ZMAI Benchmark Status

> Updated: 2026-08-07
> Status: pre-release benchmark status statement

## Overview

ZMAI has not yet been formally evaluated on public standard benchmarks (e.g. SWE-bench). The data in this file comes from project-internal validation scenarios; it does not represent public benchmark scores and must not be used for cross-comparison.

ZMAI supports a configurable LLM backend; this validation run used the DeepSeek backend.

## SWE-bench Evaluation Status

- **Not yet evaluated**: no official SWE-bench (Full / Verified / Lite) runs so far
- Planned pipeline:
  1. Prepare SWE-bench environment and data split
  2. Configure an Anthropic-compatible endpoint (local LLM or cloud API)
  3. Run batch evaluation and record pass@1
  4. Publish results with reproduction notes

## Completed Validation (real runs)

All results below come from real agent runs (ZMAI Runtime + configured LLM backend, real API calls). Reproduction materials live in `tests/hermes_validation/`.

### 1. SWE Agent Real Task Validation

Real agent run fixing a code bug (`swe_fix_demo`, driven by ISSUE.md):

- Result: before 2 failed / 2 passed → after **4/4 passed**
- Flow: read task → run tests to confirm failure → analyze source → edit fix → re-run all green → auto-stop
- A P0 EditTool line-joining bug (BUG-001) was found and fixed along the way, closing the "find bug → fix → re-test pass" loop
- Full report: `tests/hermes_validation/ZMAI_TEST_REPORT.md` §7

### 2. Autostop Validation

Two real scenarios, max_iterations=5:

| Scenario | Final status | Actual steps | Stop reason |
|----------|--------------|--------------|-------------|
| A: completable fix task | completed | 4 (< 5) | tests green → CompletionState auto-complete |
| B: uncompletable adversarial task | timeout | 5 (= max) | max_steps hard cap + LoopGuard no_progress double protection |

- Conclusion: no infinite loops, no repeated edits, no meaningless model calls
- Full report: `tests/hermes_validation/autostop_report.md`

### 3. Regression Tests

- Full suite: **1245 passed / 7 skipped**
- Coverage: auth / credential store / gateway / backend / runtime / loop guard / termination / EditTool and more
- Full report: `tests/hermes_validation/regression_report.md`

## Note

- All data in this file is reproducible via scripts and reports under `tests/hermes_validation/`
- Until an official SWE-bench evaluation is completed, this project claims no public benchmark score
