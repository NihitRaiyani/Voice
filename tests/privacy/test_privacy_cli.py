from uuid import uuid4

import pytest
from scripts.privacy import parse_args


def test_subject_cli_is_explicit_preview_and_mutually_exclusive():
    caller_id = uuid4()
    args = parse_args(["--caller-id", str(caller_id)])
    assert args.caller_id == caller_id and not args.apply
    args = parse_args(["--caller-id", str(caller_id), "--apply"])
    assert args.apply
    with pytest.raises(SystemExit):
        parse_args(["--caller-id", str(caller_id), "--call-id", str(uuid4())])
