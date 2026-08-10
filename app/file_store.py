import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import DATA_DIR
from app.formatters import format_submission_message
from app.models import Buyer, Submission

SUBMISSIONS_DIR = DATA_DIR / "submissions"
MSK = ZoneInfo("Europe/Moscow")


def _safe_name(value: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", value).strip("_")[:80]


def save_submission_files(
    submission: Submission,
    buyer: Buyer,
    *,
    creative_url: str | None = None,
) -> tuple[Path, Path]:
    """Сохраняет заявку в файлы: .txt (уведомление) и .json (метаданные)."""
    SUBMISSIONS_DIR.mkdir(parents=True, exist_ok=True)

    gft = submission.gft or "unknown"
    domain = submission.domain or submission.title
    base = f"{submission.id:04d}_GFT{gft}_{_safe_name(domain)}"

    txt_path = SUBMISSIONS_DIR / f"{base}.txt"
    json_path = SUBMISSIONS_DIR / f"{base}.json"

    text_body = format_submission_message(
        submission,
        buyer,
        creative_url=creative_url,
        html=False,
    )
    txt_path.write_text(text_body, encoding="utf-8")

    meta = {
        "id": submission.id,
        "gft": submission.gft,
        "submission_type": submission.submission_type,
        "domain": submission.domain,
        "problem": submission.problem or submission.description,
        "title": submission.title,
        "file_name": submission.file_name,
        "file_path": submission.file_path,
        "google_doc_url": submission.google_doc_url,
        "status": submission.status.value if submission.status else None,
        "created_at": submission.created_at.isoformat() if submission.created_at else None,
        "buyer": {
            "telegram_id": buyer.telegram_id,
            "username": buyer.username,
            "full_name": buyer.full_name,
        },
        "creative_url": creative_url,
        "saved_at": datetime.now(MSK).isoformat(),
    }
    json_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    return txt_path, json_path
