"""Remove provider-private display controls before binding a new send identity."""
import re

_CONTROLS = re.compile(r'\ue200[^\ue201]*\ue201|[\ue000-\uf8ff\U000f0000-\U000ffffd\U00100000-\U0010fffd]')


def visible_text(text):
    # Keep ordinary prose, Unicode, whitespace and authored URLs byte-for-byte.
    # Closed citation spans are metadata, not usable room citations. Unmatched
    # private-use characters are removed without swallowing adjacent prose.
    return _CONTROLS.sub('', text)
