"""Consistent-hash ring for store-to-agent affinity.

The hash is SHA-256. The same store id always lands on the same healthy
region, and only the ranges of an unhealthy region move.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


def token_for(value: str) -> int:
    digest = hashlib.sha256(value.encode()).digest()
    return int.from_bytes(digest[:8], "big")


@dataclass(frozen=True)
class _Node:
    token: int
    region: str
    vnode: int


class HashRing:
    def __init__(self, regions: tuple[str, ...] = ("north", "south", "east", "west"), vnodes: int = 3) -> None:
        if vnodes < 1:
            raise ValueError("vnodes must be positive")
        if not regions:
            raise ValueError("regions must not be empty")
        self.regions = regions
        self.vnodes = vnodes
        self.unhealthy: set[str] = set()
        nodes = [_Node(token_for(f"{region}:{vnode}"), region, vnode) for region in regions for vnode in range(vnodes)]
        nodes.sort(key=lambda node: (node.token, node.region, node.vnode))
        self._ring = nodes

    def set_health(self, region: str, *, healthy: bool) -> None:
        if region not in self.regions:
            raise KeyError(region)
        if healthy:
            self.unhealthy.discard(region)
        else:
            self.unhealthy.add(region)

    def owner(self, store_id: str) -> str:
        healthy = [node for node in self._ring if node.region not in self.unhealthy]
        if not healthy:
            raise RuntimeError("no healthy agent region")
        point = token_for(store_id)
        for node in healthy:
            if node.token >= point:
                return node.region
        return healthy[0].region
