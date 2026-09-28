import json

import pytest

from secondbrain.sidecar import SCHEMA_VERSION, capture_to_sidecar, sidecar_to_fields
from tests.helpers import NOW, make_capture


def test_round_trip_through_json():
    cap = make_capture(title="Idea per il digest", trashed_at=NOW, battery_pct=95)
    data = json.loads(json.dumps(capture_to_sidecar(cap)))
    assert data["schema_version"] == SCHEMA_VERSION
    assert "rel_path" not in data and "day" not in data
    fields = sidecar_to_fields(data)
    for key, value in fields.items():
        assert getattr(cap, key) == value, key


def test_unknown_schema_version():
    data = capture_to_sidecar(make_capture())
    data["schema_version"] = 99
    with pytest.raises(ValueError, match="schema"):
        sidecar_to_fields(data)


def test_missing_required_field():
    data = capture_to_sidecar(make_capture())
    del data["sha256"]
    with pytest.raises(ValueError, match="sha256"):
        sidecar_to_fields(data)
