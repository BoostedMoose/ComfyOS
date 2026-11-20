"""Public check values of the licence system – contains no secrets.

Written by `tools/mint_gift.py --init` from `tools/build_secrets.env` (gitignored, only local and in the
password manager). Only the SHA-256 hash of the owner token and the public Ed25519 key live here:
neither the token nor the signing key can be computed back from them.

Several values per list are allowed (key change: add the new value, remove the old one when needed).
Empty lists = owner token and gift files are switched off, Basic applies.
"""
from __future__ import annotations

OWNER_TOKEN_HASHES: tuple[str, ...] = (
    "ffd07e9e74e2d29ff2dca98b63525c8a45949bba5d030306ae13e5d980386656",
)
SIGN_PUBS: tuple[str, ...] = (
    "7022f7dfa8fd4aed7d8eaa5122f3a0448c26a74127e6d5740846ca68b0cd0ff2",
)
# Ids (`id`) of revoked gift licences; they take effect from the release that contains them.
REVOKED: frozenset[str] = frozenset()
