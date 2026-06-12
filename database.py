"""
database.py
-----------
Database layer for the Interior Company AI Design Agent.
All SQLite access is routed through this module.
No other component queries the database directly.

Tables
------
catalog      : 72 furniture and decor products
room_briefs  : 14 sample customer room briefs
"""
import sqlite3
import os
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
_DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "interior_company_catalog.db")

# ---------------------------------------------------------------------------
# Typed data objects
# ---------------------------------------------------------------------------

@dataclass
class CatalogItem:
    item_id: str
    category: str
    name: str
    style_tags: list = field(default_factory=list)
    price_inr: Optional[int] = None
    width_cm: Optional[int] = None
    depth_cm: Optional[int] = None
    height_cm: Optional[int] = None
    color_finish: Optional[str] = None
    in_stock: bool = False
    lead_time_days: Optional[int] = None
    room_types: list = field(default_factory=list)

    @property
    def has_price(self) -> bool:
        return self.price_inr is not None

    @property
    def footprint_sqcm(self) -> int:
        if self.width_cm is not None and self.depth_cm is not None:
            return self.width_cm * self.depth_cm
        return 0

    @property
    def is_wall_mounted(self) -> bool:
        return self.category in {"Curtains", "Wall Art", "Mirror", "Pendant Light"}

    def has_style(self, style: str) -> bool:
        return style.lower() in [s.lower() for s in self.style_tags]

    def suits_room(self, room_type: str) -> bool:
        return room_type.lower() in [r.lower() for r in self.room_types]


@dataclass
class RoomBrief:
    brief_id: str
    room_type: str
    length_cm: Optional[int] = None
    width_cm: Optional[int] = None
    ceiling_cm: Optional[int] = None
    budget_inr: Optional[int] = None
    style_preference: Optional[str] = None
    must_haves: list = field(default_factory=list)
    constraints: Optional[str] = None
    customer_note: Optional[str] = None

    @property
    def area_sqcm(self) -> int:
        if self.length_cm is not None and self.width_cm is not None:
            return self.length_cm * self.width_cm
        return 0

    @property
    def usable_area_sqcm(self) -> int:
        return int(self.area_sqcm * 0.60)

    @property
    def has_budget(self) -> bool:
        return self.budget_inr is not None and self.budget_inr > 0


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def get_connection(db_path: str = _DEFAULT_DB_PATH) -> sqlite3.Connection:
    if not os.path.exists(db_path):
        raise FileNotFoundError(
            f"Database not found at '{db_path}'. "
            "Pass the correct path to db_path or set the file next to database.py."
        )
    conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def execute_query(conn, sql: str, params: tuple = ()):
    try:
        return conn.execute(sql, params)
    except sqlite3.Error as exc:
        raise RuntimeError(
            f"Database query failed.\nSQL : {sql}\nParams: {params}\nError: {exc}"
        ) from exc


def fetch_all(conn, sql: str, params: tuple = ()):
    cursor = execute_query(conn, sql, params)
    return cursor.fetchall() or []


def fetch_one(conn, sql: str, params: tuple = ()):
    cursor = execute_query(conn, sql, params)
    return cursor.fetchone()


# ---------------------------------------------------------------------------
# Internal row → dataclass converters
# ---------------------------------------------------------------------------

def _row_to_catalog_item(row) -> CatalogItem:
    raw_styles = row["style_tags"] or ""
    style_list = [s.strip() for s in raw_styles.split(",") if s.strip()]
    raw_rooms = row["room_types"] or ""
    room_list = [r.strip() for r in raw_rooms.split(",") if r.strip()]
    return CatalogItem(
        item_id=row["item_id"],
        category=row["category"],
        name=row["name"],
        style_tags=style_list,
        price_inr=row["price_inr"],
        width_cm=row["width_cm"],
        depth_cm=row["depth_cm"],
        height_cm=row["height_cm"],
        color_finish=row["color_finish"],
        in_stock=bool(row["in_stock"]),
        lead_time_days=row["lead_time_days"],
        room_types=room_list,
    )


def _row_to_room_brief(row) -> RoomBrief:
    raw_must_haves = row["must_haves"] or ""
    must_have_list = [m.strip() for m in raw_must_haves.split(",") if m.strip()]
    return RoomBrief(
        brief_id=row["brief_id"],
        room_type=row["room_type"],
        length_cm=row["length_cm"],
        width_cm=row["width_cm"],
        ceiling_cm=row["ceiling_cm"],
        budget_inr=row["budget_inr"],
        style_preference=row["style_preference"],
        must_haves=must_have_list,
        constraints=row["constraints"],
        customer_note=row["customer_note"],
    )


# ---------------------------------------------------------------------------
# Catalog queries
# ---------------------------------------------------------------------------

def get_all_products(db_path: str = _DEFAULT_DB_PATH) -> list:
    sql = "SELECT * FROM catalog ORDER BY item_id"
    with get_connection(db_path) as conn:
        rows = fetch_all(conn, sql)
    return [_row_to_catalog_item(r) for r in rows]


def get_product_by_id(item_id: str, db_path: str = _DEFAULT_DB_PATH) -> Optional[CatalogItem]:
    sql = "SELECT * FROM catalog WHERE item_id = ?"
    with get_connection(db_path) as conn:
        row = fetch_one(conn, sql, (item_id,))
    return _row_to_catalog_item(row) if row else None


def get_products_by_category(category: str, in_stock_only: bool = False, db_path: str = _DEFAULT_DB_PATH) -> list:
    if in_stock_only:
        sql = "SELECT * FROM catalog WHERE category = ? AND in_stock = 1 ORDER BY price_inr ASC NULLS LAST"
    else:
        sql = "SELECT * FROM catalog WHERE category = ? ORDER BY price_inr ASC NULLS LAST"
    with get_connection(db_path) as conn:
        rows = fetch_all(conn, sql, (category,))
    return [_row_to_catalog_item(r) for r in rows]


def get_products_by_style(style_tag: str, category: Optional[str] = None, in_stock_only: bool = False, db_path: str = _DEFAULT_DB_PATH) -> list:
    conditions = ["style_tags LIKE ?"]
    params = [f"%{style_tag}%"]
    if category:
        conditions.append("category = ?")
        params.append(category)
    if in_stock_only:
        conditions.append("in_stock = 1")
    where_clause = " AND ".join(conditions)
    sql = f"SELECT * FROM catalog WHERE {where_clause} ORDER BY price_inr ASC NULLS LAST"
    with get_connection(db_path) as conn:
        rows = fetch_all(conn, sql, tuple(params))
    return [_row_to_catalog_item(r) for r in rows]


def get_products_by_room(
    room_type: str,
    category: Optional[str] = None,
    style_tag: Optional[str] = None,
    in_stock_only: bool = False,
    max_price: Optional[int] = None,
    max_width_cm: Optional[int] = None,
    max_depth_cm: Optional[int] = None,
    db_path: str = _DEFAULT_DB_PATH,
) -> list:
    conditions = ["room_types LIKE ?"]
    params = [f"%{room_type}%"]
    if category:
        conditions.append("category = ?")
        params.append(category)
    if style_tag:
        conditions.append("style_tags LIKE ?")
        params.append(f"%{style_tag}%")
    if in_stock_only:
        conditions.append("in_stock = 1")
    if max_price is not None:
        conditions.append("price_inr IS NOT NULL")
        conditions.append("price_inr <= ?")
        params.append(max_price)
    if max_width_cm is not None:
        conditions.append("(width_cm IS NULL OR width_cm <= ?)")
        params.append(max_width_cm)
    if max_depth_cm is not None:
        conditions.append("(depth_cm IS NULL OR depth_cm <= ?)")
        params.append(max_depth_cm)
    where_clause = " AND ".join(conditions)
    sql = f"SELECT * FROM catalog WHERE {where_clause} ORDER BY price_inr ASC NULLS LAST"
    with get_connection(db_path) as conn:
        rows = fetch_all(conn, sql, tuple(params))
    return [_row_to_catalog_item(r) for r in rows]


def get_cheapest_product(category: str, room_type: str, in_stock_only: bool = True, db_path: str = _DEFAULT_DB_PATH) -> Optional[CatalogItem]:
    conditions = ["category = ?", "room_types LIKE ?", "price_inr IS NOT NULL"]
    params = [category, f"%{room_type}%"]
    if in_stock_only:
        conditions.append("in_stock = 1")
    where_clause = " AND ".join(conditions)
    sql = f"SELECT * FROM catalog WHERE {where_clause} ORDER BY price_inr ASC LIMIT 1"
    with get_connection(db_path) as conn:
        row = fetch_one(conn, sql, tuple(params))
    return _row_to_catalog_item(row) if row else None


def get_distinct_categories(db_path: str = _DEFAULT_DB_PATH) -> list:
    sql = "SELECT DISTINCT category FROM catalog ORDER BY category"
    with get_connection(db_path) as conn:
        rows = fetch_all(conn, sql)
    return [row["category"] for row in rows]


def get_distinct_styles(db_path: str = _DEFAULT_DB_PATH) -> list:
    sql = "SELECT style_tags FROM catalog WHERE style_tags IS NOT NULL AND style_tags != ''"
    with get_connection(db_path) as conn:
        rows = fetch_all(conn, sql)
    styles = set()
    for row in rows:
        for tag in row["style_tags"].split(","):
            tag = tag.strip()
            if tag:
                styles.add(tag)
    return sorted(styles)


# ---------------------------------------------------------------------------
# Room brief queries
# ---------------------------------------------------------------------------

def get_room_brief(brief_id: str, db_path: str = _DEFAULT_DB_PATH) -> Optional[RoomBrief]:
    sql = "SELECT * FROM room_briefs WHERE brief_id = ?"
    with get_connection(db_path) as conn:
        row = fetch_one(conn, sql, (brief_id,))
    return _row_to_room_brief(row) if row else None


def get_all_room_briefs(db_path: str = _DEFAULT_DB_PATH) -> list:
    sql = "SELECT * FROM room_briefs ORDER BY brief_id"
    with get_connection(db_path) as conn:
        rows = fetch_all(conn, sql)
    return [_row_to_room_brief(r) for r in rows]


if __name__ == "__main__":
    db = _DEFAULT_DB_PATH
    all_products = get_all_products(db)
    print(f"Total products: {len(all_products)}")
    briefs = get_all_room_briefs(db)
    print(f"Total briefs: {len(briefs)}")
    null_price = [p for p in all_products if not p.has_price]
    print(f"NULL price items: {[p.item_id for p in null_price]}")
    oos = [p for p in all_products if not p.in_stock]
    print(f"OOS items: {[p.item_id for p in oos]}")
    print("database.py OK")
