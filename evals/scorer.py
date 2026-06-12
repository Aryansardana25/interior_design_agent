def score(result):

    score = 0

    if result.get("budget_ok"):
        score += 25

    if result.get("layout_ok"):
        score += 25

    if result.get("catalog_ok"):
        score += 25

    if result.get("guardrails_ok"):
        score += 25

    return score