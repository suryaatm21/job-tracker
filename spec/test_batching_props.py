"""BATCH rules: splitting a long Telegram message (telegram_utils.safe_join_lines)."""
from hypothesis import example, given, settings, strategies as st

import telegram_utils

# Telegram's limit is "1-4096 characters after entities parsing"
# (core.telegram.org/bots/api#sendmessage). Entity offsets count UTF-16 units,
# so the spec counts that way: an emoji outside the BMP counts as 2.
TELEGRAM_LIMIT = 4096
FIRST_PREFIX_SEP = "\n\n"
CONT_PREFIX = "(cont.)\n\n"


def tg_len(s):
    return len(s.encode("utf-16-le")) // 2


def one_line(min_size=1, max_size=200, max_codepoint=None):
    chars = st.characters(blacklist_categories=("Cs",), blacklist_characters="\n\r",
                          max_codepoint=max_codepoint)
    return st.text(alphabet=chars, min_size=min_size, max_size=max_size)


def unbatch(header, batches):
    out = []
    for i, b in enumerate(batches):
        prefix = (header + FIRST_PREFIX_SEP) if i == 0 else CONT_PREFIX
        assert b.startswith(prefix), (i, b[:40])
        out.extend(b[len(prefix):].split("\n"))
    return out


@settings(max_examples=2000)
@given(header=one_line(), lines=st.lists(one_line(), min_size=1, max_size=80),
       max_chars=st.integers(min_value=50, max_value=3900))
@example(header="H", lines=["only line"], max_chars=3900)
def test_batch_1_round_trips_lines_in_order(header, lines, max_chars):
    batches = telegram_utils.safe_join_lines(header, lines, max_chars)
    assert unbatch(header, batches) == lines


@settings(max_examples=2000)
@given(header=one_line(max_size=40), lines=st.lists(one_line(max_size=40), min_size=1, max_size=80),
       max_chars=st.integers(min_value=100, max_value=3900))
# 10-character header plus nine 9-character lines: the first message is 101 for a limit of 100.
@example(header="h" * 10, lines=["l" * 9] * 9 + ["tail"], max_chars=100)
def test_batch_2_every_batch_fits_max_chars_when_every_line_fits(header, lines, max_chars):
    for b in telegram_utils.safe_join_lines(header, lines, max_chars):
        assert len(b) <= max_chars, (len(b), max_chars)


@settings(max_examples=1000)
@given(header=one_line(min_size=0, max_size=100, max_codepoint=0xFFFF),
       lines=st.lists(one_line(max_size=300, max_codepoint=0xFFFF), min_size=1, max_size=200))
def test_batch_3_batches_of_normal_lines_fit_telegram(header, lines):
    """Lines up to 300 characters, each character one UTF-16 unit (the Basic Multilingual Plane)."""
    for b in telegram_utils.safe_join_lines(header, lines, 3900):
        assert tg_len(b) <= TELEGRAM_LIMIT, tg_len(b)


@given(n=st.integers(min_value=4000, max_value=6000))
@example(n=4082)
def test_batch_3_one_oversized_line_still_fits_telegram(n):
    batches = telegram_utils.safe_join_lines("Job Digest", ["x" * n], 3900)
    assert all(tg_len(b) <= TELEGRAM_LIMIT for b in batches), [tg_len(b) for b in batches]


@given(lines=st.lists(st.integers(100, 300).map(lambda n: "\U0001F600" * n), min_size=20, max_size=40))
@example(lines=["\U0001F600" * 300] * 20)
def test_batch_3_emoji_heavy_batches_still_fit_telegram(lines):
    """Each emoji outside the BMP is 2 UTF-16 units."""
    for b in telegram_utils.safe_join_lines("Job Digest", lines, 3900):
        assert tg_len(b) <= TELEGRAM_LIMIT, tg_len(b)
