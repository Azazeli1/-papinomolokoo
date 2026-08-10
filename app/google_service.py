import logging
from datetime import datetime
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from app.config import (
    GOOGLE_CREDENTIALS_FILE,
    GOOGLE_DRIVE_FOLDER_ID,
    REVIEWER_EMAIL,
)

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
]


class GoogleService:
    def __init__(self) -> None:
        self._drive = None
        self._docs = None
        self._enabled = False
        self._init_clients()

    def _init_clients(self) -> None:
        creds_path = Path(GOOGLE_CREDENTIALS_FILE)
        if not creds_path.exists():
            logger.warning(
                "Google credentials not found at %s — docs will not be created",
                creds_path,
            )
            return

        try:
            credentials = service_account.Credentials.from_service_account_file(
                str(creds_path), scopes=SCOPES
            )
            self._drive = build("drive", "v3", credentials=credentials)
            self._docs = build("docs", "v1", credentials=credentials)
            self._enabled = True
        except Exception:
            logger.exception("Failed to initialize Google API clients")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def create_submission_doc(
        self,
        *,
        buyer_name: str,
        buyer_username: str | None,
        title: str,
        description: str | None,
        file_path: str,
        file_name: str,
        submission_id: int,
    ) -> tuple[str | None, str | None]:
        if not self._enabled:
            return None, None

        try:
            file_id = self._upload_creative(file_path, file_name, buyer_name)
            doc_id, doc_url = self._create_doc(
                buyer_name=buyer_name,
                buyer_username=buyer_username,
                title=title,
                description=description,
                file_name=file_name,
                file_id=file_id,
                submission_id=submission_id,
            )
            if REVIEWER_EMAIL and doc_id:
                self._share_with_reviewer(doc_id)
            return doc_id, doc_url
        except Exception:
            logger.exception("Failed to create Google Doc for submission %s", submission_id)
            return None, None

    def _upload_creative(self, file_path: str, file_name: str, buyer_name: str) -> str | None:
        media = MediaFileUpload(file_path, resumable=True)
        metadata: dict = {
            "name": f"{buyer_name} — {file_name}",
        }
        if GOOGLE_DRIVE_FOLDER_ID:
            metadata["parents"] = [GOOGLE_DRIVE_FOLDER_ID]

        result = (
            self._drive.files()
            .create(body=metadata, media_body=media, fields="id, webViewLink")
            .execute()
        )
        return result.get("id")

    def _create_doc(
        self,
        *,
        buyer_name: str,
        buyer_username: str | None,
        title: str,
        description: str | None,
        file_name: str,
        file_id: str | None,
        submission_id: int,
    ) -> tuple[str | None, str | None]:
        doc_metadata: dict = {"title": f"Креатив #{submission_id} — {title}"}
        if GOOGLE_DRIVE_FOLDER_ID:
            doc_metadata["parents"] = [GOOGLE_DRIVE_FOLDER_ID]

        doc = self._drive.files().create(body=doc_metadata, fields="id").execute()
        doc_id = doc["id"]

        creative_link = ""
        if file_id:
            file_meta = (
                self._drive.files().get(fileId=file_id, fields="webViewLink").execute()
            )
            creative_link = file_meta.get("webViewLink", "")

        username_line = f"@{buyer_username}" if buyer_username else "—"
        now = datetime.now().strftime("%d.%m.%Y %H:%M")

        requests = [
            {
                "insertText": {
                    "location": {"index": 1},
                    "text": (
                        f"Заявка на проверку креатива\n"
                        f"{'=' * 40}\n\n"
                        f"ID заявки: #{submission_id}\n"
                        f"Бюер: {buyer_name} ({username_line})\n"
                        f"Название: {title}\n"
                        f"Дата: {now}\n"
                        f"Файл: {file_name}\n"
                    ),
                }
            }
        ]

        if description:
            requests.append(
                {
                    "insertText": {
                        "location": {"index": 1},
                        "text": f"Описание: {description}\n\n",
                    }
                }
            )

        if creative_link:
            requests.append(
                {
                    "insertText": {
                        "location": {"index": 1},
                        "text": f"Ссылка на креатив: {creative_link}\n\n",
                    }
                }
            )

        requests.append(
            {
                "insertText": {
                    "location": {"index": 1},
                    "text": (
                        "\n--- Комментарии ревьюера ---\n"
                        "(пишите правки и замечания здесь)\n"
                    ),
                }
            }
        )

        self._docs.documents().batchUpdate(
            documentId=doc_id, body={"requests": list(reversed(requests))}
        ).execute()

        doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"
        return doc_id, doc_url

    def _share_with_reviewer(self, file_id: str) -> None:
        self._drive.permissions().create(
            fileId=file_id,
            body={"type": "user", "role": "writer", "emailAddress": REVIEWER_EMAIL},
            sendNotificationEmail=True,
        ).execute()


google_service = GoogleService()
