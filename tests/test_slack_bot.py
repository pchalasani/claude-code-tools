"""Pure-function tests for the Slack adapter (slack_bolt-free).

These exercise ONLY the wire-format-free helpers in ``slack_bot`` --
``slack_event_to_incoming`` (Slack event dict -> normalized
:class:`IncomingMessage` or ``None``), ``validate_download`` (the HTML/4xx
download guard), ``discord_to_mrkdwn`` (markdown -> Slack mrkdwn), and
``_SeenCache`` (bounded dedup). They import none of ``slack_bolt`` /
``slack_sdk`` / ``aiohttp`` (those are lazy inside ``run_slack_bot`` /
``SlackTransport``), open no socket, and use no live tokens. Fixture event
dicts are module-level and mirror real Slack ``message`` payloads; every
assertion is a plain ``assert`` against a real :class:`IncomingMessage`,
matching the no-mock style of ``tests/test_discord_bot.py``.
"""

from __future__ import annotations

import asyncio

import pytest

from claude_code_tools.agent_tunnel.chat_types import (
    AttachmentRef,
    Addressee,
    Surface,
)
from claude_code_tools.agent_tunnel.config import TunnelConfig
from claude_code_tools.agent_tunnel.slack_bot import (
    _IGNORE_SUBTYPES,
    _SeenCache,
    SlackTransport,
    discord_to_mrkdwn,
    slack_event_to_incoming,
    validate_download,
)

# -- identities + watched channels --------------------------------------------

BOT_USER_ID = "U0LAN0Z89"  # this bot's own user id (auth.test "user_id")
BOT_ID = "B0LANBOT00"      # this bot's bot id (auth.test "bot_id")
CHANNEL_IDS = {"C123"}     # the single watched channel for most fixtures


# -- fixture event dicts (verbatim-style Slack message payloads) --------------

CHANNEL_EVENT = {
    "type": "message",
    "channel": "C123",
    "user": "U1",
    "text": "pay hello",
    "ts": "1355517523.000005",
    "channel_type": "channel",
}

# A reply living in a thread: thread_ts points at the parent, ts is this reply.
THREAD_REPLY_EVENT = {
    "type": "message",
    "channel": "C123",
    "user": "U1",
    "text": "follow up question",
    "ts": "1700000000.000009",
    "thread_ts": "1700000000.000100",
    "channel_type": "channel",
}

# The thread PARENT itself arrives with thread_ts == ts -> still CHANNEL surface.
THREAD_PARENT_EVENT = {
    "type": "message",
    "channel": "C123",
    "user": "U1",
    "text": "pay parent question",
    "ts": "1700000000.000100",
    "thread_ts": "1700000000.000100",
    "channel_type": "channel",
}

IM_EVENT = {
    "type": "message",
    "channel": "D024BE91L",
    "user": "U1",
    "text": "pay hi there",
    "ts": "1355517523.000010",
    "channel_type": "im",
}

MPIM_EVENT = {
    "type": "message",
    "channel": "Gmpim0001",
    "user": "U1",
    "text": "pay group hi",
    "ts": "1355517523.000011",
    "channel_type": "mpim",
}

# Leading mentions inside a THREAD (mention logic only applies on THREAD).
THREAD_SELF_MENTION_EVENT = {
    "type": "message",
    "channel": "C123",
    "user": "U2",
    "text": f"<@{BOT_USER_ID}> what's up?",
    "ts": "1700000000.000200",
    "thread_ts": "1700000000.000100",
    "channel_type": "channel",
}

THREAD_OTHER_MENTION_EVENT = {
    "type": "message",
    "channel": "C123",
    "user": "U2",
    "text": "<@U999> can you take this?",
    "ts": "1700000000.000201",
    "thread_ts": "1700000000.000100",
    "channel_type": "channel",
}

THREAD_BROADCAST_MENTION_EVENT = {
    "type": "message",
    "channel": "C123",
    "user": "U2",
    "text": "<!here> anyone around?",
    "ts": "1700000000.000202",
    "thread_ts": "1700000000.000100",
    "channel_type": "channel",
}

# On a watched CHANNEL surface, <!here> is just a (non-handle) leading token.
CHANNEL_BROADCAST_EVENT = {
    "type": "message",
    "channel": "C123",
    "user": "U2",
    "text": "<!here> pay please look",
    "ts": "1700000000.000203",
    "channel_type": "channel",
}

# A message carrying a file (regular subtype-less message + files[]).
FILE_EVENT = {
    "type": "message",
    "channel": "C123",
    "user": "U1",
    "text": "",
    "ts": "1700000000.000300",
    "channel_type": "channel",
    "files": [
        {
            "id": "F1",
            "name": "ghostrap.png",
            "size": 12345,
            "url_private_download": "https://files.slack.com/dl/T-F1/ghostrap.png",
            "url_private": "https://files.slack.com/pri/T-F1/ghostrap.png",
        }
    ],
}

# A file with a null name -> filename falls back to file-{id}.
FILE_NULL_NAME_EVENT = {
    "type": "message",
    "channel": "C123",
    "user": "U1",
    "text": "",
    "ts": "1700000000.000301",
    "channel_type": "channel",
    "files": [
        {
            "id": "F1",
            "name": None,
            "size": 999,
            "url_private_download": "https://files.slack.com/files-pri/T-F1/download/x",
        }
    ],
}

# A file with only url_private (no _download) -> url falls back to url_private.
FILE_URL_PRIVATE_ONLY_EVENT = {
    "type": "message",
    "channel": "C123",
    "user": "U1",
    "text": "",
    "ts": "1700000000.000302",
    "channel_type": "channel",
    "files": [
        {
            "id": "F2",
            "name": "notes.txt",
            "size": 42,
            "url_private": "https://files.slack.com/files-pri/T-F2/notes.txt",
        }
    ],
}

# Drop fixtures.
BOT_SUBTYPE_EVENT = {
    "type": "message",
    "subtype": "bot_message",
    "channel": "C123",
    "bot_id": "Bsomeother",
    "text": "I am a bot",
    "ts": "1700000000.000400",
    "channel_type": "channel",
}

OWN_BOT_ID_EVENT = {
    "type": "message",
    "channel": "C123",
    "bot_id": BOT_ID,
    "text": "echo from myself",
    "ts": "1700000000.000401",
    "channel_type": "channel",
}

OWN_USER_EVENT = {
    "type": "message",
    "channel": "D024BE91L",
    "user": BOT_USER_ID,
    "text": "my own DM echo",
    "ts": "1700000000.000402",
    "channel_type": "im",
}

MESSAGE_CHANGED_EVENT = {
    "type": "message",
    "subtype": "message_changed",
    "channel": "C123",
    "ts": "1700000000.000403",
    "channel_type": "channel",
    "message": {"user": "U1", "text": "edited text", "ts": "1700000000.000300"},
}

MESSAGE_DELETED_EVENT = {
    "type": "message",
    "subtype": "message_deleted",
    "channel": "C123",
    "ts": "1700000000.000404",
    "channel_type": "channel",
    "deleted_ts": "1700000000.000300",
}

CHANNEL_JOIN_EVENT = {
    "type": "message",
    "subtype": "channel_join",
    "channel": "C123",
    "user": "U1",
    "text": "<@U1> has joined the channel",
    "ts": "1700000000.000405",
    "channel_type": "channel",
}

# Not a watched channel.
UNWATCHED_CHANNEL_EVENT = {
    "type": "message",
    "channel": "C999",
    "user": "U1",
    "text": "pay hello",
    "ts": "1700000000.000500",
    "channel_type": "channel",
}

# A THREAD reply (thread_ts != ts) in an UNWATCHED channel. Thread replies are
# intentionally NOT re-gated by channel_ids (only top-level CHANNEL posts are).
UNWATCHED_THREAD_REPLY_EVENT = {
    "type": "message",
    "channel": "C999",
    "user": "U1",
    "text": "follow up in an unwatched channel",
    "ts": "1700000000.000501",
    "thread_ts": "1700000000.000100",
    "channel_type": "channel",
}

# A leading <!subteam^S…> usergroup mention inside a THREAD -> Addressee.OTHER.
THREAD_SUBTEAM_MENTION_EVENT = {
    "type": "message",
    "channel": "C123",
    "user": "U2",
    "text": "<!subteam^S0LTEAM> ping",
    "ts": "1700000000.000502",
    "thread_ts": "1700000000.000100",
    "channel_type": "channel",
}

# A self-mention on a CHANNEL surface (no thread_ts): mention logic is THREAD-
# only, so this must stay NONE and the text must NOT be stripped.
CHANNEL_SELF_MENTION_EVENT = {
    "type": "message",
    "channel": "C123",
    "user": "U2",
    "text": f"<@{BOT_USER_ID}> hello",
    "ts": "1700000000.000503",
    "channel_type": "channel",
}

# A DM (im) carrying a single file (url_private only) -> DM surface, 1 file.
DM_FILE_EVENT = {
    "type": "message",
    "channel": "D024BE91L",
    "user": "U1",
    "text": "",
    "ts": "1700000000.000504",
    "channel_type": "im",
    "files": [
        {
            "id": "F9",
            "name": "dm-note.txt",
            "size": 17,
            "url_private": "https://files.slack.com/files-pri/T-F9/dm-note.txt",
        }
    ],
}

# A CHANNEL-surface post with NO ts: cannot anchor a thread -> dropped.
CHANNEL_NO_TS_EVENT = {
    "type": "message",
    "channel": "C123",
    "user": "U1",
    "text": "pay no timestamp",
    "channel_type": "channel",
}


# -- 1. channel message -------------------------------------------------------


def test_channel_message() -> None:
    msg = slack_event_to_incoming(
        CHANNEL_EVENT, bot_user_id=BOT_USER_ID, bot_id=BOT_ID,
        channel_ids=CHANNEL_IDS,
    )
    assert msg is not None
    assert msg.surface is Surface.CHANNEL
    assert msg.text == "pay hello"
    assert msg.author_id == "U1"
    assert msg.channel_id == "C123"
    assert msg.ts == "1355517523.000005"
    assert msg.addressee is Addressee.NONE
    # CHANNEL dest carries no key but DOES carry the triggering ts so
    # open_thread can anchor a Slack thread off it (G-18).
    assert msg.dest.thread_key == ""
    assert msg.dest.surface is Surface.CHANNEL
    assert msg.dest.channel_id == "C123"
    assert msg.dest.thread_ts == "1355517523.000005"


# -- 2. threaded reply vs thread parent ---------------------------------------


def test_threaded_reply_thread_ts_differs() -> None:
    msg = slack_event_to_incoming(
        THREAD_REPLY_EVENT, bot_user_id=BOT_USER_ID, bot_id=BOT_ID,
        channel_ids=CHANNEL_IDS,
    )
    assert msg is not None
    assert msg.surface is Surface.THREAD
    assert msg.dest.thread_key == "th:C123-1700000000.000100"
    assert msg.dest.surface is Surface.THREAD
    assert msg.dest.thread_ts == "1700000000.000100"
    assert msg.dest.channel_id == "C123"
    assert msg.ts == "1700000000.000009"


def test_thread_parent_thread_ts_equals_ts_is_channel() -> None:
    # The parent message of a thread (thread_ts == ts) is NOT a follow-up; it
    # routes as CHANNEL so the handle-opens-thread path runs.
    msg = slack_event_to_incoming(
        THREAD_PARENT_EVENT, bot_user_id=BOT_USER_ID, bot_id=BOT_ID,
        channel_ids=CHANNEL_IDS,
    )
    assert msg is not None
    assert msg.surface is Surface.CHANNEL
    assert msg.dest.thread_key == ""
    assert msg.dest.thread_ts == "1700000000.000100"
    # The thread PARENT routes with a CHANNEL dest (open_thread later reads
    # parent.thread_ts off a CHANNEL-surface dest), so pin dest.surface/channel.
    assert msg.dest.surface is Surface.CHANNEL
    assert msg.dest.channel_id == "C123"


# -- 3. im AND mpim -> DM -----------------------------------------------------


def test_im_message() -> None:
    msg = slack_event_to_incoming(
        IM_EVENT, bot_user_id=BOT_USER_ID, bot_id=BOT_ID,
        channel_ids=CHANNEL_IDS, respond_to_dms=True,
    )
    assert msg is not None
    assert msg.surface is Surface.DM
    assert msg.dest.thread_key == "dm:D024BE91L"
    assert msg.dest.surface is Surface.DM
    assert msg.channel_id == "D024BE91L"


def test_mpim_message() -> None:
    msg = slack_event_to_incoming(
        MPIM_EVENT, bot_user_id=BOT_USER_ID, bot_id=BOT_ID,
        channel_ids=CHANNEL_IDS, respond_to_dms=True,
    )
    assert msg is not None
    assert msg.surface is Surface.DM
    assert msg.dest.thread_key == "dm:Gmpim0001"
    assert msg.channel_id == "Gmpim0001"


# -- 4. leading-mention -> Addressee ------------------------------------------


def test_leading_self_mention_stripped() -> None:
    msg = slack_event_to_incoming(
        THREAD_SELF_MENTION_EVENT, bot_user_id=BOT_USER_ID, bot_id=BOT_ID,
        channel_ids=CHANNEL_IDS,
    )
    assert msg is not None
    assert msg.surface is Surface.THREAD
    assert msg.addressee is Addressee.SELF
    assert msg.text == "what's up?"  # the leading <@bot> is stripped


def test_leading_other_mention_is_other_unstripped() -> None:
    msg = slack_event_to_incoming(
        THREAD_OTHER_MENTION_EVENT, bot_user_id=BOT_USER_ID, bot_id=BOT_ID,
        channel_ids=CHANNEL_IDS,
    )
    assert msg is not None
    assert msg.surface is Surface.THREAD
    assert msg.addressee is Addressee.OTHER
    assert msg.text == "<@U999> can you take this?"  # NOT stripped


def test_leading_broadcast_in_thread_is_other() -> None:
    msg = slack_event_to_incoming(
        THREAD_BROADCAST_MENTION_EVENT, bot_user_id=BOT_USER_ID, bot_id=BOT_ID,
        channel_ids=CHANNEL_IDS,
    )
    assert msg is not None
    assert msg.surface is Surface.THREAD
    assert msg.addressee is Addressee.OTHER


def test_leading_subteam_in_thread_is_other() -> None:
    # The third OTHER-producing leading regex (<!subteam^S…>) -> OTHER, text
    # unstripped, so the bot stays out when teammates ping a usergroup.
    msg = slack_event_to_incoming(
        THREAD_SUBTEAM_MENTION_EVENT, bot_user_id=BOT_USER_ID, bot_id=BOT_ID,
        channel_ids=CHANNEL_IDS,
    )
    assert msg is not None
    assert msg.surface is Surface.THREAD
    assert msg.addressee is Addressee.OTHER
    assert msg.text == "<!subteam^S0LTEAM> ping"  # NOT stripped


def test_leading_broadcast_on_channel_is_none() -> None:
    # On CHANNEL the handle-token parse governs; addressee stays NONE so the
    # core treats "<!here>" as a (non-handle) token and stays silent (G-broadcast).
    msg = slack_event_to_incoming(
        CHANNEL_BROADCAST_EVENT, bot_user_id=BOT_USER_ID, bot_id=BOT_ID,
        channel_ids=CHANNEL_IDS,
    )
    assert msg is not None
    assert msg.surface is Surface.CHANNEL
    assert msg.addressee is Addressee.NONE
    assert msg.text == "<!here> pay please look"  # left intact for token parse


def test_self_mention_on_channel_is_none_unstripped() -> None:
    # Mention logic is THREAD-only: a leading <@bot> on a CHANNEL surface (no
    # thread_ts) must stay NONE and the text must NOT be stripped.
    msg = slack_event_to_incoming(
        CHANNEL_SELF_MENTION_EVENT, bot_user_id=BOT_USER_ID, bot_id=BOT_ID,
        channel_ids=CHANNEL_IDS,
    )
    assert msg is not None
    assert msg.surface is Surface.CHANNEL
    assert msg.addressee is Addressee.NONE
    assert msg.text == f"<@{BOT_USER_ID}> hello"  # unstripped on CHANNEL


# -- 5. message with files ----------------------------------------------------


def test_message_with_files_not_dropped() -> None:
    msg = slack_event_to_incoming(
        FILE_EVENT, bot_user_id=BOT_USER_ID, bot_id=BOT_ID,
        channel_ids=CHANNEL_IDS,
    )
    assert msg is not None  # attachment-only (text=="") is NOT dropped
    assert msg.text == ""
    file_dict = FILE_EVENT["files"][0]
    assert msg.attachments == (
        AttachmentRef(
            "ghostrap.png",
            12345,
            url="https://files.slack.com/dl/T-F1/ghostrap.png",
            _native=file_dict,
        ),
    )


def test_message_with_file_null_name_falls_back() -> None:
    msg = slack_event_to_incoming(
        FILE_NULL_NAME_EVENT, bot_user_id=BOT_USER_ID, bot_id=BOT_ID,
        channel_ids=CHANNEL_IDS,
    )
    assert msg is not None
    assert len(msg.attachments) == 1
    att = msg.attachments[0]
    assert att.filename == "file-F1"  # null name -> file-{id}
    assert att.size == 999


def test_message_with_file_url_private_fallback() -> None:
    msg = slack_event_to_incoming(
        FILE_URL_PRIVATE_ONLY_EVENT, bot_user_id=BOT_USER_ID, bot_id=BOT_ID,
        channel_ids=CHANNEL_IDS,
    )
    assert msg is not None
    assert len(msg.attachments) == 1
    att = msg.attachments[0]
    assert att.filename == "notes.txt"
    # no url_private_download -> url_private is used.
    assert att.url == "https://files.slack.com/files-pri/T-F2/notes.txt"


def test_dm_with_file_preserved() -> None:
    # An attachment-only DM (im) keeps its file on the DM surface (the files[]
    # parse is shared, but the DM branch sets a different dest).
    msg = slack_event_to_incoming(
        DM_FILE_EVENT, bot_user_id=BOT_USER_ID, bot_id=BOT_ID,
        channel_ids=CHANNEL_IDS, respond_to_dms=True,
    )
    assert msg is not None
    assert msg.surface is Surface.DM
    assert msg.dest.thread_key == "dm:D024BE91L"
    assert len(msg.attachments) == 1
    assert (
        msg.attachments[0].url
        == "https://files.slack.com/files-pri/T-F9/dm-note.txt"
    )


# -- 6. bot / edit / delete / own / system messages -> None -------------------


def test_bot_subtype_ignored() -> None:
    assert slack_event_to_incoming(
        BOT_SUBTYPE_EVENT, bot_user_id=BOT_USER_ID, bot_id=BOT_ID,
        channel_ids=CHANNEL_IDS,
    ) is None


def test_own_bot_id_ignored() -> None:
    assert slack_event_to_incoming(
        OWN_BOT_ID_EVENT, bot_user_id=BOT_USER_ID, bot_id=BOT_ID,
        channel_ids=CHANNEL_IDS,
    ) is None


def test_own_user_ignored() -> None:
    # Own user id in an im (respond_to_dms on) is still dropped as a self-loop.
    assert slack_event_to_incoming(
        OWN_USER_EVENT, bot_user_id=BOT_USER_ID, bot_id=BOT_ID,
        channel_ids=CHANNEL_IDS, respond_to_dms=True,
    ) is None


def test_message_changed_ignored() -> None:
    assert slack_event_to_incoming(
        MESSAGE_CHANGED_EVENT, bot_user_id=BOT_USER_ID, bot_id=BOT_ID,
        channel_ids=CHANNEL_IDS,
    ) is None


def test_message_deleted_ignored() -> None:
    assert slack_event_to_incoming(
        MESSAGE_DELETED_EVENT, bot_user_id=BOT_USER_ID, bot_id=BOT_ID,
        channel_ids=CHANNEL_IDS,
    ) is None


def test_channel_join_ignored() -> None:
    assert slack_event_to_incoming(
        CHANNEL_JOIN_EVENT, bot_user_id=BOT_USER_ID, bot_id=BOT_ID,
        channel_ids=CHANNEL_IDS,
    ) is None


@pytest.mark.parametrize("subtype", sorted(_IGNORE_SUBTYPES))
def test_ignored_subtypes_all_dropped(subtype: str) -> None:
    # Pin the WHOLE housekeeping-noise trust boundary: every subtype in
    # _IGNORE_SUBTYPES drops, so a typo/removal of any one is caught (not just
    # the message_changed/_deleted/channel_join/bot_message that have fixtures).
    event = {
        "type": "message",
        "subtype": subtype,
        "channel": "C123",
        "user": "U1",
        "text": "housekeeping noise",
        "ts": "1700000000.000600",
        "channel_type": "channel",
    }
    assert slack_event_to_incoming(
        event, bot_user_id=BOT_USER_ID, bot_id=BOT_ID,
        channel_ids=CHANNEL_IDS,
    ) is None


# -- 7. watched-channel gate + respond_to_dms gate ----------------------------


def test_channel_not_watched_dropped() -> None:
    assert slack_event_to_incoming(
        UNWATCHED_CHANNEL_EVENT, bot_user_id=BOT_USER_ID, bot_id=BOT_ID,
        channel_ids=CHANNEL_IDS,
    ) is None


def test_dm_respond_to_dms_gate() -> None:
    # DMs are NOT gated by channel_ids, but ARE gated by respond_to_dms.
    enabled = slack_event_to_incoming(
        IM_EVENT, bot_user_id=BOT_USER_ID, bot_id=BOT_ID,
        channel_ids=CHANNEL_IDS, respond_to_dms=True,
    )
    assert enabled is not None and enabled.surface is Surface.DM
    disabled = slack_event_to_incoming(
        IM_EVENT, bot_user_id=BOT_USER_ID, bot_id=BOT_ID,
        channel_ids=CHANNEL_IDS, respond_to_dms=False,
    )
    assert disabled is None  # DMs off -> dropped


def test_mpim_respond_to_dms_off_dropped() -> None:
    # The mpim off-gate path (not just im): respond_to_dms=False drops it.
    assert slack_event_to_incoming(
        MPIM_EVENT, bot_user_id=BOT_USER_ID, bot_id=BOT_ID,
        channel_ids=CHANNEL_IDS, respond_to_dms=False,
    ) is None


def test_thread_reply_in_unwatched_channel_still_routes() -> None:
    # SECURITY-RELEVANT routing rule: a THREAD reply (thread_ts != ts) is NOT
    # re-gated by channel_ids. A reply in C999 (NOT watched) still routes as a
    # THREAD. Only top-level CHANNEL posts are gated; pin this so a future
    # "tighten the gate" refactor that breaks the thread-bypass is caught.
    msg = slack_event_to_incoming(
        UNWATCHED_THREAD_REPLY_EVENT, bot_user_id=BOT_USER_ID, bot_id=BOT_ID,
        channel_ids=CHANNEL_IDS,
    )
    assert msg is not None
    assert msg.surface is Surface.THREAD
    assert msg.dest.thread_key == "th:C999-1700000000.000100"
    assert msg.dest.channel_id == "C999"


def test_channel_no_ts_dropped() -> None:
    # A CHANNEL-surface post with no ts cannot anchor a thread -> dropped (the
    # binding would key on "th:{channel}-" and a real reply could never match).
    assert slack_event_to_incoming(
        CHANNEL_NO_TS_EVENT, bot_user_id=BOT_USER_ID, bot_id=BOT_ID,
        channel_ids=CHANNEL_IDS,
    ) is None


def test_empty_bot_ids_does_not_drop_normal_message() -> None:
    # G-20 bootstrap window: on the very FIRST event (before auth.test populates
    # the ids) bot_user_id/bot_id are still "". The `bot_id and …` /
    # `bot_user_id and …` short-circuits must keep a normal user message from
    # self-dropping, so it still classifies as CHANNEL.
    msg = slack_event_to_incoming(
        CHANNEL_EVENT, bot_user_id="", bot_id="", channel_ids=CHANNEL_IDS,
    )
    assert msg is not None
    assert msg.surface is Surface.CHANNEL


# -- 8. resolve_display -------------------------------------------------------


def test_resolve_display_used() -> None:
    names = {"U1": "Alice"}

    def resolver(uid: str) -> str:
        # Annotated -> (str) -> str (a bare `names.get(uid, uid)` lambda would
        # infer `str | None` and trip the resolve_display: Callable[[str], str]
        # parameter).
        return names.get(uid, uid)

    known = slack_event_to_incoming(
        CHANNEL_EVENT, bot_user_id=BOT_USER_ID, bot_id=BOT_ID,
        channel_ids=CHANNEL_IDS, resolve_display=resolver,
    )
    assert known is not None and known.author_display == "Alice"
    # an unknown user resolves to its raw id (the no-op fallback).
    other = dict(CHANNEL_EVENT, user="U2")
    unknown = slack_event_to_incoming(
        other, bot_user_id=BOT_USER_ID, bot_id=BOT_ID,
        channel_ids=CHANNEL_IDS, resolve_display=resolver,
    )
    assert unknown is not None and unknown.author_display == "U2"


# -- 9. validate_download HTML / 4xx guard ------------------------------------


def test_validate_download_html_guard_raises() -> None:
    # text/html (a login page on a stripped-auth redirect) is an auth failure.
    with pytest.raises(RuntimeError):
        validate_download("text/html; charset=utf-8", 200)


def test_validate_download_status_4xx_raises() -> None:
    with pytest.raises(RuntimeError):
        validate_download("image/png", 404)
    with pytest.raises(RuntimeError):
        validate_download("application/octet-stream", 500)
    # 400 is the EXACT failure boundary (status >= 400).
    with pytest.raises(RuntimeError):
        validate_download("image/png", 400)


def test_validate_download_ok_does_not_raise() -> None:
    # a real binary download returns None (no raise).
    assert validate_download("image/png", 200) is None
    # 399 is just below the boundary -> ok.
    assert validate_download("image/png", 399) is None
    # A missing / empty Content-Type on a real 200 must NOT be mistaken for an
    # HTML login page (the `(content_type or "")` None-guard).
    assert validate_download(None, 200) is None
    assert validate_download("", 200) is None


# -- 10. discord_to_mrkdwn translations ---------------------------------------


def test_discord_to_mrkdwn_bold() -> None:
    assert discord_to_mrkdwn("**b**") == "*b*"


def test_discord_to_mrkdwn_link() -> None:
    assert discord_to_mrkdwn("[t](u)") == "<u|t>"


def test_discord_to_mrkdwn_strikethrough() -> None:
    assert discord_to_mrkdwn("~~s~~") == "~s~"


def test_discord_to_mrkdwn_escapes_amp_lt_gt_first() -> None:
    # & < > are HTML-escaped BEFORE any markup translation, so a literal
    # "a < b && c > d" round-trips fully escaped and unmangled.
    assert (
        discord_to_mrkdwn("a < b && c > d")
        == "a &lt; b &amp;&amp; c &gt; d"
    )


def test_discord_to_mrkdwn_code_passthrough() -> None:
    # inline code and fenced blocks survive translation unchanged.
    assert discord_to_mrkdwn("`x`") == "`x`"
    fence = "```\ncode line\n```"
    assert discord_to_mrkdwn(fence) == fence


def test_discord_to_mrkdwn_link_url_with_amp_escaped_then_linked() -> None:
    # The load-bearing reason escaping runs FIRST: a link URL containing '&'
    # (a query string — the common case) is escaped to &amp; THEN wrapped as
    # <url|text>; Slack unescapes &amp; on render, so the link survives intact.
    assert (
        discord_to_mrkdwn("[click](http://x?a=1&b=2)")
        == "<http://x?a=1&amp;b=2|click>"
    )


def test_discord_to_mrkdwn_bold_link_combo() -> None:
    # bold wrapping a link: **[t](u)** -> *<u|t>* (markup order is stable).
    assert discord_to_mrkdwn("**[t](u)**") == "*<u|t>*"


def test_discord_to_mrkdwn_bold_across_newline() -> None:
    # the **bold** regex uses re.DOTALL, so a bold span across a newline folds.
    assert discord_to_mrkdwn("**a\nb**") == "*a\nb*"


# -- 11. _SeenCache add / dup / capacity --------------------------------------


def test_seen_cache_add_then_dup() -> None:
    seen = _SeenCache(cap=4)
    assert seen.add("k1") is True   # first sight -> accepted
    assert seen.add("k1") is False  # repeat -> rejected (dedup hit)


def test_seen_cache_capacity_evicts_oldest() -> None:
    seen = _SeenCache(cap=4)
    for k in ("a", "b", "c", "d"):
        assert seen.add(k) is True
    # exceeding cap evicts the oldest ("a"); "a" is then treated as new again.
    assert seen.add("e") is True
    assert seen.add("a") is True
    # a still-resident recent key remains a duplicate.
    assert seen.add("e") is False


def test_seen_cache_dup_does_not_refresh_recency() -> None:
    # Eviction is by INSERTION order, NOT recency: re-adding a present key does
    # NOT move it to the end. Pins insertion-order dedup and FORBIDS an accidental
    # LRU change (a move_to_end in add() would silently alter which retries drop).
    # cap=3 so a single overflow makes one clean, unambiguous eviction.
    seen = _SeenCache(cap=3)
    assert seen.add("a") is True
    assert seen.add("b") is True
    assert seen.add("c") is True
    assert seen.add("a") is False  # dup, must NOT refresh a's position
    # The load-bearing assertion: adding "d" overflows cap=3 and evicts the
    # OLDEST BY INSERTION ("a"), NOT "b" — even though "a" was "touched" most
    # recently by the dup add above. Under an LRU (move_to_end on dup) the dup
    # would have moved "a" to the newest slot, so "b" would be evicted instead.
    assert seen.add("d") is True
    # Check the survivors FIRST (these are pure reads — a dup add never mutates):
    # "b" and "c" stayed resident; under an LRU "b" would have been evicted.
    assert seen.add("b") is False  # b resident (would be gone under LRU)
    assert seen.add("c") is False  # c resident
    assert seen.add("d") is False  # d resident
    # ...and only now confirm "a" itself WAS the one evicted (mutates state).
    assert seen.add("a") is True   # a was evicted -> seen as new again


# -- SlackTransport usergroup resolution (Codex P2) ---------------------------
# Fills IncomingMessage.author_role_ids from subteam membership so a
# `slack.allowed_usergroup_ids` allowlist actually works. Uses a real fake
# async client stub (no slack_bolt, no network, no mocks).


class _FakeUGClient:
    """Minimal async Slack client stub for usergroups.users.list."""

    def __init__(self, members: dict) -> None:
        self._members = members
        self.calls: list[str] = []

    async def usergroups_users_list(self, *, usergroup: str) -> dict:
        self.calls.append(usergroup)
        return {"users": self._members.get(usergroup, [])}


class _FakeApp:
    def __init__(self, client: _FakeUGClient) -> None:
        self.client = client


def _slack_cfg(tmp_path, usergroups=()) -> TunnelConfig:
    cfg = TunnelConfig(
        state_path=tmp_path / "s.json", registry_path=tmp_path / "r.json"
    )
    cfg.slack.allowed_usergroup_ids = list(usergroups)
    return cfg


def test_resolve_usergroups_matches_membership(tmp_path) -> None:
    cfg = _slack_cfg(tmp_path, usergroups=["S1", "S2"])
    client = _FakeUGClient({"S1": ["U1", "U2"], "S2": ["U9"]})
    tx = SlackTransport(_FakeApp(client), "xoxb-test", cfg)
    assert asyncio.run(tx.resolve_usergroups("U1")) == frozenset({"S1"})
    assert asyncio.run(tx.resolve_usergroups("U9")) == frozenset({"S2"})
    assert asyncio.run(tx.resolve_usergroups("U404")) == frozenset()
    # membership is cached: later lookups re-list no already-seen subteam
    seen = len(client.calls)
    asyncio.run(tx.resolve_usergroups("U2"))
    assert len(client.calls) == seen


def test_resolve_usergroups_noop_when_unconfigured(tmp_path) -> None:
    cfg = _slack_cfg(tmp_path)  # allowed_usergroup_ids empty
    client = _FakeUGClient({"S1": ["U1"]})
    tx = SlackTransport(_FakeApp(client), "xoxb-test", cfg)
    assert asyncio.run(tx.resolve_usergroups("U1")) == frozenset()
    assert client.calls == []  # zero API calls on the common path
