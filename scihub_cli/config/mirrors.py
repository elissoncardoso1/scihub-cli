"""
Mirror configuration and management for Sci-Hub CLI.
"""

from enum import Enum


class MirrorTier(Enum):
    """Mirror difficulty tiers."""

    EASY = "easy"
    HARD = "hard"


class MirrorConfig:
    """Configuration for Sci-Hub mirrors organized by difficulty."""

    # Mirror configuration by difficulty level.
    #
    # EASY mirrors were verified on 2026-10-09 with
    # MirrorManager(timeout=15)._test_mirror (article-level probe with PDF-link
    # requirement) against the live list on sci-hub.st's mirror section plus
    # the user-supplied directory (sci-hub.al/mk/ee/vg/st, sci-hub.ru/red/box/su).
    # Passes: vg, ru, su (vg/su flaky across rounds; ru recovers). Failures:
    # al and mk serve article HTML without a PDF link via the parser; ee and
    # st did not resolve the probe DOI; red and box did not answer the probe.
    # Domains are volatile; keep this list curated.  Users can also supply a
    # mirror explicitly with ``--mirror``; MirrorManager then validates an
    # article-specific URL before using it.
    MIRROR_TIERS = {
        MirrorTier.EASY: [
            "https://sci-hub.vg",
            "https://sci-hub.ru",
            "https://sci-hub.su",
        ],
        MirrorTier.HARD: [],
    }

    @classmethod
    def get_mirrors_by_tier(cls, tier: MirrorTier) -> list[str]:
        """Get mirrors for a specific tier."""
        return cls.MIRROR_TIERS.get(tier, [])

    @classmethod
    def get_all_mirrors(cls) -> list[str]:
        """Get all mirrors ordered by difficulty (easy first)."""
        return cls.MIRROR_TIERS[MirrorTier.EASY] + cls.MIRROR_TIERS[MirrorTier.HARD]

    @classmethod
    def get_easy_mirrors(cls) -> list[str]:
        """Get only easy mirrors."""
        return cls.MIRROR_TIERS[MirrorTier.EASY]

    @classmethod
    def get_hard_mirrors(cls) -> list[str]:
        """Get only hard mirrors."""
        return cls.MIRROR_TIERS[MirrorTier.HARD]

    @classmethod
    def is_hard_mirror(cls, mirror_url: str) -> bool:
        """Check if a mirror is in the hard tier."""
        return mirror_url in cls.MIRROR_TIERS[MirrorTier.HARD]


# Default mirror configuration
DEFAULT_MIRRORS = MirrorConfig.get_all_mirrors()
