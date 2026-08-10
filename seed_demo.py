"""Демо-данные для проверки панели и формата уведомлений."""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from app.database import async_session, init_db
from app.formatters import format_submission_message
from app.models import Buyer, Submission, SubmissionStatus

MSK = ZoneInfo("Europe/Moscow")


async def seed() -> None:
    await init_db()

    async with async_session() as session:
        from sqlalchemy import select

        existing = await session.execute(
            select(Buyer).where(Buyer.telegram_id == 7523423935)
        )
        buyer = existing.scalar_one_or_none()
        if not buyer:
            buyer = Buyer(
                telegram_id=7523423935,
                username="eduard",
                full_name="Eduard",
            )
            session.add(buyer)
            await session.flush()

        submission = Submission(
            buyer_id=buyer.id,
            gft="1833",
            submission_type="White",
            domain="consultanta-audit.info",
            problem="✍️ не работают кнопки куки и так же внизу нету картики",
            title="GFT 1833 — consultanta-audit.info",
            description="✍️ не работают кнопки куки и так же внизу нету картики",
            status=SubmissionStatus.NEW,
            created_at=datetime(2026, 8, 5, 0, 18, tzinfo=MSK),
            google_doc_url="https://docs.google.com/document/d/demo-creative-link/edit",
        )
        session.add(submission)
        await session.commit()
        await session.refresh(submission)

        await session.refresh(submission)

        print(
            format_submission_message(
                submission,
                buyer,
                creative_url=submission.google_doc_url,
                html=False,
            )
        )

        from app.file_store import save_submission_files

        txt_path, json_path = save_submission_files(
            submission, buyer, creative_url=submission.google_doc_url
        )
        print(f"\n📁 Сохранено: {txt_path}\n📁 {json_path}")
        print(f"\n✅ Демо-заявка #{submission.id} создана. Открой http://localhost:8000")


if __name__ == "__main__":
    asyncio.run(seed())
