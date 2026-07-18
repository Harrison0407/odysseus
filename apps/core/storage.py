"""Storage abstraction (spec section 6): documents persist behind an
interface so a future move to S3-compatible object storage does not touch
model or view code. Today the only implementation is local disk, outside
any publicly served static/media path — downloads are always mediated by
an authenticated view (apps.documents.views.download).
"""

import hashlib
import os
import uuid
from pathlib import Path

from django.conf import settings


class DocumentStorage:
    """Minimal local-disk implementation of the document storage adapter."""

    def __init__(self, root=None):
        self.root = Path(root or settings.DOCUMENT_STORAGE_ROOT)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, stored_name: str) -> Path:
        return self.root / stored_name[:2] / stored_name

    def save(self, file_obj, original_filename: str) -> dict:
        """Streams the upload to disk, computing SHA-256 as it goes, and
        returns provenance metadata. Never trusts the filename alone."""
        ext = Path(original_filename).suffix.lower()
        stored_name = f"{uuid.uuid4().hex}{ext}"
        dest = self._path_for(stored_name)
        dest.parent.mkdir(parents=True, exist_ok=True)

        sha256 = hashlib.sha256()
        size = 0
        with open(dest, "wb") as out:
            for chunk in file_obj.chunks() if hasattr(file_obj, "chunks") else iter(lambda: file_obj.read(65536), b""):
                out.write(chunk)
                sha256.update(chunk)
                size += len(chunk)

        return {
            "stored_name": stored_name,
            "sha256": sha256.hexdigest(),
            "size_bytes": size,
            "original_filename": original_filename,
        }

    def open(self, stored_name: str):
        return open(self._path_for(stored_name), "rb")

    def delete(self, stored_name: str) -> None:
        # Immutability (core principle 4.4): originals are never deleted by
        # normal application flows. This exists only for admin cleanup of
        # orphaned uploads that never became a confirmed DocumentVersion.
        path = self._path_for(stored_name)
        if path.exists():
            os.remove(path)


document_storage = DocumentStorage()
