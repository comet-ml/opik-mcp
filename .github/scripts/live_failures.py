"""Print the failing test ids in a pytest junit file, one per line.

Used by the live workflow to name the failing tests in its Slack message. A
missing or unreadable file prints one line saying so, because a job that
failed before pytest ran has no junit and still needs a message.

Run: ``python3 .github/scripts/live_failures.py <junit.xml>``
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def failures(path: Path) -> list[str]:
    root = ET.parse(path).getroot()
    return [
        f"{case.get('classname')}::{case.get('name')}"
        for case in root.iter("testcase")
        if case.find("failure") is not None or case.find("error") is not None
    ]


def main() -> int:
    path = Path(sys.argv[1])
    try:
        found = failures(path)
    except (OSError, ET.ParseError):
        sys.stdout.write("the job failed before the tests reported (no junit file)\n")
        return 0
    sys.stdout.write("".join(f"{line}\n" for line in found))
    return 0


if __name__ == "__main__":
    sys.exit(main())
