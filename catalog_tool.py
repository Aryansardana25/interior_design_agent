"""
catalog_tool.py
---------------
Catalog search tool for the Interior Company AI Design Agent.
This is the ONLY permitted interface through which the agent accesses
product data. The agent must never recommend an item it has not received
as a result from one of the functions in this module.

Four-tier search cascade (stops at first tier with results):
  Tier 1 — exact style match, in-stock, priced
  Tier 2 — adjacent style match, in-stock, priced
  Tier 3 — any style, in-stock, priced
  Tier 4 — exact style match, OUT-OF-STOCK (flagged)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from database import (
    CatalogItem,
    get_products_by_room,
    get_products_by_category,
    get_products_by_style,
    get_product_by_id,
    get_cheapest_product,
    get_distinct_categories,
    get_distinct_styles,
)

_DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "interior_company_catalog.db")

LONG_LEAD_TIME_DAYS = 45
LARGE_FOOTPRINT_RATIO = 0.80

# Style adjacency map — single source of truth
STYLE_ADJACENCY: dict = {
    "Scandinavian": ["Minimalist", "Contemporary"],
    "Minimalist": ["Scandinavian", "Japandi", "Contemporary"],
    "Japandi": ["Minimalist", "Scandinavian"],
    "Mid-Century": ["Contemporary", "Industrial"],
    "Industrial": ["Mid-Century", "Contemporary"],
    "Bohemian": ["Coastal", "Traditional"],
    "Coastal": ["Bohemian", "Minimalist"],
    "Traditional": ["Bohemian", "Contemporary"],
    "Contemporary": ["Minimalist", "Mid-Century"],
}


class SearchTier(str, Enum):
    EXACT_STYLE = "exact_style"
    ADJACENT_STYLE = "adjacent_style"
    ANY_STYLE = "any_style"
    OOS_FALLBACK = "oos_fallback"
    NO_RESULTS = "no_results"


class StyleMatchType(str, Enum):
    EXACT = "exact"
    ADJACENT = "adjacent"
    NONE = "none"


@dataclass
class ItemWarnings:
    is_oos: bool = False
    has_null_price: bool = False
    has_long_lead: bool = False
    has_large_footprint: bool = False

    @property
    def has_any(self) -> bool:
        return any([self.is_oos, self.has_null_price, self.has_long_lead, self.has_large_footprint])

    def as_list(self) -> list:
        labels = []
        if self.is_oos:
            labels.append("OUT_OF_STOCK")
        if self.has_null_price:
            labels.append("NULL_PRICE")
        if self.has_long_lead:
            labels.append(f"LONG_LEAD_TIME (>{LONG_LEAD_TIME_DAYS}d)")
        if self.has_large_footprint:
            labels.append("LARGE_FOOTPRINT")
        return labels


@dataclass
class CatalogItemResult:
    item: CatalogItem
    style_match: StyleMatchType
    adjacent_style_used: Optional[str]
    warnings: ItemWarnings

    @property
    def item_id(self) -> str:
        return self.item.item_id

    @property
    def name(self) -> str:
        return self.item.name

    @property
    def category(self) -> str:
        return self.item.category

    @property
    def price_inr(self) -> Optional[int]:
        return self.item.price_inr

    @property
    def in_stock(self) -> bool:
        return self.item.in_stock

    @property
    def footprint_sqcm(self) -> int:
        return self.item.footprint_sqcm

    @property
    def lead_time_days(self) -> Optional[int]:
        return self.item.lead_time_days

    def summary(self) -> str:
        price_str = f"₹{self.price_inr:,}" if self.price_inr is not None else "PRICE N/A"
        stock_str = "in-stock" if self.in_stock else "OOS"
        match_str = self.style_match.value
        if self.adjacent_style_used:
            match_str += f"→{self.adjacent_style_used}"
        warn_str = " [" + ", ".join(self.warnings.as_list()) + "]" if self.warnings.has_any else ""
        return f"{self.item_id} | {self.name} | {price_str} | {match_str} | {stock_str}{warn_str}"


@dataclass
class StyleGapReport:
    requested_style: str
    category: str
    room_type: str
    exact_match_found: bool = False
    adjacent_match_found: bool = False
    adjacent_style_used: Optional[str] = None
    any_match_found: bool = False
    oos_match_found: bool = False
    full_gap: bool = False

    def describe(self) -> str:
        if self.exact_match_found:
            return f"Exact {self.requested_style} {self.category} found."
        if self.adjacent_match_found:
            return (
                f"No {self.requested_style} {self.category} in catalog. "
                f"Closest match uses adjacent style '{self.adjacent_style_used}'."
            )
        if self.any_match_found:
            return (
                f"No {self.requested_style} or adjacent-style {self.category} found. "
                f"Best available in-stock {self.category} used regardless of style."
            )
        if self.oos_match_found:
            return (
                f"No in-stock {self.category} available for {self.room_type}. "
                f"An out-of-stock {self.requested_style} option exists but has lead time."
            )
        return (
            f"CATALOG GAP: No {self.category} found for {self.room_type} "
            f"at any style tier, in-stock or otherwise."
        )


@dataclass
class CatalogSearchResult:
    results: list
    query_params: dict
    search_tier: SearchTier
    style_gap_report: Optional[StyleGapReport]
    total_found: int

    @property
    def has_results(self) -> bool:
        return len(self.results) > 0

    @property
    def best(self) -> Optional[CatalogItemResult]:
        return self.results[0] if self.results else None

    @property
    def has_style_gap(self) -> bool:
        if self.style_gap_report is None:
            return False
        return not self.style_gap_report.exact_match_found

    def summaries(self) -> list:
        return [r.summary() for r in self.results]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_warnings(item: CatalogItem, usable_area_sqcm: Optional[int] = None) -> ItemWarnings:
    large_fp = False
    if usable_area_sqcm and usable_area_sqcm > 0 and not item.is_wall_mounted:
        if item.footprint_sqcm > 0:
            large_fp = (item.footprint_sqcm / usable_area_sqcm) >= LARGE_FOOTPRINT_RATIO
    return ItemWarnings(
        is_oos=not item.in_stock,
        has_null_price=not item.has_price,
        has_long_lead=(item.lead_time_days is not None and item.lead_time_days > LONG_LEAD_TIME_DAYS),
        has_large_footprint=large_fp,
    )


def _wrap_items(items, style_match: StyleMatchType, adjacent_style_used=None, usable_area_sqcm=None) -> list:
    return [
        CatalogItemResult(
            item=item,
            style_match=style_match,
            adjacent_style_used=adjacent_style_used,
            warnings=_build_warnings(item, usable_area_sqcm),
        )
        for item in items
    ]


def _get_adjacent_styles(requested_style: str) -> list:
    return STYLE_ADJACENCY.get(requested_style, [])


def _is_usable(item: CatalogItem) -> bool:
    return item.in_stock and item.has_price


def _filter_usable(items) -> list:
    return [i for i in items if _is_usable(i)]


def _apply_price_ceiling(items, max_price: Optional[int]) -> list:
    if max_price is None:
        return items
    return [item for item in items if item.price_inr is not None and item.price_inr <= max_price]


def _deduplicate(items) -> list:
    seen = set()
    unique = []
    for item in items:
        if item.item_id not in seen:
            seen.add(item.item_id)
            unique.append(item)
    return unique


def _sort_by_price(items) -> list:
    return sorted(items, key=lambda i: (i.price_inr is None, i.price_inr or 0))


# ---------------------------------------------------------------------------
# Four-tier cascade
# ---------------------------------------------------------------------------

def _tier1_exact_style(category, room_type, style, max_price, max_width_cm, max_depth_cm, db_path) -> list:
    items = get_products_by_room(
        room_type=room_type, category=category, style_tag=style,
        in_stock_only=True, max_price=max_price, max_width_cm=max_width_cm,
        max_depth_cm=max_depth_cm, db_path=db_path,
    )
    return _filter_usable(items)


def _tier2_adjacent_style(category, room_type, requested_style, max_price, max_width_cm, max_depth_cm, db_path):
    for adj_style in _get_adjacent_styles(requested_style):
        items = get_products_by_room(
            room_type=room_type, category=category, style_tag=adj_style,
            in_stock_only=True, max_price=max_price, max_width_cm=max_width_cm,
            max_depth_cm=max_depth_cm, db_path=db_path,
        )
        usable = _filter_usable(items)
        if usable:
            return usable, adj_style
    return [], None


def _tier3_any_style(category, room_type, max_price, max_width_cm, max_depth_cm, db_path) -> list:
    items = get_products_by_room(
        room_type=room_type, category=category,
        in_stock_only=True, max_price=max_price, max_width_cm=max_width_cm,
        max_depth_cm=max_depth_cm, db_path=db_path,
    )
    return _filter_usable(items)


def _tier4_oos_fallback(category, room_type, style, max_price, max_width_cm, max_depth_cm, db_path) -> list:
    items = []
    if style:
        items = get_products_by_room(
            room_type=room_type, category=category, style_tag=style,
            in_stock_only=False, max_price=max_price, max_width_cm=max_width_cm,
            max_depth_cm=max_depth_cm, db_path=db_path,
        )
        items = [i for i in items if not i.in_stock and i.has_price]
    if not items:
        items = get_products_by_room(
            room_type=room_type, category=category, in_stock_only=False,
            max_price=max_price, max_width_cm=max_width_cm,
            max_depth_cm=max_depth_cm, db_path=db_path,
        )
        items = [i for i in items if not i.in_stock and i.has_price]
    return items


def _run_cascade(category, room_type, style, max_price, max_width_cm, max_depth_cm, limit, usable_area_sqcm, db_path) -> CatalogSearchResult:
    query_params = {
        "category": category, "room_type": room_type, "style": style,
        "max_price": max_price, "max_width_cm": max_width_cm,
        "max_depth_cm": max_depth_cm, "limit": limit,
    }
    gap = StyleGapReport(requested_style=style or "", category=category, room_type=room_type) if style else None

    # Tier 1
    if style:
        t1_items = _tier1_exact_style(category, room_type, style, max_price, max_width_cm, max_depth_cm, db_path)
        if t1_items:
            t1_items = _sort_by_price(_deduplicate(t1_items))[:limit]
            if gap:
                gap.exact_match_found = True
            return CatalogSearchResult(
                results=_wrap_items(t1_items, StyleMatchType.EXACT, usable_area_sqcm=usable_area_sqcm),
                query_params={**query_params, "tier_used": "exact_style"},
                search_tier=SearchTier.EXACT_STYLE,
                style_gap_report=gap,
                total_found=len(t1_items),
            )

    # Tier 2
    if style:
        t2_items, adj_style_used = _tier2_adjacent_style(category, room_type, style, max_price, max_width_cm, max_depth_cm, db_path)
        if t2_items:
            t2_items = _sort_by_price(_deduplicate(t2_items))[:limit]
            if gap:
                gap.adjacent_match_found = True
                gap.adjacent_style_used = adj_style_used
            return CatalogSearchResult(
                results=_wrap_items(t2_items, StyleMatchType.ADJACENT, adjacent_style_used=adj_style_used, usable_area_sqcm=usable_area_sqcm),
                query_params={**query_params, "tier_used": "adjacent_style", "adjacent_style_used": adj_style_used},
                search_tier=SearchTier.ADJACENT_STYLE,
                style_gap_report=gap,
                total_found=len(t2_items),
            )

    # Tier 3
    t3_items = _tier3_any_style(category, room_type, max_price, max_width_cm, max_depth_cm, db_path)
    if t3_items:
        t3_items = _sort_by_price(_deduplicate(t3_items))[:limit]
        if gap:
            gap.any_match_found = True
        return CatalogSearchResult(
            results=_wrap_items(t3_items, StyleMatchType.NONE, usable_area_sqcm=usable_area_sqcm),
            query_params={**query_params, "tier_used": "any_style"},
            search_tier=SearchTier.ANY_STYLE,
            style_gap_report=gap,
            total_found=len(t3_items),
        )

    # Tier 4
    t4_items = _tier4_oos_fallback(category, room_type, style, max_price, max_width_cm, max_depth_cm, db_path)
    if t4_items:
        t4_items = _sort_by_price(_deduplicate(t4_items))[:limit]
        if gap:
            gap.oos_match_found = True
        return CatalogSearchResult(
            results=_wrap_items(t4_items, StyleMatchType.EXACT if style else StyleMatchType.NONE, usable_area_sqcm=usable_area_sqcm),
            query_params={**query_params, "tier_used": "oos_fallback"},
            search_tier=SearchTier.OOS_FALLBACK,
            style_gap_report=gap,
            total_found=len(t4_items),
        )

    if gap:
        gap.full_gap = True
    return CatalogSearchResult(
        results=[],
        query_params={**query_params, "tier_used": "no_results"},
        search_tier=SearchTier.NO_RESULTS,
        style_gap_report=gap,
        total_found=0,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def search_catalog(
    category: str,
    room_type: str,
    style: Optional[str] = None,
    max_price: Optional[int] = None,
    max_width_cm: Optional[int] = None,
    max_depth_cm: Optional[int] = None,
    limit: int = 5,
    usable_area_sqcm: Optional[int] = None,
    db_path: str = _DEFAULT_DB_PATH,
) -> CatalogSearchResult:
    """Primary catalog search with four-tier style cascade."""
    category = category.strip()
    room_type = room_type.strip()
    style = style.strip() if style else None
    return _run_cascade(category, room_type, style, max_price, max_width_cm, max_depth_cm, limit, usable_area_sqcm, db_path)


def search_by_style(
    style: str,
    room_type: Optional[str] = None,
    category: Optional[str] = None,
    max_price: Optional[int] = None,
    limit: int = 10,
    db_path: str = _DEFAULT_DB_PATH,
) -> CatalogSearchResult:
    """Return all in-stock, priced items matching a given style tag."""
    style = style.strip()
    params = {"style": style, "room_type": room_type, "category": category, "max_price": max_price, "limit": limit}
    raw = get_products_by_style(style_tag=style, category=category, in_stock_only=True, db_path=db_path)
    items = _filter_usable(raw)
    if room_type:
        items = [i for i in items if i.suits_room(room_type)]
    if max_price is not None:
        items = _apply_price_ceiling(items, max_price)
    gap = StyleGapReport(requested_style=style, category=category or "any", room_type=room_type or "any")
    if items:
        items = _sort_by_price(_deduplicate(items))[:limit]
        gap.exact_match_found = True
        return CatalogSearchResult(
            results=_wrap_items(items, StyleMatchType.EXACT),
            query_params={**params, "tier_used": "exact_style"},
            search_tier=SearchTier.EXACT_STYLE,
            style_gap_report=gap,
            total_found=len(items),
        )
    for adj_style in _get_adjacent_styles(style):
        adj_raw = get_products_by_style(style_tag=adj_style, category=category, in_stock_only=True, db_path=db_path)
        adj_items = _filter_usable(adj_raw)
        if room_type:
            adj_items = [i for i in adj_items if i.suits_room(room_type)]
        if max_price is not None:
            adj_items = _apply_price_ceiling(adj_items, max_price)
        if adj_items:
            adj_items = _sort_by_price(_deduplicate(adj_items))[:limit]
            gap.adjacent_match_found = True
            gap.adjacent_style_used = adj_style
            return CatalogSearchResult(
                results=_wrap_items(adj_items, StyleMatchType.ADJACENT, adj_style),
                query_params={**params, "tier_used": "adjacent_style", "adjacent_style": adj_style},
                search_tier=SearchTier.ADJACENT_STYLE,
                style_gap_report=gap,
                total_found=len(adj_items),
            )
    gap.full_gap = True
    return CatalogSearchResult(results=[], query_params={**params, "tier_used": "no_results"}, search_tier=SearchTier.NO_RESULTS, style_gap_report=gap, total_found=0)


def search_by_room(
    room_type: str,
    style: Optional[str] = None,
    max_price: Optional[int] = None,
    max_width_cm: Optional[int] = None,
    max_depth_cm: Optional[int] = None,
    limit: int = 20,
    db_path: str = _DEFAULT_DB_PATH,
) -> CatalogSearchResult:
    """Return all in-stock, priced items for a given room type."""
    room_type = room_type.strip()
    style = style.strip() if style else None
    params = {"room_type": room_type, "style": style, "max_price": max_price, "limit": limit}
    items = get_products_by_room(room_type=room_type, style_tag=style, in_stock_only=True, max_price=max_price, max_width_cm=max_width_cm, max_depth_cm=max_depth_cm, db_path=db_path)
    items = _filter_usable(items)
    if not items:
        return CatalogSearchResult(results=[], query_params={**params, "tier_used": "no_results"}, search_tier=SearchTier.NO_RESULTS, style_gap_report=None, total_found=0)
    items = _sort_by_price(_deduplicate(items))[:limit]
    match_type = StyleMatchType.EXACT if style else StyleMatchType.NONE
    tier = SearchTier.EXACT_STYLE if style else SearchTier.ANY_STYLE
    return CatalogSearchResult(results=_wrap_items(items, match_type), query_params={**params, "tier_used": tier.value}, search_tier=tier, style_gap_report=None, total_found=len(items))


def search_by_budget(
    room_type: str,
    max_price: int,
    style: Optional[str] = None,
    category: Optional[str] = None,
    max_width_cm: Optional[int] = None,
    max_depth_cm: Optional[int] = None,
    limit: int = 10,
    db_path: str = _DEFAULT_DB_PATH,
) -> CatalogSearchResult:
    """Return in-stock, priced items within a specific price ceiling."""
    room_type = room_type.strip()
    style = style.strip() if style else None
    params = {"room_type": room_type, "max_price": max_price, "style": style, "category": category, "limit": limit}
    if category:
        return _run_cascade(category, room_type, style, max_price, max_width_cm, max_depth_cm, limit, None, db_path)
    items = get_products_by_room(room_type=room_type, style_tag=style, in_stock_only=True, max_price=max_price, max_width_cm=max_width_cm, max_depth_cm=max_depth_cm, db_path=db_path)
    items = _filter_usable(items)
    if not items:
        return CatalogSearchResult(results=[], query_params={**params, "tier_used": "no_results"}, search_tier=SearchTier.NO_RESULTS, style_gap_report=None, total_found=0)
    items = _sort_by_price(_deduplicate(items))[:limit]
    match_type = StyleMatchType.EXACT if style else StyleMatchType.NONE
    tier = SearchTier.EXACT_STYLE if style else SearchTier.ANY_STYLE
    return CatalogSearchResult(results=_wrap_items(items, match_type), query_params={**params, "tier_used": tier.value}, search_tier=tier, style_gap_report=None, total_found=len(items))


def get_cheapest_per_category(categories: list, room_type: str, db_path: str = _DEFAULT_DB_PATH) -> dict:
    """Return cheapest in-stock, priced item for each category. Used for feasibility pre-check."""
    result = {}
    for cat in categories:
        item = get_cheapest_product(cat, room_type, in_stock_only=True, db_path=db_path)
        if item:
            result[cat] = CatalogItemResult(
                item=item,
                style_match=StyleMatchType.NONE,
                adjacent_style_used=None,
                warnings=_build_warnings(item),
            )
        else:
            result[cat] = None
    return result


def get_item_by_id(item_id: str, db_path: str = _DEFAULT_DB_PATH) -> Optional[CatalogItemResult]:
    """Fetch and wrap a single catalog item by item_id."""
    item = get_product_by_id(item_id, db_path=db_path)
    if item is None:
        return None
    return CatalogItemResult(item=item, style_match=StyleMatchType.NONE, adjacent_style_used=None, warnings=_build_warnings(item))
