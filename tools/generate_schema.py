"""Regenerate packages/contracts/assistant-turn.schema.json from Pydantic.

Run from the repo root:

    .venv/bin/python tools/generate_schema.py          # print to stdout
    .venv/bin/python tools/generate_schema.py --write  # overwrite the file

The Pydantic model is the source of truth. Keeping the JSON-Schema file
in sync is now a one-line command rather than a hand-edited document.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "packages" / "contracts" / "assistant-turn.schema.json"

sys.path.insert(0, str(REPO_ROOT))

from apps.api.contracts import ConversationTurnResponse  # noqa: E402


def build_schema() -> dict:
    schema = ConversationTurnResponse.model_json_schema()
    schema.pop("title", None)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://chirplet.local/contracts/assistant-turn.schema.json"
    schema["title"] = "AssistantTurnResponse"
    schema.setdefault("$defs", {}).pop("ChatTurn", None)
    return schema


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help="Overwrite the schema file instead of printing."
    )
    args = parser.parse_args()

    rendered = json.dumps(build_schema(), indent=2, ensure_ascii=False) + "\n"

    if args.write:
        SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
        SCHEMA_PATH.write_text(rendered, encoding="utf-8")
        print(f"wrote {SCHEMA_PATH}")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
