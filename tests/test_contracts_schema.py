import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "packages" / "contracts" / "assistant-turn.schema.json"
GENERATOR = REPO_ROOT / "tools" / "generate_schema.py"


def test_schema_file_is_in_sync_with_pydantic():
    """The JSON-Schema file must be byte-identical to the generator's output.

    If this fails, run `python tools/generate_schema.py --write` and commit.
    """
    result = subprocess.run(
        [sys.executable, str(GENERATOR)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=True,
    )
    expected = result.stdout
    actual = SCHEMA_PATH.read_text(encoding="utf-8")
    assert actual == expected, (
        f"{SCHEMA_PATH.relative_to(REPO_ROOT)} is out of date. "
        "Run `python tools/generate_schema.py --write` and commit."
    )


def test_generated_schema_is_valid_json():
    payload = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert payload["title"] == "AssistantTurnResponse"
    assert payload["$id"].endswith("assistant-turn.schema.json")
    assert "AssistantTurnResponse" in payload.get("$defs", {}) or "properties" in payload
    # The Pydantic-generated schema exposes the nested types under $defs.
    defs = payload.get("$defs", {})
    assert "AssistantPayload" in defs
    assert "AvatarState" in defs
    assert "AvatarMood" in defs
    assert "MouthCue" in defs
