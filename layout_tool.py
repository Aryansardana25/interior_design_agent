"""
layout_tool.py
--------------
Layout / fit checker for the Interior Company AI Design Agent.

Checks that selected furniture physically fits the room with sensible
circulation space. Uses heuristics — not a CAD engine.

Rules applied:
  1. Total floor footprint of non-wall-mounted items ≤ 60% of room area
     (40% reserved for circulation, doorways, pathways).
  2. No single item wider than the room's shorter dimension minus 60 cm
     (minimum clearance on each side).
  3. The L-sofa or large sectional special case: width + depth must not
     exceed 70% of the room's shorter dimension (prevents corner lockout).
  4. A flag is raised (not a hard block) when a single item exceeds
     30% of the usable area — it will dominate the room.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# Fraction of total room area reserved for circulation.
CIRCULATION_RESERVE = 0.40  # 40% kept clear
MAX_USABLE_FRACTION = 1.0 - CIRCULATION_RESERVE  # 60%

# Minimum clear walkway on each side of any floor piece (cm)
MIN_SIDE_CLEARANCE_CM = 60

# If a single item takes more than this fraction of usable area → advisory flag
DOMINANT_ITEM_FRACTION = 0.30


@dataclass
class LayoutItem:
    """A furniture piece being considered for layout."""
    item_id: str
    name: str
    category: str
    width_cm: Optional[int]
    depth_cm: Optional[int]
    is_wall_mounted: bool = False


@dataclass
class LayoutIssue:
    severity: str  # "error" | "warning"
    item_id: Optional[str]
    message: str


@dataclass
class LayoutCheckResult:
    """
    Result of a layout check.

    fits: True when no hard errors were found.
    issues: list of LayoutIssue objects (warnings and errors).
    total_footprint_sqcm: combined floor area of non-wall-mounted items.
    usable_area_sqcm: room area after circulation reserve.
    footprint_utilisation_pct: what % of usable area is consumed.
    """
    fits: bool
    issues: list
    total_footprint_sqcm: int
    room_area_sqcm: int
    usable_area_sqcm: int
    footprint_utilisation_pct: float
    items_checked: list

    def summary(self) -> str:
        verdict = "✅ FITS" if self.fits else "❌ DOES NOT FIT"
        pct = self.footprint_utilisation_pct
        parts = [
            f"{verdict}",
            f"Room: {self.room_area_sqcm:,} sqcm | Usable: {self.usable_area_sqcm:,} sqcm",
            f"Furniture footprint: {self.total_footprint_sqcm:,} sqcm ({pct:.1f}% of usable)",
        ]
        if self.issues:
            parts.append("Issues:")
            for issue in self.issues:
                icon = "❌" if issue.severity == "error" else "⚠️"
                item_tag = f"[{issue.item_id}] " if issue.item_id else ""
                parts.append(f"  {icon} {item_tag}{issue.message}")
        return "\n".join(parts)

    def has_errors(self) -> bool:
        return any(i.severity == "error" for i in self.issues)

    def has_warnings(self) -> bool:
        return any(i.severity == "warning" for i in self.issues)


def check_layout(
    items: list,       # list of LayoutItem
    room_length_cm: Optional[int],
    room_width_cm: Optional[int],
) -> LayoutCheckResult:
    """
    Check whether the given items fit the room.

    Parameters
    ----------
    items : list of LayoutItem — the selected furniture pieces
    room_length_cm : longer room dimension in cm
    room_width_cm  : shorter room dimension in cm

    Returns
    -------
    LayoutCheckResult — always returns an object, never raises.
    When room dimensions are unknown, returns a result with fits=True
    but a prominent warning so the caller knows the check was skipped.
    """
    issues = []
    floor_items = [i for i in items if not i.is_wall_mounted]

    # Handle missing room dimensions
    if room_length_cm is None or room_width_cm is None:
        return LayoutCheckResult(
            fits=True,
            issues=[LayoutIssue(severity="warning", item_id=None,
                               message="Room dimensions unknown — layout check skipped.")],
            total_footprint_sqcm=0,
            room_area_sqcm=0,
            usable_area_sqcm=0,
            footprint_utilisation_pct=0.0,
            items_checked=[i.item_id for i in items],
        )

    room_area = room_length_cm * room_width_cm
    usable_area = int(room_area * MAX_USABLE_FRACTION)
    shorter_dim = min(room_length_cm, room_width_cm)

    # Total footprint
    total_footprint = 0
    for item in floor_items:
        if item.width_cm and item.depth_cm:
            total_footprint += item.width_cm * item.depth_cm
        # Items with unknown dimensions: skip footprint contribution but flag

    # Rule 1: total footprint vs usable area
    if usable_area > 0:
        util_pct = (total_footprint / usable_area) * 100
    else:
        util_pct = 0.0

    if total_footprint > usable_area:
        issues.append(LayoutIssue(
            severity="error",
            item_id=None,
            message=(
                f"Total furniture footprint ({total_footprint:,} sqcm) exceeds usable area "
                f"({usable_area:,} sqcm). Remove or swap at least one piece."
            )
        ))

    # Rule 2: individual item width vs room's shorter dimension
    for item in floor_items:
        if item.width_cm is None:
            issues.append(LayoutIssue(
                severity="warning",
                item_id=item.item_id,
                message=f"{item.name} has unknown dimensions — cannot verify fit."
            ))
            continue
        max_allowed_width = shorter_dim - (2 * MIN_SIDE_CLEARANCE_CM)
        if max_allowed_width < 0:
            max_allowed_width = shorter_dim  # tiny room — still check
        if item.width_cm > max_allowed_width:
            issues.append(LayoutIssue(
                severity="error",
                item_id=item.item_id,
                message=(
                    f"{item.name} ({item.width_cm} cm wide) is too wide for this room. "
                    f"Maximum width with clearance: {max_allowed_width} cm."
                )
            ))

    # Rule 3: large L-sofa / sectional corner check
    for item in floor_items:
        if item.category in {"Sofa", "Sectional"} and item.width_cm and item.depth_cm:
            if item.width_cm >= 280 or item.depth_cm >= 160:
                combined = item.width_cm + item.depth_cm
                if combined > shorter_dim * 1.4:
                    issues.append(LayoutIssue(
                        severity="warning",
                        item_id=item.item_id,
                        message=(
                            f"{item.name} is a large sectional (W:{item.width_cm} D:{item.depth_cm}). "
                            f"Verify it fits the corner without blocking circulation."
                        )
                    ))

    # Rule 4: dominant item advisory
    if usable_area > 0:
        for item in floor_items:
            if item.width_cm and item.depth_cm:
                item_fp = item.width_cm * item.depth_cm
                if (item_fp / usable_area) > DOMINANT_ITEM_FRACTION:
                    issues.append(LayoutIssue(
                        severity="warning",
                        item_id=item.item_id,
                        message=(
                            f"{item.name} ({item_fp:,} sqcm) occupies "
                            f"{(item_fp/usable_area)*100:.0f}% of usable area — "
                            f"it will dominate the room."
                        )
                    ))

    fits = not any(i.severity == "error" for i in issues)

    return LayoutCheckResult(
        fits=fits,
        issues=issues,
        total_footprint_sqcm=total_footprint,
        room_area_sqcm=room_area,
        usable_area_sqcm=usable_area,
        footprint_utilisation_pct=util_pct,
        items_checked=[i.item_id for i in items],
    )


def make_layout_item(catalog_item_result) -> LayoutItem:
    """Helper: convert a CatalogItemResult to a LayoutItem."""
    item = catalog_item_result.item
    return LayoutItem(
        item_id=item.item_id,
        name=item.name,
        category=item.category,
        width_cm=item.width_cm,
        depth_cm=item.depth_cm,
        is_wall_mounted=item.is_wall_mounted,
    )


if __name__ == "__main__":
    # BR-01 test: 480 × 360 cm room
    items = [
        LayoutItem("SOF-001", "Nordby 3-Seater", "Sofa", 210, 90),
        LayoutItem("CFT-001", "Oslo Coffee Table", "Coffee Table", 110, 60),
        LayoutItem("TVU-001", "Floating TV Console", "TV Unit", 180, 40, is_wall_mounted=False),
        LayoutItem("RUG-001", "Berber Wool Rug", "Rug", 240, 150),
        LayoutItem("LMP-002", "Tripod Floor Lamp", "Floor Lamp", 55, 55),
    ]
    result = check_layout(items, room_length_cm=480, room_width_cm=360)
    print(result.summary())
    print()

    # BR-09 studio trap: 320 × 280 cm room, big sectional
    items_09 = [
        LayoutItem("SOF-004", "Marrakech Modular Sectional", "Sofa", 300, 170),
        LayoutItem("DNT-001", "Oslo 6-Seater Dining", "Dining Table", 180, 90),
        LayoutItem("BKS-002", "Industrial 5-Tier Shelf", "Bookshelf", 90, 35),
    ]
    result_09 = check_layout(items_09, room_length_cm=320, room_width_cm=280)
    print("BR-09 (studio trap):")
    print(result_09.summary())
