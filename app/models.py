"""Modelos de datos: archivos subidos, transacciones y chat."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str] = mapped_column(String(128), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    # Mapeo de columnas detectado/elegido, como JSON serializado.
    column_mapping: Mapped[str] = mapped_column(Text, default="{}")
    detected_columns: Mapped[str] = mapped_column(Text, default="[]")
    notes: Mapped[str] = mapped_column(Text, default="")
    default_person: Mapped[str] = mapped_column(String(120), default="")

    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="file", cascade="all, delete-orphan", passive_deletes=True
    )


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_id: Mapped[int] = mapped_column(
        ForeignKey("uploaded_files.id", ondelete="CASCADE"), index=True
    )
    date: Mapped[dt.date | None] = mapped_column(Date, nullable=True, index=True)
    description: Mapped[str] = mapped_column(String(512), default="")
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(8), default="")
    category: Mapped[str] = mapped_column(String(160), default="Sin categoria", index=True)
    person: Mapped[str] = mapped_column(String(160), default="Sin asignar", index=True)
    account: Mapped[str] = mapped_column(String(160), default="")
    raw: Mapped[str] = mapped_column(Text, default="{}")

    file: Mapped[UploadedFile] = relationship(back_populates="transactions")


Index("ix_tx_file_date", Transaction.file_id, Transaction.date)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    model: Mapped[str] = mapped_column(String(160), default="")
