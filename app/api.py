import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import ADMIN_PASSWORD
from app.database import get_session
from app.models import Buyer, Message, MessageSender, Submission, SubmissionStatus
from app.notifications import notify_buyer

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


class LoginRequest(BaseModel):
    password: str


class StatusUpdate(BaseModel):
    status: SubmissionStatus


class MessageCreate(BaseModel):
    text: str


class ConnectionManager:
    def __init__(self) -> None:
        self.active: dict[int, list[WebSocket]] = {}

    async def connect(self, submission_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active.setdefault(submission_id, []).append(websocket)

    def disconnect(self, submission_id: int, websocket: WebSocket) -> None:
        if submission_id in self.active:
            self.active[submission_id] = [
                ws for ws in self.active[submission_id] if ws != websocket
            ]

    async def broadcast(self, submission_id: int, data: dict) -> None:
        for ws in self.active.get(submission_id, []):
            try:
                await ws.send_json(data)
            except Exception:
                pass


manager = ConnectionManager()

STATUS_LABELS = {
    SubmissionStatus.NEW: "Новая",
    SubmissionStatus.IN_REVIEW: "На проверке",
    SubmissionStatus.NEEDS_FIX: "Нужны правки",
    SubmissionStatus.APPROVED: "Одобрен",
    SubmissionStatus.REJECTED: "Отклонён",
}


def _submission_to_dict(s: Submission) -> dict:
    return {
        "id": s.id,
        "gft": s.gft,
        "submission_type": s.submission_type,
        "domain": s.domain,
        "problem": s.problem or s.description,
        "title": s.title,
        "description": s.description,
        "file_name": s.file_name,
        "file_type": s.file_type,
        "google_doc_url": s.google_doc_url,
        "status": s.status.value,
        "status_label": STATUS_LABELS.get(s.status, s.status.value),
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "buyer": {
            "id": s.buyer.id,
            "name": s.buyer.full_name or "Бюер",
            "username": s.buyer.username,
            "telegram_id": s.buyer.telegram_id,
        },
    }


def _check_auth(request: Request) -> bool:
    return request.cookies.get("admin_auth") == ADMIN_PASSWORD


def _require_auth(request: Request) -> None:
    if not _check_auth(request):
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.get("/", response_class=HTMLResponse)
async def admin_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("admin.html", {"request": request})


@router.post("/api/login")
async def login(data: LoginRequest) -> dict:
    if data.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Неверный пароль")
    return {"ok": True}


@router.get("/api/submissions")
async def list_submissions(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    _require_auth(request)

    result = await session.execute(
        select(Submission)
        .options(selectinload(Submission.buyer))
        .order_by(Submission.created_at.desc())
    )
    submissions = result.scalars().all()

    return [_submission_to_dict(s) for s in submissions]


@router.get("/api/submissions/{submission_id}")
async def get_submission(
    submission_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    _require_auth(request)

    result = await session.execute(
        select(Submission)
        .options(selectinload(Submission.buyer))
        .where(Submission.id == submission_id)
    )
    s = result.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Not found")

    return _submission_to_dict(s)


@router.patch("/api/submissions/{submission_id}/status")
async def update_status(
    submission_id: int,
    data: StatusUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    _require_auth(request)

    result = await session.execute(select(Submission).where(Submission.id == submission_id))
    submission = result.scalar_one_or_none()
    if not submission:
        raise HTTPException(status_code=404, detail="Not found")

    submission.status = data.status
    await session.commit()

    result = await session.execute(
        select(Submission)
        .options(selectinload(Submission.buyer))
        .where(Submission.id == submission_id)
    )
    submission = result.scalar_one()
    if submission.buyer:
        label = STATUS_LABELS.get(data.status, data.status.value)
        await notify_buyer(
            submission.buyer.telegram_id,
            f"📋 Статус заявки <b>#{submission_id}</b> изменён: <b>{label}</b>",
        )

    return {"ok": True, "status": data.status.value}


@router.get("/api/submissions/{submission_id}/messages")
async def get_messages(
    submission_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    _require_auth(request)

    result = await session.execute(
        select(Message)
        .options(selectinload(Message.buyer))
        .where(Message.submission_id == submission_id)
        .order_by(Message.created_at)
    )
    messages = result.scalars().all()

    return [
        {
            "id": m.id,
            "sender": m.sender.value,
            "text": m.text,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "buyer_name": m.buyer.full_name if m.buyer else None,
        }
        for m in messages
    ]


@router.post("/api/submissions/{submission_id}/messages")
async def send_message(
    submission_id: int,
    data: MessageCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    _require_auth(request)

    result = await session.execute(
        select(Submission)
        .options(selectinload(Submission.buyer))
        .where(Submission.id == submission_id)
    )
    submission = result.scalar_one_or_none()
    if not submission:
        raise HTTPException(status_code=404, detail="Not found")

    msg = Message(
        submission_id=submission_id,
        sender=MessageSender.ADMIN,
        text=data.text.strip(),
    )
    session.add(msg)
    await session.commit()
    await session.refresh(msg)

    if submission.buyer:
        await notify_buyer(
            submission.buyer.telegram_id,
            f"💬 <b>Сообщение от ревьюера</b> (заявка #{submission_id})\n\n{data.text.strip()}",
        )

    payload = {
        "type": "message",
        "id": msg.id,
        "sender": msg.sender.value,
        "text": msg.text,
        "created_at": msg.created_at.isoformat() if msg.created_at else datetime.now(timezone.utc).isoformat(),
    }
    await manager.broadcast(submission_id, payload)

    return payload


@router.get("/api/buyers")
async def list_buyers(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    _require_auth(request)

    result = await session.execute(select(Buyer).order_by(Buyer.created_at.desc()))
    buyers = result.scalars().all()

    return [
        {
            "id": b.id,
            "name": b.full_name or "Бюер",
            "username": b.username,
            "telegram_id": b.telegram_id,
        }
        for b in buyers
    ]


@router.websocket("/ws/chat/{submission_id}")
async def websocket_chat(websocket: WebSocket, submission_id: int) -> None:
    await manager.connect(submission_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(submission_id, websocket)
