FORBIDDEN_TOPICS = [
    "remove wall",
    "structural",
    "load bearing",
    "plumbing",
    "electrical",
    "rewiring"
]


def validate_request(text):
    if not text:
        return True, None

    content = text.lower()

    for item in FORBIDDEN_TOPICS:
        if item in content:
            return (
                False,
                f"Unsupported request detected: {item}"
            )

    return True, None


def validate_budget(total, budget):
    if total > budget:
        return (
            False,
            "Budget exceeded"
        )

    return True, None


def validate_products(products):

    valid = []

    for p in products:

        stock = p.get("stock", 1)
        price = p.get("price")

        if stock <= 0:
            continue

        if price is None:
            continue

        valid.append(p)

    return valid