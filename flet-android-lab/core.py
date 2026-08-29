from datetime import date


def days_until(target_date: str | date, today: date) -> int:
    if isinstance(target_date, str):
        target_date = date.fromisoformat(target_date)
    return (target_date - today).days


def label(days: int) -> str:
    if days > 0:
        return f"还有 {days} 天"
    if days == 0:
        return "就是今天"
    return f"已 {-days} 天"


def sort_items(items: list[dict], today: date) -> list[dict]:
    def key(item: dict) -> tuple[bool, int]:
        d = days_until(item["date"], today)
        return (d < 0, abs(d) if d < 0 else d)

    return sorted(items, key=key)
