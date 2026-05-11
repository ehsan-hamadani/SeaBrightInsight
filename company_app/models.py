"""ORM models."""

from datetime import datetime
from typing import List

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Company(Base):
    """A company record in the directory database."""

    __tablename__ = "companies"

    # Primary key: stable SQLite INTEGER row id (referenced by directors.company_id).
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_name: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    company_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    company_area: Mapped[str | None] = mapped_column(String(200), nullable=True)
    company_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    company_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    company_telephone: Mapped[str | None] = mapped_column(String(200), nullable=True)
    company_email: Mapped[str | None] = mapped_column(String(500), nullable=True)
    company_house_number: Mapped[str | None] = mapped_column(
        String(32), nullable=True, index=True
    )
    company_house_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    company_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    pipeline_stage: Mapped[str | None] = mapped_column(
        String(80), nullable=True, index=True
    )
    include_flag: Mapped[str | None] = mapped_column(String(50), nullable=True)
    connected: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    directors: Mapped[List["Director"]] = relationship(
        "Director", back_populates="company", cascade="all, delete-orphan"
    )


class Director(Base):
    """A director or officer associated with a company."""

    __tablename__ = "directors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id"), nullable=False, index=True
    )
    director_name: Mapped[str] = mapped_column(String(500), nullable=False)
    role: Mapped[str | None] = mapped_column(String(128), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email: Mapped[str | None] = mapped_column(String(500), nullable=True)
    interaction_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    appointment_date: Mapped[str | None] = mapped_column(String(64), nullable=True)

    company: Mapped["Company"] = relationship("Company", back_populates="directors")
