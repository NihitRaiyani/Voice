"""Single-use tokens for the `/ws` socket (roma.telephony.streamauth).

This replaced the Twilio account-SID comparison, which was the only authentication the
endpoint had. `/ws` answers with a live microphone feed of a lead's conversation, so
"anyone who learns the URL" is not an acceptable audience.
"""

from roma.telephony.streamauth import MAX_PENDING, PendingStreams


def test_a_minted_token_is_accepted_once():
    p = PendingStreams()
    token = p.mint("CA_1")
    assert p.redeem(token) == (True, "CA_1")


def test_a_token_cannot_be_replayed():
    """The socket URL appears in logs, in the answer XML, and in Vobiz's own records. Once
    the call it belonged to has connected, a copy of it must be worthless."""
    p = PendingStreams()
    token = p.mint()
    assert p.redeem(token)[0] is True
    assert p.redeem(token)[0] is False


def test_an_unknown_or_missing_token_is_refused():
    p = PendingStreams()
    assert p.redeem("forged")[0] is False
    assert p.redeem(None)[0] is False
    assert p.redeem("")[0] is False


def test_tokens_expire():
    """A token only has to cover answer -> connect, not the call. An answer that never
    produced a socket must not leave a working key lying around."""
    p = PendingStreams(ttl_secs=10.0)
    token = p.mint(now=0.0)
    assert p.redeem(token, now=5.0)[0] is True

    p2 = PendingStreams(ttl_secs=10.0)
    stale = p2.mint(now=0.0)
    assert p2.redeem(stale, now=11.0)[0] is False


def test_two_calls_cannot_redeem_each_others_sockets():
    p = PendingStreams()
    a, b = p.mint("CA_a"), p.mint("CA_b")
    assert a != b
    assert p.redeem(a) == (True, "CA_a")
    assert p.redeem(b) == (True, "CA_b")


def test_unredeemed_tokens_cannot_grow_without_bound():
    """A carrier that fetches the answer URL and never connects would otherwise leak an entry
    per attempt, for the life of the process."""
    p = PendingStreams(ttl_secs=1e9, max_pending=4)
    for _ in range(50):
        p.mint()
    assert len(p) <= 4


def test_the_counters_make_rejections_visible():
    """A silent rejection looks identical to a carrier that never called."""
    p = PendingStreams()
    p.redeem("forged")
    token = p.mint()
    p.redeem(token)
    assert (p.minted, p.redeemed, p.rejected) == (1, 1, 1)


def test_the_shipped_bound_is_sane():
    assert MAX_PENDING >= 16
