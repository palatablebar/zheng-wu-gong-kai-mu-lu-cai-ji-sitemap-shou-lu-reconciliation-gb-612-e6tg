"""跨站稿件组服务：算法 C 的幂等落库 + 报表统计。

幂等口径（重跑不重复生成关系、不重复记账）：
- 组按自然键 `group_key` upsert：已存在只刷新标题/计数/last_seen_run_id；
- 成员按 `catalog_item_id` upsert（唯一约束）：一条目录入口始终只属于一个组；
- 收敛：全量重算后，不再出现的成员删除、空组删除；
- 返回 {created, reused, dropped}：重跑同一批数据时 created=0、dropped=0。
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.models.catalog_item import CatalogItem
from src.models.crawl_run import CrawlRun
from src.models.doc_group import DocGroup, DocGroupMember
from src.services import repost_engine
from src.utils.timeutil import iso_utc


def _catalog_entries(db: Session) -> list[dict]:
    code_by_id = _site_codes(db)
    rows = db.scalars(select(CatalogItem).order_by(CatalogItem.id)).all()
    return [
        {
            "id": row.id,
            "site_id": row.site_id,
            "site_code": code_by_id.get(row.site_id, ""),
            "entry_url": row.entry_url,
            "canonical_url": row.canonical_url,
            "page_canonical": row.page_canonical,
            "title": row.title,
            "column_name": row.column_name,
        }
        for row in rows
    ]


def _site_codes(db: Session) -> dict[int, str]:
    from src.models.site import Site

    return {site.id: site.code for site in db.scalars(select(Site)).all()}


def sync_doc_groups(db: Session, *, run_id: int, groups: list[dict]) -> dict:
    """把算法 C 的归组结果幂等写入 doc_groups / doc_group_members。"""
    existing_groups = {group.group_key: group for group in db.scalars(select(DocGroup)).all()}
    existing_members = {
        member.catalog_item_id: member for member in db.scalars(select(DocGroupMember)).all()
    }
    created = reused = 0
    seen_items: set[int] = set()
    touched_groups: set[int] = set()

    for payload in groups:
        group = existing_groups.get(payload["group_key"])
        if group is None:
            group = DocGroup(
                group_key=payload["group_key"],
                origin_url=payload["origin_url"],
                origin_site_code=payload["origin_site_code"],
                title=payload["title"],
                entry_count=payload["entry_count"],
                site_count=payload["site_count"],
                first_seen_run_id=run_id,
                last_seen_run_id=run_id,
            )
            db.add(group)
            db.flush()
            existing_groups[group.group_key] = group
            created += 1
        else:
            group.origin_url = payload["origin_url"]
            group.origin_site_code = payload["origin_site_code"]
            group.title = payload["title"]
            group.entry_count = payload["entry_count"]
            group.site_count = payload["site_count"]
            group.last_seen_run_id = run_id
            reused += 1
        touched_groups.add(group.id)

        for member_payload in payload["members"]:
            item_id = member_payload["catalog_item_id"]
            seen_items.add(item_id)
            member = existing_members.get(item_id)
            evidence = {
                "declared_canonical": member_payload.get("declared_canonical") or "",
                "via": member_payload["via"],
            }
            if member is None:
                member = DocGroupMember(
                    group_id=group.id,
                    catalog_item_id=item_id,
                    site_id=member_payload["site_id"],
                    site_code=member_payload["site_code"],
                    entry_url=member_payload["entry_url"][:300],
                    canonical_url=member_payload["canonical_url"][:300],
                    role=member_payload["role"],
                    via=member_payload["via"],
                    evidence=evidence,
                )
                db.add(member)
                existing_members[item_id] = member
            else:
                member.group_id = group.id
                member.site_id = member_payload["site_id"]
                member.site_code = member_payload["site_code"]
                member.entry_url = member_payload["entry_url"][:300]
                member.canonical_url = member_payload["canonical_url"][:300]
                member.role = member_payload["role"]
                member.via = member_payload["via"]
                member.evidence = evidence
    db.flush()

    # 收敛：目录里已不存在的条目成员删除；空组删除
    dropped = 0
    for item_id, member in list(existing_members.items()):
        if item_id not in seen_items:
            db.delete(member)
            dropped += 1
    db.flush()
    empty_groups = db.scalars(
        select(DocGroup).where(
            ~select(DocGroupMember.id).where(DocGroupMember.group_id == DocGroup.id).exists()
        )
    ).all()
    dropped_groups = 0
    for group in empty_groups:
        db.delete(group)
        dropped_groups += 1
    db.flush()
    return {"created": created, "reused": reused, "dropped_members": dropped, "dropped_groups": dropped_groups}


def rebuild_doc_groups(db: Session, *, run: CrawlRun) -> dict:
    """全量重算稿件组并幂等落库，返回写 run.metrics 的 summary。"""
    result = repost_engine.build_doc_groups(_catalog_entries(db))
    sync = sync_doc_groups(db, run_id=run.id, groups=result["groups"])
    summary = {
        **result["stats"],
        "by_site": result["by_site"],
        "groups_created": sync["created"],
        "groups_reused": sync["reused"],
        "dangling": result["stats"]["dangling"],
    }
    return {"summary": summary, "sync": sync, "dangling_items": result["dangling"]}


def document_stats(db: Session) -> dict:
    """总览 / 站点对账用的稿件维度统计（实时从落库表算，数量与入口对平）。"""
    groups = db.scalars(select(DocGroup)).all()
    members = db.scalars(select(DocGroupMember)).all()
    entry_total = db.scalar(select(func.count()).select_from(CatalogItem)) or 0
    document_total = len(groups)
    by_site: dict[str, dict] = {}
    for member in members:
        slot = by_site.setdefault(
            member.site_code, {"code": member.site_code, "entries": 0, "documents": set(), "repost_in": 0}
        )
        slot["entries"] += 1
        slot["documents"].add(member.group_id)
        if member.role == repost_engine.ROLE_REPOST:
            slot["repost_in"] += 1
    origin_out: dict[str, int] = {}
    multi_site = 0
    for group in groups:
        if group.site_count > 1:
            multi_site += 1
            origin_out[group.origin_site_code] = origin_out.get(group.origin_site_code, 0) + 1
    per_site = {
        code: {
            "entries": slot["entries"],
            "documents": len(slot["documents"]),
            "repost_in": slot["repost_in"],
            "origin_out": origin_out.get(code, 0),
        }
        for code, slot in by_site.items()
    }
    return {
        "document_total": document_total,
        "entry_total": entry_total,
        "member_total": len(members),
        "merged_entries": entry_total - document_total,
        "repost_entries": sum(1 for member in members if member.role == repost_engine.ROLE_REPOST),
        "multi_site_groups": multi_site,
        "balanced": len(members) == entry_total,
        "by_site": per_site,
    }


def serialize_group(group: DocGroup, members: list[DocGroupMember], run_nos: dict[int, str]) -> dict:
    return {
        "id": group.id,
        "group_key": group.group_key,
        "title": group.title,
        "origin_url": group.origin_url,
        "origin_site_code": group.origin_site_code,
        "entry_count": group.entry_count,
        "site_count": group.site_count,
        "first_seen_run": run_nos.get(group.first_seen_run_id or 0),
        "last_seen_run": run_nos.get(group.last_seen_run_id or 0),
        "created_at": iso_utc(group.created_at),
        "members": [
            {
                "site_code": member.site_code,
                "entry_url": member.entry_url,
                "canonical_url": member.canonical_url,
                "role": member.role,
                "via": member.via,
                "declared_canonical": (member.evidence or {}).get("declared_canonical", ""),
            }
            for member in sorted(members, key=lambda item: (item.role != "origin", item.site_code, item.canonical_url))
        ],
    }


def list_documents(
    db: Session,
    *,
    site_code: str | None = None,
    multi_site: bool | None = None,
    q: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    stmt = select(DocGroup)
    count_stmt = select(func.count()).select_from(DocGroup)
    if multi_site is True:
        stmt = stmt.where(DocGroup.site_count > 1)
        count_stmt = count_stmt.where(DocGroup.site_count > 1)
    if site_code:
        member_group_ids = select(DocGroupMember.group_id).where(DocGroupMember.site_code == site_code)
        stmt = stmt.where(DocGroup.id.in_(member_group_ids))
        count_stmt = count_stmt.where(DocGroup.id.in_(member_group_ids))
    if q:
        like = f"%{q.strip()}%"
        clause = DocGroup.title.ilike(like) | DocGroup.origin_url.ilike(like) | DocGroup.group_key.ilike(like)
        stmt = stmt.where(clause)
        count_stmt = count_stmt.where(clause)
    total = db.scalar(count_stmt) or 0
    groups = db.scalars(stmt.order_by(DocGroup.site_count.desc(), DocGroup.id).limit(limit).offset(offset)).all()
    group_ids = [group.id for group in groups]
    members_by_group: dict[int, list[DocGroupMember]] = {group_id: [] for group_id in group_ids}
    if group_ids:
        for member in db.scalars(select(DocGroupMember).where(DocGroupMember.group_id.in_(group_ids))).all():
            members_by_group.setdefault(member.group_id, []).append(member)
    run_nos = {run.id: run.run_no for run in db.scalars(select(CrawlRun)).all()}
    return {
        "items": [serialize_group(group, members_by_group.get(group.id, []), run_nos) for group in groups],
        "total": total,
        "limit": limit,
        "offset": offset,
        "summary": document_stats(db),
    }


def group_for_canonical(db: Session, canonical_url: str) -> dict | None:
    """缺陷详情用：按入口 canonical 找到所属稿件组（含各站真实入口）。"""
    member = db.scalar(select(DocGroupMember).where(DocGroupMember.canonical_url == canonical_url))
    if member is None:
        return None
    group = db.get(DocGroup, member.group_id)
    if group is None:
        return None
    members = db.scalars(select(DocGroupMember).where(DocGroupMember.group_id == group.id)).all()
    run_nos = {run.id: run.run_no for run in db.scalars(select(CrawlRun)).all()}
    return serialize_group(group, list(members), run_nos)


def group_key_map(db: Session, canonical_urls: list[str]) -> dict[str, str]:
    """缺陷列表用：canonical_url -> group_key 的批量映射（避免 N+1）。"""
    if not canonical_urls:
        return {}
    rows = db.execute(
        select(DocGroupMember.canonical_url, DocGroup.group_key)
        .join(DocGroup, DocGroup.id == DocGroupMember.group_id)
        .where(DocGroupMember.canonical_url.in_(canonical_urls))
    ).all()
    return {str(url): str(key) for url, key in rows}
