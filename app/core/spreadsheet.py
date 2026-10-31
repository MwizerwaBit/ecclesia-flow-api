"""Keeping exported text from being run as a spreadsheet formula."""

import re

_NUMBERLIKE = re.compile(r"^[+-]?[\d\s().-]+$")


def safe_cell(value: str) -> str:
    """A spreadsheet runs a cell that starts with = + - @ as a formula. Prefix
    those with a quote so exported text stays text (phone numbers and other
    plain numbers are left alone, so a leading + survives)."""
    if value and value[0] in "=@\t\r":
        return "'" + value
    if value and value[0] in "+-" and not _NUMBERLIKE.match(value):
        return "'" + value
    return value


def unsafe_cell(value: str) -> str:
    """Undo safe_cell on the way back in, so an export can be imported again."""
    if len(value) > 1 and value[0] == "'" and value[1] in "=@+-\t\r":
        return value[1:]
    return value
