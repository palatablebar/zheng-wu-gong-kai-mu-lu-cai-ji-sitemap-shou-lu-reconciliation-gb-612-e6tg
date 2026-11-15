"""CSV 导出：缺陷清单 / 站点覆盖表 / 覆盖率报表 / 跨站稿件组。内容全部来自算法 B/C 的落库结果。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.constants.enums import DEFECT_CATEGORY_LABELS, DEFECT_SEVERITY_LABELS, DEFECT_STATUS_LABELS
from src.models.crawl_run import CrawlRun
from src.models.defect import Defect
from src.models.doc_group import DocGroup, DocGroupMember
from src.models.site import Site
from src.services import repost_service
from src.utils.csv_export import to_csv

DEFECT_HEADERS = [
    "缺陷ID",
    "站点编码",
    "站点名称",
    "分类",
    "分类说明",
    "严重度",
    "严重度说明",
    "栏目",
    "canonical URL",
    "sitemap loc",
    "证据摘要",
    "处理状态",
    "复核人",
    "复核备注",
    "检出时间",
]

SITE_HEADERS = [
    "站点编码",
    "站点名称",
    "责任部门",
    "区域",
    "首页",
    "sitemap",
    "目录条目数",
    "sitemap loc 数",
    "分页清单页",
    "canonical 去重数",
    "覆盖率",
    "缺陷数",
    "涉及稿件数",
    "转载入口数",
    "原创被转载数",
    "启用",
]

COVERAGE_HEADERS = [
    "巡检号",
    "站点编码",
    "站点名称",
    "目录条目 A",
    "sitemap loc B",
    "可达 C",
    "A∩B∩C",
    "覆盖率",
    "不可达率",
    "死链数",
    "缺陷数",
    "P0",
    "P1",
    "P2",
    "涉及稿件数",
    "转载入口数",
]

DOCUMENT_HEADERS = [
    "稿件组",
    "稿件标题",
    "原稿站点",
    "原稿 canonical",
    "跨站数",
    "入口数",
    "各站真实入口",
    "判定证据",
    "首次检出巡检",
    "最近确认巡检",
]


def _evidence_text(defect: Defect) -> str:
    evidence = defect.evidence or {}
    chain = evidence.get("redirect_chain") or []
    if chain:
        return "301 链路：" + " → ".join([chain[0].get("from", "")] + [hop.get("to", "") for hop in chain])
    parts = []
    if evidence.get("http_status") is not None:
        parts.append(f"探测状态 HTTP {evidence['http_status']}")
    if evidence.get("page_canonical"):
        parts.append(f"页面 canonical={evidence['page_canonical']}")
    if evidence.get("sitemap_loc"):
        parts.append(f"sitemap loc={evidence['sitemap_loc']}")
    if evidence.get("entry_url"):
        parts.append(f"目录入口={evidence['entry_url']}")
    return "；".join(parts)


def defects_csv(db: Session, run_id: int | None = None) -> bytes:
    stmt = select(Defect)
    if run_id:
        stmt = stmt.where(Defect.run_id == run_id)
    rows = db.scalars(stmt.order_by(Defect.severity, Defect.site_code, Defect.id)).all()
    site_names = {site.id: site.name for site in db.scalars(select(Site)).all()}
    data = [
        [
            defect.id,
            defect.site_code,
            site_names.get(defect.site_id, ""),
            DEFECT_CATEGORY_LABELS.get(defect.category, defect.category),
            defect.category,
            defect.severity.upper(),
            DEFECT_SEVERITY_LABELS.get(defect.severity, defect.severity),
            defect.column_name,
            defect.canonical_url,
            (defect.evidence or {}).get("sitemap_loc", ""),
            _evidence_text(defect),
            DEFECT_STATUS_LABELS.get(defect.status, defect.status),
            defect.reviewer or "",
            defect.review_note or "",
            defect.created_at.strftime("%Y-%m-%d %H:%M") if defect.created_at else "",
        ]
        for defect in rows
    ]
    return to_csv(DEFECT_HEADERS, data)


def sites_csv(db: Session) -> bytes:
    sites = db.scalars(select(Site).order_by(Site.id)).all()
    latest = db.scalar(select(CrawlRun).order_by(CrawlRun.id.desc()).limit(1))
    metrics = {}
    if latest is not None:
        metrics = {item.get("code"): item for item in (latest.metrics or {}).get("sites", [])}
    doc_by_site = repost_service.document_stats(db)["by_site"]
    data = []
    for site in sites:
        stat = metrics.get(site.code, {})
        doc = doc_by_site.get(site.code, {})
        data.append(
            [
                site.code,
                site.name,
                site.dept,
                site.region,
                site.home_url,
                site.sitemap_url,
                stat.get("catalog_total", ""),
                stat.get("sitemap_total", ""),
                stat.get("pages", ""),
                stat.get("unique", ""),
                f"{float(stat.get('coverage') or 0):.4f}" if stat else "",
                stat.get("defects", ""),
                doc.get("documents", ""),
                doc.get("repost_in", ""),
                doc.get("origin_out", ""),
                "是" if site.enabled else "否",
            ]
        )
    return to_csv(SITE_HEADERS, data)


def coverage_csv(db: Session, run_id: int | None = None) -> bytes:
    run = db.get(CrawlRun, run_id) if run_id else db.scalar(select(CrawlRun).order_by(CrawlRun.id.desc()).limit(1))
    if run is None:
        return to_csv(COVERAGE_HEADERS, [])
    documents = (run.metrics or {}).get("documents") or {}
    doc_by_site = {item.get("code"): item for item in documents.get("by_site", [])}
    data = []
    for item in (run.metrics or {}).get("sites", []):
        catalog_total = int(item.get("catalog_total") or 0)
        hit = int(item.get("hit") or 0)
        doc = doc_by_site.get(item.get("code"), {})
        data.append(
            [
                run.run_no,
                item.get("code", ""),
                item.get("name", ""),
                catalog_total,
                int(item.get("sitemap_total") or 0),
                int(item.get("reachable") or 0),
                hit,
                f"{float(item.get('coverage') or 0):.4f}",
                f"{float(item.get('unreachable_rate') or 0):.4f}",
                int(item.get("dead") or 0),
                int(item.get("defects") or 0),
                int(item.get("p0") or 0),
                int(item.get("p1") or 0),
                int(item.get("p2") or 0),
                int(doc.get("documents") or 0),
                int(doc.get("repost_in") or 0),
            ]
        )
    # 合计行：跨站稿件数为全局去重口径；勾稽 入口合计 − 跨站稿件数 = 转载合并 = 转载入口数
    entry_total = int(documents.get("entry_total") or run.catalog_count or 0)
    document_total = int(documents.get("document_total") or entry_total)
    data.append(
        [
            run.run_no,
            "ALL",
            "全部站点（跨站稿件已去重）",
            int(run.catalog_count or 0),
            int(run.sitemap_count or 0),
            sum(int(item.get("reachable") or 0) for item in (run.metrics or {}).get("sites", [])),
            sum(int(item.get("hit") or 0) for item in (run.metrics or {}).get("sites", [])),
            f"{float(run.coverage or 0):.4f}",
            f"{float(run.unreachable_rate or 0):.4f}",
            sum(int(item.get("dead") or 0) for item in (run.metrics or {}).get("sites", [])),
            int(run.defect_count or 0),
            int(run.p0_count or 0),
            int(run.p1_count or 0),
            int(run.p2_count or 0),
            document_total,
            int(documents.get("repost_entries") or 0),
        ]
    )
    return to_csv(COVERAGE_HEADERS, data)


def documents_csv(db: Session) -> bytes:
    """跨站稿件组清单：一行一组，保留各站真实入口与判定证据，可追溯。"""
    groups = db.scalars(select(DocGroup).order_by(DocGroup.site_count.desc(), DocGroup.id)).all()
    members = db.scalars(select(DocGroupMember)).all()
    by_group: dict[int, list[DocGroupMember]] = {}
    for member in members:
        by_group.setdefault(member.group_id, []).append(member)
    run_nos = {run.id: run.run_no for run in db.scalars(select(CrawlRun)).all()}
    data = []
    for group in groups:
        entries = sorted(
            by_group.get(group.id, []), key=lambda item: (item.role != "origin", item.site_code, item.canonical_url)
        )
        data.append(
            [
                group.group_key,
                group.title,
                group.origin_site_code,
                group.origin_url,
                group.site_count,
                group.entry_count,
                "；".join(f"{entry.site_code}={entry.entry_url}（{'原稿' if entry.role == 'origin' else '转载'}）" for entry in entries),
                "；".join(
                    f"{entry.site_code} 页面 rel=canonical → {entry.evidence.get('declared_canonical')}"
                    for entry in entries
                    if entry.role == "repost" and (entry.evidence or {}).get("declared_canonical")
                ),
                run_nos.get(group.first_seen_run_id or 0, ""),
                run_nos.get(group.last_seen_run_id or 0, ""),
            ]
        )
    return to_csv(DOCUMENT_HEADERS, data)
