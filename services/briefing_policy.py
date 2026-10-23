import re

REQUIRED_SECTIONS = (
    "Overview", "Build & run", "Ports & networking", "Environment variables", "Risks & caveats",
)


def is_complete_briefing(briefing: str) -> bool:
    return all(
        re.search(r"^## " + re.escape(section) + r"\s*$", briefing, re.MULTILINE | re.IGNORECASE)
        for section in REQUIRED_SECTIONS
    )
