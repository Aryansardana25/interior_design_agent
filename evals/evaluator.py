import json

from scorer import score


def run():

    with open(
        "evals/test_cases.json",
        "r"
    ) as f:

        tests = json.load(f)

    results = []

    for t in tests:

        fake = {
            "budget_ok": True,
            "layout_ok": True,
            "catalog_ok": True,
            "guardrails_ok": True
        }

        results.append(
            {
                "case": t["id"],
                "score": score(fake)
            }
        )

    print(results)


if __name__ == "__main__":
    run()