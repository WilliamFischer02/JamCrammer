"""Stubs for the experimental games that KICKOFF §9.4 defers (notes in handoff.md).

Super Sort: binary color classification (pink plastic vs blue paper) → arrow key; the item
goes to the side *opposite* the key. Needs: item ROI, two HSV ranges, key mapping, a payout
measurement first (formula undocumented; display caps at 999 but pays in full).

Falling Phantoms: left/right dodging with a dense final phase; reaction-critical. Needs a
60 fps lane tracker like Swoopy's and a Learn session to see the phantom patterns.
"""

from __future__ import annotations

from jamcrammer.core.logging import get_logger

log = get_logger("arcade.stubs")

NOT_BUILT = "not built: see handoff.md (experimental, after M11)"


class SuperSortFarmer:
    name = "super_sort"

    def run(self, *_args: object, **_kwargs: object) -> None:
        raise NotImplementedError(NOT_BUILT)


class FallingPhantomsFarmer:
    name = "falling_phantoms"

    def run(self, *_args: object, **_kwargs: object) -> None:
        raise NotImplementedError(NOT_BUILT)
