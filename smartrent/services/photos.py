"""Unit photos on disk: UPLOAD_DIR/units/<random name>.

Uploads are checked by their first bytes (file signature), not by the name or
the browser's Content-Type, so a renamed script is refused. The saved name is
random; the uploader's filename is never used as a path.
"""
import os
import secrets

from flask import current_app

from ..extensions import db
from ..models import Unit, UnitPhoto

SIGNATURES = {b"\xff\xd8\xff": "jpg", b"\x89PNG\r\n\x1a\n": "png"}
MAX_PER_UNIT = 12


def folder() -> str:
    path = os.path.join(current_app.config["UPLOAD_DIR"], "units")
    os.makedirs(path, exist_ok=True)
    return path


def detect(head: bytes) -> str | None:
    for sig, ext in SIGNATURES.items():
        if head.startswith(sig):
            return ext
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


def save(unit: Unit, file, caption: str | None = None) -> UnitPhoto:
    """Store one uploaded file for a unit. Raises ValueError with a user-facing message."""
    if len(unit.photos) >= MAX_PER_UNIT:
        raise ValueError(f"A unit can have at most {MAX_PER_UNIT} photos.")
    data = file.read()
    ext = detect(data[:12])
    if not ext:
        raise ValueError(f"{file.filename or 'File'} is not a JPEG, PNG or WebP image.")
    name = f"{secrets.token_hex(12)}.{ext}"
    with open(os.path.join(folder(), name), "wb") as fh:
        fh.write(data)
    position = max((p.position for p in unit.photos), default=-1) + 1
    photo = UnitPhoto(unit_id=unit.id, filename=name, caption=(caption or "").strip()[:120] or None, position=position)
    db.session.add(photo)
    return photo


def delete(photo: UnitPhoto) -> None:
    """Remove the row, and the file once no other row uses it (seed photos are shared)."""
    name = photo.filename
    db.session.delete(photo)
    db.session.flush()
    if not db.session.scalar(db.select(db.func.count(UnitPhoto.id)).where(UnitPhoto.filename == name)):
        try:
            os.remove(os.path.join(folder(), name))
        except FileNotFoundError:
            pass


def delete_all(unit: Unit) -> None:
    for photo in list(unit.photos):
        delete(photo)


def make_cover(photo: UnitPhoto) -> None:
    """Move a photo to the front; the rest keep their order."""
    others = [p for p in photo.unit.photos if p.id != photo.id]
    for i, p in enumerate([photo, *others]):
        p.position = i
