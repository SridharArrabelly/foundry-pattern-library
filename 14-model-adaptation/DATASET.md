# Dataset card

**Task:** industry-neutral enterprise support triage into four stable,
organization-specific routing codes (`AX7`, `BR2`, `CZ9`, `UNSUPPORTED`) with a strict
JSON response. The codes are intentionally opaque: their stable meaning is learned from
reviewed examples rather than from changing business facts.

**Reviewed data:** `train.jsonl` and `validation.jsonl` are checked-in, human-reviewed
examples. `test.jsonl` is a separate held-out set and is never used for training,
validation, prompt examples, or synthetic-data review.

**Data boundaries:** examples contain no customer facts, changing business knowledge,
secrets or personal data. Changing knowledge belongs in RAG, Search, or Foundry IQ.
Lightweight instructions belong in the prompt. Fine-tuning is used here only for stable
classification behavior, exact output shape, concise rationale and refusal of unsupported
categories.

**Synthetic data:** optional generation is explicitly Preview and writes candidates to
`generated/`. Candidates are not consumed by the training command. A human must review
and deduplicate them, and none may be copied into `test.jsonl`.
