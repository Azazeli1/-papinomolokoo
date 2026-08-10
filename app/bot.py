import logging
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import ADMIN_TELEGRAM_ID, TELEGRAM_BOT_TOKEN, UPLOAD_DIR
from app.database import async_session
from app.google_service import google_service
from app.models import Buyer, Message, MessageSender, Submission, SubmissionStatus

logger = logging.getLogger(__name__)

router = Router()


class UploadStates(StatesGroup):
    waiting_title = State()
    waiting_description = State()
    waiting_file = State()


class ChatStates(StatesGroup):
    waiting_message = State()


def _buyer_display_name(message: Message) -> str:
    if message.from_user and message.from_user.full_name:
        return message.from_user.full_name
    return "Бюер"


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
        "👋 Привет! Я бот для загрузки креативов на проверку.\n\n"
        "Команды:\n"
        "/upload — загрузить новый креатив\n"
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
    await state.set_state(UploadStates.waiting_title)
    await message.answer("📝 Введите название креатива (кампания / гео / оффер):")


@router.message(UploadStates.waiting_title)
async def process_title(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Пожалуйста, отправьте текстовое название.")
        return
    await state.update_data(title=message.text.strip())
    await state.set_state(UploadStates.waiting_description)
    await message.answer(
        "📋 Добавьте описание (опционально).\n"
        "Напишите текст или отправьте «-» чтобы пропустить."
    )


@router.message(UploadStates.waiting_description)
async def process_description(message: Message, state: FSMContext) -> None:
    desc = None
    if message.text and message.text.strip() != "-":
        desc = message.text.strip()
    await state.update_data(description=desc)
    await state.set_state(UploadStates.waiting_file)
    await message.answer(
        "📎 Теперь отправьте креатив — фото, видео или документ.\n"
        "Можно отправить как файл (без сжатия)."
    )


@router.message(UploadStates.waiting_file, F.document | F.photo | F.video)
async def process_file(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    title = data.get("title", "Без названия")
    description = data.get("description")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    if message.document:
        file_id = message.document.file_id
        file_name = message.document.file_name or "creative"
        mime = message.document.mime_type
    elif message.photo:
        photo = message.photo[-1]
        file_id = photo.file_id
        file_name = f"photo_{photo.file_unique_id}.jpg"
        mime = "image/jpeg"
    elif message.video:
        file_id = message.video.file_id
        file_name = message.video.file_name or f"video_{message.video.file_unique_id}.mp4"
        mime = message.video.mime_type
    else:
        await message.answer("Неподдерживаемый формат. Отправьте фото, видео или документ.")
        return

    tg_file = await bot.get_file(file_id)
    assert tg_file.file_path
    local_name = f"{message.from_user.id}_{file_name}"
    local_path = UPLOAD_DIR / local_name
    await bot.download_file(tg_file.file_path, local_path)

    buyer = await _get_or_create_buyer(message)

    async with async_session() as session:
        submission = Submission(
            buyer_id=buyer.id,
            title=title,
            description=description,
            file_name=file_name,
            file_path=str(local_path),
            file_type=mime,
            status=SubmissionStatus.NEW,
        )
        session.add(submission)
        await session.commit()
        await session.refresh(submission)

        doc_id, doc_url = google_service.create_submission_doc(
            buyer_name=buyer.full_name or "Бюер",
            buyer_username=buyer.username,
            title=title,
            description=description,
            file_path=str(local_path),
            file_name=file_name,
            submission_id=submission.id,
        )

        if doc_url:
            submission.google_doc_url = doc_url
            submission.google_doc_id = doc_id
            await session.commit()

        submission_id = submission.id

    await state.clear()

    response = (
        f"✅ Креатив <b>«{title}»</b> отправлен на проверку!\n"
        f"Заявка #{submission_id}\n"
        f"Статус: ожидает проверки"
    )
    if doc_url:
        response += f"\n\n📄 Google Doc: {doc_url}"

    await message.answer(response, parse_mode="HTML")

    admin_text = (
        f"🆕 <b>Новый креатив от бюера</b>\n"
        f"Заявка #{submission_id}\n"
        f"Бюер: {buyer.full_name}"
    )
    if buyer.username:
        admin_text += f" (@{buyer.username})"
    admin_text += f"\nНазвание: {title}"
    if doc_url:
        admin_text += f"\n\n📄 <a href='{doc_url}'>Открыть Google Doc</a>"
    admin_text += f"\n\n💬 Панель: /admin (веб)"

    await _notify_admin(bot, admin_text)


@router.message(UploadStates.waiting_file)
async def process_file_invalid(message: Message) -> None:
    await message.answer("Отправьте файл — фото, видео или документ.")


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
        lines.append(f"#{s.id} — {s.title}\n   {label}")
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
        await message.answer("Сначала загрузите креатив через /upload")
        return

    lines = ["💬 Выберите заявку — напишите номер:\n"]
    for s in submissions:
        lines.append(f"#{s.id} — {s.title}")
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
            f"От: {buyer.full_name}"
        )
        if buyer.username:
            admin_text += f" (@{buyer.username})"
        admin_text += f"\n\n{text}"
        await _notify_admin(bot, admin_text)


def create_bot() -> tuple[Bot, Dispatcher]:
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    return bot, dp
