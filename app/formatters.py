from datetime import datetime
from zoneinfo import ZoneInfo

from app.models import Buyer, Submission

MSK = ZoneInfo("Europe/Moscow")


def _buyer_label(buyer: Buyer) -> str:
    name = buyer.username or buyer.full_name or "бюер"
    return f"{name} (id: {buyer.telegram_id})"


def _format_time(dt: datetime | None) -> str:
    if not dt:
        dt = datetime.now(MSK)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(MSK)
    else:
        dt = dt.astimezone(MSK)
    return dt.strftime("%d.%m.%Y %H:%M")


def format_submission_message(
    submission: Submission,
    buyer: Buyer,
    *,
    creative_url: str | None = None,
    html: bool = False,
) -> str:
    """Формат уведомления для ревьюера."""
    lines = [
        f"GFT: {submission.gft or '—'}",
        f"Тип: {submission.submission_type or '—'}",
        f"Домен: {submission.domain or '—'}",
        f"Проблема: {submission.problem or submission.description or '—'}",
    ]

    if creative_url:
        if html:
            lines.append(f'Креатив: <a href="{creative_url}">{creative_url}</a>')
        else:
            lines.append(f"Креатив: {creative_url}")

    lines.extend([
        "",
        f"От: {_buyer_label(buyer)}",
        f"Время: {_format_time(submission.created_at)}",
    ])

    return "\n".join(lines)
