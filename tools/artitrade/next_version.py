#!/usr/bin/env python3
"""Next ARTITRADE version under the owner's rule (USER_STANDING_RULES.md R1).

0.0.20 is the last 0.0.x. Then 0.1.0 ... 0.1.9, 0.2.0 ... : the last number counts
0..9, after 9 the middle number increases by one and the last restarts at 0.
Numeric dot-separated only; nothing else is ever part of a version.
"""
import re
import sys

PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def parse(version):
    m = PATTERN.match(version)
    if not m:
        raise ValueError("version must be digits separated by dots, e.g. 0.1.0: %r" % version)
    return tuple(int(x) for x in m.groups())


def next_version(version):
    major, minor, patch = parse(version)
    if patch >= 9:                      # also covers 0.0.20 -> 0.1.0
        return "%d.%d.0" % (major, minor + 1)
    return "%d.%d.%d" % (major, minor, patch + 1)


if __name__ == "__main__":
    print(next_version(sys.argv[1]))
