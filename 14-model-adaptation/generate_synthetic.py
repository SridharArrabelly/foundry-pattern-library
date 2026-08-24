"""Optional Preview synthetic candidate generation for Pattern 14.

Candidates are written only under generated/ and are never consumed automatically by
training or evaluation. Human review and deduplication are mandatory.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from adapt_model import (
    BASE_DIR,
    CATEGORIES,
    OUTPUT_SCHEMA,
    Config,
    INSTRUCTIONS_PATH,
    TEST_PATH,
    TRAIN_PATH,
    VALIDATION_PATH,
    project_clients,
    read_jsonl,
    test_example,
    training_example,
)


GENERATED_DIR = BASE_DIR / "generated"
SYNTHETIC_SCHEMA = {
    "type": "object",
    "properties": {
        "input": {"type": "string", "minLength": 10, "maxLength": 300},
        **OUTPUT_SCHEMA["properties"],
    },
    "required": ["input", "category", "rationale"],
    "additionalProperties": False,
}


def existing_inputs() -> set[str]:
    values = set()
    for split, path in (("train", TRAIN_PATH), ("validation", VALIDATION_PATH)):
        for index, row in enumerate(read_jsonl(path), start=1):
            user_input, _ = training_example(row, f"{split}:{index}")
            values.add(user_input.casefold())
    for index, row in enumerate(read_jsonl(TEST_PATH), start=1):
        _, user_input, _ = test_example(row, f"test:{index}")
        values.add(user_input.casefold())
    return values


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--enable-preview-synthetic",
        action="store_true",
        help="Acknowledge that Foundry synthetic-data generation is Preview.",
    )
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument(
        "--output",
        type=Path,
        default=GENERATED_DIR / "candidates.jsonl",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.enable_preview_synthetic:
        raise SystemExit(
            "Synthetic generation is Preview and disabled by default. "
            "Pass --enable-preview-synthetic only after accepting that boundary."
        )
    if args.count < 1 or args.count > 100:
        raise SystemExit("--count must be between 1 and 100")
    output = args.output.resolve()
    generated_root = GENERATED_DIR.resolve()
    if generated_root not in output.parents:
        raise SystemExit("Synthetic candidates must stay under 14-model-adaptation/generated/")

    config = Config.from_env()
    config.validate()
    seen = existing_inputs()
    candidates = []
    credential, project, client = project_clients(config)
    with credential, project, client:
        for index in range(args.count):
            target = sorted(CATEGORIES)[index % len(CATEGORIES)]
            response = client.responses.create(
                model=config.base_deployment,
                instructions=(
                    "Generate one industry-neutral enterprise support triage example. "
                    f"The correct category must be {target}. Do not include people, email "
                    "addresses, account numbers, secrets, customer facts, or changing "
                    "business knowledge. Return only the requested JSON."
                ),
                input="Create a distinct candidate for human review.",
                temperature=0.8,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "synthetic_triage_candidate",
                        "schema": SYNTHETIC_SCHEMA,
                        "strict": True,
                    }
                },
            )
            if getattr(response, "status", None) != "completed":
                raise RuntimeError(f"synthetic response {index + 1} did not complete")
            candidate = json.loads(response.output_text)
            if candidate["category"] != target:
                raise RuntimeError(
                    f"synthetic response {index + 1} ignored target category {target}"
                )
            key = candidate["input"].strip().casefold()
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "messages": [
                        {
                            "role": "system",
                            "content": INSTRUCTIONS_PATH.read_text(
                                encoding="utf-8"
                            ).strip(),
                        },
                        {"role": "user", "content": candidate["input"].strip()},
                        {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "category": candidate["category"],
                                    "rationale": candidate["rationale"],
                                },
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        },
                    ],
                    "_review_status": "UNREVIEWED_PREVIEW_CANDIDATE",
                }
            )

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        for candidate in candidates:
            stream.write(json.dumps(candidate, sort_keys=True) + "\n")
    print(
        f"Wrote {len(candidates)} UNREVIEWED Preview candidates to {output}. "
        "Training does not consume this file."
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, RuntimeError) as error:
        print(f"SYNTHETIC GENERATION FAILED: {error}", file=sys.stderr)
        sys.exit(1)
