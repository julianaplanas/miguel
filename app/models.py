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
    # Un archivo subido pero todavia no confirmado en la pantalla de
    # revision. Mientras sea False no tiene movimientos: no esta en el
    # dashboard ni en los totales.
    imported: Mapped[bool] = mapped_column(Boolean, default=False)
    # Quien leyo el archivo: "" (el lector automatico) o "modelo". Cuando
    # lo leyo el modelo, sus filas quedan guardadas aca en JSON para no
    # volver a pagar la lectura en cada vista previa ni al reimportar.
    reader: Mapped[str] = mapped_column(String(16), default="")
    ai_rows: Mapped[str] = mapped_column(Text, default="")

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
    # De donde salio la categoria: file / rule / ai / manual. Lo que el
    # usuario corrigio a mano no se vuelve a pisar al recategorizar.
    category_source: Mapped[str] = mapped_column(String(16), default="")
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


class ExchangeRate(Base):
    """Tipo de cambio hacia la moneda base: 1 `code` = `rate` de la base.

    No se consulta ninguna API: en Argentina el tipo que le sirve a cada uno
    (oficial, MEP, blue) es una decision personal, asi que el valor lo pone
    el usuario a mano desde Ajustes.
    """

    __tablename__ = "exchange_rates"

    code: Mapped[str] = mapped_column(String(8), primary_key=True)
    base: Mapped[str] = mapped_column(String(8), default="")
    rate: Mapped[float] = mapped_column(Float, default=1.0)
    # "manual", o el proveedor usado: "dolarapi:blue", "erapi"...
    source: Mapped[str] = mapped_column(String(64), default="manual")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class AppSetting(Base):
    """Preferencias que el usuario cambia desde la UI (p. ej. la moneda base).

    Las variables de entorno solo dan el valor inicial: lo que se guarda aqui
    manda, para no tener que redesplegar por cambiar la moneda.
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class RateHistory(Base):
    """Cotizacion de una moneda en una fecha concreta.

    Con inflacion alta, convertir un gasto de enero al tipo de hoy deforma
    cualquier comparacion entre meses. Guardando la serie se puede convertir
    cada movimiento al tipo que regia el dia que ocurrio.
    """

    __tablename__ = "rate_history"

    code: Mapped[str] = mapped_column(String(8), primary_key=True)
    base: Mapped[str] = mapped_column(String(8), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    rate: Mapped[float] = mapped_column(Float, default=1.0)
    source: Mapped[str] = mapped_column(String(64), default="")


class CategoryRule(Base):
    """Regla de categorizacion: si la descripcion contiene X, es de tal categoria."""

    __tablename__ = "category_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pattern: Mapped[str] = mapped_column(String(200), index=True)
    category: Mapped[str] = mapped_column(String(160))
    # "manual" (la escribio el usuario) o "ai" (la sugirio el modelo).
    source: Mapped[str] = mapped_column(String(16), default="manual")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
