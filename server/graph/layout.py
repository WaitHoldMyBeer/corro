"""Static layout: every node gets its position here, once, and the page never moves it.

Clusters (a hub and its members) are discs. The matter sits at the origin and the
discs are packed around it greedily, each family of clusters in its own sector so
that folders stay with folders and people with people. Clusters marked late (documents
added outside Clio) are fitted in afterwards, into room held open for them, so adding one
moves nothing that was already drawn. Inside a disc the members
sit on a sunflower spiral, which spaces them almost evenly, so radii can be chosen
that never overlap. Nothing here is random and nothing is iterated to convergence:
the same clusters in the same order give the same picture.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

GOLDEN_ANGLE = math.pi * (3 - math.sqrt(5))
SPACING = 30.0  # spiral constant: neighbours end up about 1.77 x this apart
INNER = 3.5  # spiral slots left empty around the hub, so the first member clears a hub drawn oversize
CLUSTER_PAD = 26.0  # clear ring around a cluster: room for claim nodes and labels
CENTRE_CLEAR = 70.0  # nothing but the matter inside this radius
STEP = 14.0  # search step for the packing, radial and along the arc
MIN_SECTOR = math.radians(24)
ASPECT = 1.6  # the packing is this much wider than tall, to suit a landscape stage; clusters stay round


@dataclass
class Cluster:
    key: str
    family: int
    hub: int  # node index; -1 for room held open with nothing in it yet
    members: list[int] = field(default_factory=list)  # node indexes, in the order they spiral outward
    room: int = 0  # sized for at least this many members, so it need not move as it fills
    x: float = 0.0
    y: float = 0.0

    @property
    def radius(self) -> float:
        return SPACING * math.sqrt(max(len(self.members), self.room) + INNER) + CLUSTER_PAD


def member_offset(k: int, phase: float) -> tuple[float, float]:
    distance = SPACING * math.sqrt(k + INNER)
    angle = phase + k * GOLDEN_ANGLE
    return distance * math.cos(angle), distance * math.sin(angle)


def _sectors(clusters: list[Cluster], families: int) -> list[tuple[float, float]]:
    """(centre angle, half width) per family, width in proportion to the area its clusters need."""
    area = [0.0] * families
    for cluster in clusters:
        area[cluster.family] += cluster.radius**2
    used = [index for index in range(families) if area[index] > 0]
    spare = 2 * math.pi - MIN_SECTOR * len(used)
    total = sum(area) or 1.0
    out, start = [(0.0, 0.0)] * families, -math.pi / 2
    for index in used:
        width = MIN_SECTOR + spare * area[index] / total
        out[index] = (start + width / 2, width / 2)
        start += width
    return out


def _fit(cluster: Cluster, sector: tuple[float, float], placed: list[Cluster]) -> None:
    """The spot nearest the centre, inside the family's sector while that has room, that touches nothing placed."""
    centre, half = sector
    radius = cluster.radius
    distance = CENTRE_CLEAR + radius
    while True:
        reach = STEP / (distance * ASPECT)
        limit = half if distance < 40 * radius else math.pi
        offsets = [0.0]
        for j in range(1, int(limit / reach) + 1):
            offsets += [j * reach, -j * reach]
        for offset in offsets:
            x, y = ASPECT * distance * math.cos(centre + offset), distance * math.sin(centre + offset)
            if all((x - other.x) ** 2 + (y - other.y) ** 2 >= (radius + other.radius) ** 2 for other in placed):
                cluster.x, cluster.y = x, y
                placed.append(cluster)
                return
        distance += STEP


def place(clusters: list[Cluster], families: int, late: list[Cluster] = ()) -> None:
    """Sets x and y on every cluster. `clusters` go largest first, each as close to the centre
    as fits. `late` clusters are placed after all of those, in the order given, and take no
    part in sizing the sectors: whatever is in them, nothing in `clusters` moves."""
    sectors = _sectors(clusters, families)
    placed: list[Cluster] = []
    for cluster in sorted(clusters, key=lambda item: (-len(item.members), item.family, item.key)):
        _fit(cluster, sectors[cluster.family], placed)
    for cluster in late:
        _fit(cluster, sectors[cluster.family] if sectors[cluster.family][1] else (-math.pi / 2, math.pi), placed)


def positions(clusters: list[Cluster], families: int, count: int, late: list[Cluster] = ()) -> list[tuple[float, float]]:
    """World coordinates for `count` nodes; a node in no cluster (the matter) stays at the origin."""
    place(clusters, families, late)
    out = [(0.0, 0.0)] * count
    for cluster in [*clusters, *late]:
        if cluster.hub < 0:
            continue
        out[cluster.hub] = (cluster.x, cluster.y)
        phase = math.atan2(cluster.y, cluster.x)
        for k, node in enumerate(cluster.members):
            dx, dy = member_offset(k, phase)
            out[node] = (cluster.x + dx, cluster.y + dy)
    return out
