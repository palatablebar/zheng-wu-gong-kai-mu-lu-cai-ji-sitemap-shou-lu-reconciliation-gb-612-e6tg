"""跨站稿件组（算法 C 的落库结果）：一份稿件 = 多个站点入口的转载关系。

- `doc_groups.group_key`：`doc-<sha1(原稿 canonical)[:16]>`，自然键唯一，重跑幂等；
- `doc_group_members.catalog_item_id` 唯一：一条目录入口只属于一个稿件组，
  重跑巡检不会重复生成关系，也不会重复记账；
- 成员保留各站真实入口（entry_url）与判定证据（via / declared_canonical），
  缺陷整改仍按入口维度流转，主站整改不会掩盖其他站漏收。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base
from src.utils.timeutil import utcnow


class DocGroup(Base):
    __tablename__ = "doc_groups"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_key: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    origin_url: Mapped[str] = mapped_column(String(300), default="")
    origin_site_code: Mapped[str] = mapped_column(String(40), default="", index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    entry_count: Mapped[int] = mapped_column(Integer, default=1)
    site_count: Mapped[int] = mapped_column(Integer, default=1)
    first_seen_run_id: Mapped[int | None] = mapped_column(ForeignKey("crawl_runs.id", ondelete="SET NULL"), nullable=True)
    last_seen_run_id: Mapped[int | None] = mapped_column(ForeignKey("crawl_runs.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class DocGroupMember(Base):
    __tablename__ = "doc_group_members"
    __table_args__ = (UniqueConstraint("catalog_item_id", name="uq_doc_member_catalog_item"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("doc_groups.id", ondelete="CASCADE"), index=True)
    catalog_item_id: Mapped[int] = mapped_column(ForeignKey("catalog_items.id", ondelete="CASCADE"), index=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    site_code: Mapped[str] = mapped_column(String(40), default="")
    entry_url: Mapped[str] = mapped_column(String(300), default="")
    canonical_url: Mapped[str] = mapped_column(String(300), default="", index=True)
    role: Mapped[str] = mapped_column(String(12), default="origin", index=True)  # origin / repost
    via: Mapped[str] = mapped_column(String(20), default="self")  # self / canonical_tag
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
