import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import ADMIN_TELEGRAM_ID, TELEGRAM_BOT_TOKEN, UPLOAD_DIR
from app.database import async_session
from app.file_store import save_submission_files
from app.formatters import format_submission_message
from app.google_service import google_service
from app.models import Buyer, Message, MessageSender, Submission, SubmissionStatus

logger = logging.getLogger(__name__)

router = Router()
MSK = ZoneInfo("Europe/Moscow")


class UploadStates(StatesGroup):
    waiting_gft = State()
    waiting_type = State()
    waiting_domain = State()
    waiting_problem = State()
    waiting_file = State()


class ChatStates(StatesGroup):
    waiting_message = State()


def _make_title(gft: str, domain: str) -> str:
    return f"GFT {gft} — {domain}"


async def _get_or_create_buyer(message: Message) -> Buyer:
    assert message.from_user
    async with async_session() as session:
        result = await session.execute(
            select(Buyer).where(Buyer.telegram_id == message.from_user.id)
        )
        buyer = result.scalar_one_or_none()
        if buyer:
            return buyer

        buyer = Buyer(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
        )
        session.add(buyer)
        await session.commit()
        await session.refresh(buyer)
        return buyer


async def _notify_admin(bot: Bot, text: str) -> None:
    if ADMIN_TELEGRAM_ID:
        try:
            await bot.send_message(ADMIN_TELEGRAM_ID, text, parse_mode="HTML")
        except Exception:
            logger.exception("Failed to notify admin")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "👋 Привет! Я бот для заявок по креативам и лендингам.\n\n"
        "Команды:\n"
        "/upload — новая заявка (GFT, тип, домен, проблема, креатив)\n"
        "/my — мои заявки\n"
        "/chat — написать ревьюеру\n"
        "/cancel — отменить текущее действие"
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Действие отменено.")


@router.message(Command("upload"))
async def cmd_upload(message: Message, state: FSMContext) -> None:
    await state.set_state(UploadStates.waiting_gft)
    await message.answer("🔢 Введите номер GFT (например: 1833):")


@router.message(UploadStates.waiting_gft)
async def process_gft(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Отправьте номер GFT текстом.")
        return
    await state.update_data(gft=message.text.strip())
    await state.set_state(UploadStates.waiting_type)
    await message.answer("📋 Введите тип (например: White, Black):")


@router.message(UploadStates.waiting_type)
async def process_type(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Отправьте тип текстом.")
        return
    await state.update_data(submission_type=message.text.strip())
    await state.set_state(UploadStates.waiting_domain)
    await message.answer("🌐 Введите домен (например: consultanta-audit.info):")


@router.message(UploadStates.waiting_domain)
async def process_domain(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Отправьте домен текстом.")
        return
    await state.update_data(domain=message.text.strip())
    await state.set_state(UploadStates.waiting_problem)
    await message.answer("✍️ Опишите проблему:")


@router.message(UploadStates.waiting_problem)
async def process_problem(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Опишите проблему текстом.")
        return
    await state.update_data(problem=message.text.strip())
    await state.set_state(UploadStates.waiting_file)
    await message.answer(
        "📎 Отправьте креатив — фото, видео или файл.\n"
        "Или напишите «-» если креатива нет."
    )


@router.message(UploadStates.waiting_file, F.text)
async def process_file_skip(message: Message, state: FSMContext, bot: Bot) -> None:
    if message.text and message.text.strip() == "-":
        await _save_submission(message, state, bot, file_data=None)
        return
    await message.answer("Отправьте файл (фото/видео/документ) или «-» чтобы пропустить.")


@router.message(UploadStates.waiting_file, F.document | F.photo | F.video)
async def process_file(message: Message, state: FSMContext, bot: Bot) -> None:
    file_data: dict = {}
    if message.document:
        file_data = {
            "file_id": message.document.file_id,
            "file_name": message.document.file_name or "creative",
            "mime": message.document.mime_type,
        }
    elif message.photo:
        photo = message.photo[-1]
        file_data = {
            "file_id": photo.file_id,
            "file_name": f"photo_{photo.file_unique_id}.jpg",
            "mime": "image/jpeg",
        }
    elif message.video:
        file_data = {
            "file_id": message.video.file_id,
            "file_name": message.video.file_name or f"video_{message.video.file_unique_id}.mp4",
            "mime": message.video.mime_type,
        }

    await _save_submission(message, state, bot, file_data=file_data)


@router.message(UploadStates.waiting_file)
async def process_file_invalid(message: Message) -> None:
    await message.answer("Отправьте файл или «-» чтобы пропустить креатив.")


async def _save_submission(
    message: Message,
    state: FSMContext,
    bot: Bot,
    *,
    file_data: dict | None,
) -> None:
    data = await state.get_data()
    gft = data["gft"]
    submission_type = data["submission_type"]
    domain = data["domain"]
    problem = data["problem"]
    title = _make_title(gft, domain)

    file_name = None
    file_path = None
    mime = None
    doc_url = None
    doc_id = None

    if file_data:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        tg_file = await bot.get_file(file_data["file_id"])
        assert tg_file.file_path
        file_name = file_data["file_name"]
        mime = file_data["mime"]
        local_name = f"{message.from_user.id}_{file_name}"
        local_path = UPLOAD_DIR / local_name
        await bot.download_file(tg_file.file_path, local_path)
        file_path = str(local_path)

    buyer = await _get_or_create_buyer(message)

    async with async_session() as session:
        submission = Submission(
            buyer_id=buyer.id,
            gft=gft,
            submission_type=submission_type,
            domain=domain,
            problem=problem,
            title=title,
            description=problem,
            file_name=file_name,
            file_path=file_path,
            file_type=mime,
            status=SubmissionStatus.NEW,
        )
        session.add(submission)
        await session.commit()
        await session.refresh(submission)

        if file_path:
            doc_id, doc_url = google_service.create_submission_doc(
                buyer_name=buyer.full_name or "Бюер",
                buyer_username=buyer.username,
                gft=gft,
                submission_type=submission_type,
                domain=domain,
                problem=problem,
                file_path=file_path,
                file_name=file_name or "creative",
                submission_id=submission.id,
            )
            if doc_url:
                submission.google_doc_url = doc_url
                submission.google_doc_id = doc_id
                await session.commit()

        submission_id = submission.id
        submission.created_at = submission.created_at or datetime.now(MSK)

        creative_url = doc_url
        txt_path, json_path = save_submission_files(
            submission, buyer, creative_url=creative_url
        )

        admin_text = format_submission_message(
            submission,
            buyer,
            creative_url=creative_url,
            html=True,
        )

    await state.clear()

    buyer_response = (
        f"✅ Заявка отправлена!\n"
        f"#{submission_id} — {title}\n"
        f"Статус: ожидает проверки\n"
        f"📁 Файл: {txt_path.name}"
    )
    if doc_url:
        buyer_response += f"\n\n📄 Google Doc: {doc_url}"

    await message.answer(buyer_response)
    await _notify_admin(bot, admin_text)


@router.message(Command("my"))
async def cmd_my_submissions(message: Message) -> None:
    buyer = await _get_or_create_buyer(message)

    async with async_session() as session:
        result = await session.execute(
            select(Submission)
            .where(Submission.buyer_id == buyer.id)
            .order_by(Submission.created_at.desc())
            .limit(10)
        )
        submissions = result.scalars().all()

    if not submissions:
        await message.answer("У вас пока нет заявок. Используйте /upload")
        return

    status_labels = {
        SubmissionStatus.NEW: "🆕 Новая",
        SubmissionStatus.IN_REVIEW: "🔍 На проверке",
        SubmissionStatus.NEEDS_FIX: "✏️ Нужны правки",
        SubmissionStatus.APPROVED: "✅ Одобрен",
        SubmissionStatus.REJECTED: "❌ Отклонён",
    }

    lines = ["📋 <b>Ваши заявки:</b>\n"]
    for s in submissions:
        label = status_labels.get(s.status, s.status.value)
        lines.append(f"#{s.id} — GFT {s.gft or '?'} / {s.domain or s.title}")
        lines.append(f"   {label}")
        if s.google_doc_url:
            lines.append(f"   📄 {s.google_doc_url}")
        lines.append("")

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("chat"))
async def cmd_chat(message: Message, state: FSMContext) -> None:
    buyer = await _get_or_create_buyer(message)

    async with async_session() as session:
        result = await session.execute(
            select(Submission)
            .where(Submission.buyer_id == buyer.id)
            .order_by(Submission.created_at.desc())
            .limit(5)
        )
        submissions = result.scalars().all()

    if not submissions:
        await message.answer("Сначала создайте заявку через /upload")
        return

    lines = ["💬 Выберите заявку — напишите номер:\n"]
    for s in submissions:
        lines.append(f"#{s.id} — GFT {s.gft or '?'} / {s.domain or s.title}")
    await state.set_state(ChatStates.waiting_message)
    await state.update_data(chat_step="pick_submission", submissions=[s.id for s in submissions])
    await message.answer("\n".join(lines))


@router.message(ChatStates.waiting_message)
async def process_chat(message: Message, state: FSMContext, bot: Bot) -> None:
    if not message.text:
        return

    data = await state.get_data()
    step = data.get("chat_step")

    if step == "pick_submission":
        try:
            submission_id = int(message.text.strip().lstrip("#"))
        except ValueError:
            await message.answer("Введите номер заявки, например: 1")
            return

        submission_ids = data.get("submissions", [])
        if submission_id not in submission_ids:
            await message.answer("Заявка не найдена. Выберите из списка.")
            return

        await state.update_data(chat_step="write_message", submission_id=submission_id)
        await message.answer(f"Напишите сообщение для заявки #{submission_id}:")
        return

    if step == "write_message":
        submission_id = data.get("submission_id")
        buyer = await _get_or_create_buyer(message)
        text = message.text.strip()

        async with async_session() as session:
            result = await session.execute(
                select(Submission)
                .options(selectinload(Submission.buyer))
                .where(Submission.id == submission_id)
            )
            submission = result.scalar_one_or_none()
            if not submission:
                await message.answer("Заявка не найдена.")
                await state.clear()
                return

            msg = Message(
                submission_id=submission_id,
                buyer_id=buyer.id,
                sender=MessageSender.BUYER,
                text=text,
            )
            session.add(msg)
            await session.commit()

        await state.clear()
        await message.answer("✅ Сообщение отправлено ревьюеру.")

        admin_text = (
            f"💬 <b>Сообщение от бюера</b> (заявка #{submission_id})\n"
            f"От: {buyer.username or buyer.full_name} (id: {buyer.telegram_id})\n\n"
            f"{text}"
        )
        await _notify_admin(bot, admin_text)


def create_bot() -> tuple[Bot, Dispatcher]:
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    return bot, dp
