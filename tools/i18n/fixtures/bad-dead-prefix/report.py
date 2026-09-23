_SEVERITY_HEADING_KEY = {
    "high": "report.severity_high",
}


def render(cat: dict[str, str], state: str) -> list[str]:
    return [
        cat["report.title"],
        cat[f"verdict.{state}"],
        cat.get(f"outcome.{state}", state),
        cat[f"next.{state}"],
    ]
