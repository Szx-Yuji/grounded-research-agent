import hashlib
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.core.config import settings

ALLOWED_DOCUMENT_EXTENSIONS = {".pdf", ".txt", ".md"}
READ_CHUNK_SIZE = 1024 * 1024


async def save_document_file(
    file: UploadFile,
    *,
    owner_id: uuid.UUID,
    knowledge_base_id: uuid.UUID,
    document_id: uuid.UUID,
) -> tuple[str, int, str, str]:
    original_filename = Path(file.filename or "").name

    if not original_filename:
        raise HTTPException(
            status_code=422,
            detail="Document filename is required",
        )

    suffix = Path(original_filename).suffix.lower()

    if suffix not in ALLOWED_DOCUMENT_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail="Only PDF, TXT and Markdown files are supported",
        )

    storage_key = f"{owner_id}/{knowledge_base_id}/{document_id}{suffix}"

    target_path = settings.DOCUMENT_STORAGE_DIR / storage_key
    target_path.parent.mkdir(parents=True, exist_ok=True)

    size_bytes = 0
    content_hasher = hashlib.sha256()

    try:
        with target_path.open("wb") as output_file:
            while True:
                chunk = await file.read(READ_CHUNK_SIZE)

                if not chunk:
                    break

                size_bytes += len(chunk)
                content_hasher.update(chunk)

                if size_bytes > settings.MAX_DOCUMENT_SIZE_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="Document is too large",
                    )

                output_file.write(chunk)

        if size_bytes == 0:
            raise HTTPException(
                status_code=422,
                detail="Document must not be empty",
            )

    except Exception:
        target_path.unlink(missing_ok=True)
        raise

    finally:
        await file.close()

    return (
        storage_key,
        size_bytes,
        original_filename,
        content_hasher.hexdigest(),
    )


def delete_document_file(storage_key: str) -> None:
    storage_root = settings.DOCUMENT_STORAGE_DIR.resolve()
    target_path = (storage_root / storage_key).resolve()

    if not target_path.is_relative_to(storage_root):
        raise ValueError("Invalid document storage key")

    target_path.unlink(missing_ok=True)
