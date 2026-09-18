"""The fixture loader (docs/10 Step 7).

Most of these are about failing LOUD. A harness that quietly accepts a malformed script is
worse than no harness — it reports a confident green over a check that never ran.
"""

import pytest
from roma.eval import script as script_mod

HEADER = '{"name": "t"}'
TURN = '{"lead": "haan", "expect_phase": "p2_discover"}'


def test_loads_header_and_turns():
    s = script_mod.loads(f"{HEADER}\n{TURN}\n")
    assert s.name == "t"
    assert len(s.turns) == 1
    assert s.turns[0].lead == "haan"
    assert s.turns[0].expect_phase == "p2_discover"


def test_comments_and_blank_lines_are_skipped():
    s = script_mod.loads(f"# a note\n\n{HEADER}\n\n# another\n{TURN}\n")
    assert s.name == "t"
    assert len(s.turns) == 1


def test_unknown_turn_key_is_an_error():
    """The failure this guards is `expect_phse`: a typo'd key would assert NOTHING while
    the run still reported PASS. Silent no-op checks are the one thing a harness must not
    have, so unknown keys are fatal rather than ignored."""
    with pytest.raises(script_mod.ScriptError, match="unknown key"):
        script_mod.loads(f'{HEADER}\n{{"lead": "haan", "expect_phse": "p2_discover"}}\n')


def test_unknown_header_key_is_an_error():
    with pytest.raises(script_mod.ScriptError, match="unknown key"):
        script_mod.loads('{"name": "t", "expect_slot": {}}\n' + TURN)


def test_header_needs_a_name():
    with pytest.raises(script_mod.ScriptError, match="needs a 'name'"):
        script_mod.loads('{"description": "x"}\n' + TURN)


def test_turn_needs_a_lead():
    with pytest.raises(script_mod.ScriptError, match="needs a 'lead'"):
        script_mod.loads(f'{HEADER}\n{{"expect_phase": "p1_open"}}\n')


def test_header_without_turns_is_an_error():
    with pytest.raises(script_mod.ScriptError, match="no turns"):
        script_mod.loads(HEADER + "\n")


def test_empty_script_is_an_error():
    with pytest.raises(script_mod.ScriptError, match="empty"):
        script_mod.loads("\n# only a comment\n")


def test_bad_json_names_the_line():
    with pytest.raises(script_mod.ScriptError, match="line 2"):
        script_mod.loads(f"{HEADER}\n{{not json}}\n")
