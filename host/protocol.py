"""Shared query name representation for the USB protocol."""

NAME_LEN = 24


def board_name(phrase: str) -> str:
    """Fit the NUL-terminated slot without splitting a UTF-8 code point."""
    return phrase.encode("utf-8")[:NAME_LEN - 1].decode("utf-8", "ignore")
