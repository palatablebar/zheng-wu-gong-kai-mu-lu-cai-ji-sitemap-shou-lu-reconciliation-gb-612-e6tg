"""算法 C 落库幂等：SQLite 内存库验证 sync 重跑不重复建组、不重复记账、可收敛。"""
from __future__ import annotations

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from src.models import Base
from src.models.catalog_item import CatalogItem
from src.models.crawl_run import CrawlRun
from src.models.doc_group import DocGroup, DocGroupMember
from src.models.site import Site
from src.services import repost_service

ZWGK = "https://zwgk.hezhou.gov.cn"
ZWFW = "https://zwfw.hezhou.gov.cn"


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)()


def _seed_catalog(db: Session) -> CrawlRun:
    db.add(Site(code="zwgk-hezhou", name="主站", home_url=ZWGK))
    db.add(Site(code="zwfw-hezhou", name="服务网", home_url=ZWFW))
    db.flush()
    sites = {site.code: site for site in db.scalars(select(Site)).all()}
    db.add_all(
        [
            CatalogItem(
                site_id=sites["zwgk-hezhou"].id,
                column_code="zcwj",
                column_name="政策文件",
                entry_url=f"{ZWGK}/xxgk/zcwj/zcwj-021.html",
                canonical_url=f"{ZWGK}/xxgk/zcwj/zcwj-021.html",
                title="联合发文",
            ),
            CatalogItem(
                site_id=sites["zwfw-hezhou"].id,
                column_code="zcwj",
                column_name="政策文件",
                entry_url=f"{ZWFW}/xxgk/zcwj/zcwj-021.html",
                canonical_url=f"{ZWFW}/xxgk/zcwj/zcwj-021.html",
                page_canonical=f"{ZWGK}/xxgk/zcwj/zcwj-021.html",
                title="联合发文",
            ),
            CatalogItem(
                site_id=sites["zwfw-hezhou"].id,
                column_code="zcwj",
                column_name="政策文件",
                entry_url=f"{ZWFW}/xxgk/zcwj/zcwj-001.html",
                canonical_url=f"{ZWFW}/xxgk/zcwj/zcwj-001.html",
                title="同名独立文件",
            ),
        ]
    )
    run = CrawlRun(run_no="C20261115-0001", trigger="seed", status="success")
    db.add(run)
    db.flush()
    return run


def test_sync_is_idempotent_across_reruns():
    db = _session()
    run = _seed_catalog(db)

    first = repost_service.rebuild_doc_groups(db, run=run)
    assert first["summary"]["document_total"] == 2  # 转载组 1 + 同名独立组 1
    assert first["summary"]["entry_total"] == 3
    assert first["summary"]["merged_entries"] == 1
    assert first["sync"]["created"] == 2
    groups_after_first = db.scalar(select(func.count()).select_from(DocGroup))
    members_after_first = db.scalar(select(func.count()).select_from(DocGroupMember))

    # 重跑（新 run）：不重复生成关系、不重复记账
    rerun = CrawlRun(run_no="C20261115-0002", trigger="manual", status="success")
    db.add(rerun)
    db.flush()
    second = repost_service.rebuild_doc_groups(db, run=rerun)
    assert second["sync"]["created"] == 0
    assert second["sync"]["reused"] == 2
    assert second["sync"]["dropped_members"] == 0
    assert db.scalar(select(func.count()).select_from(DocGroup)) == groups_after_first
    assert db.scalar(select(func.count()).select_from(DocGroupMember)) == members_after_first

    # last_seen 跟随新 run，first_seen 保持首次巡检（可追溯）
    group = db.scalar(select(DocGroup).where(DocGroup.site_count > 1))
    assert group.first_seen_run_id == run.id
    assert group.last_seen_run_id == rerun.id
    db.close()


def test_sync_converges_when_repost_removed():
    db = _session()
    run = _seed_catalog(db)
    repost_service.rebuild_doc_groups(db, run=run)
    assert db.scalar(select(func.count()).select_from(DocGroup)) == 2

    # 转载入口下线：成员收敛删除，组随之调整；独立文件入口不受影响
    orphan = db.scalar(select(CatalogItem).where(CatalogItem.page_canonical.is_not(None)))
    db.delete(orphan)
    db.flush()
    rerun = CrawlRun(run_no="C20261115-0003", trigger="manual", status="success")
    db.add(rerun)
    db.flush()
    result = repost_service.rebuild_doc_groups(db, run=rerun)
    assert result["sync"]["dropped_members"] == 1
    assert result["summary"]["document_total"] == 2  # 两条独立稿件
    assert result["summary"]["entry_total"] == 2
    assert db.scalar(select(func.count()).select_from(DocGroupMember)) == 2
    titles = {group.title for group in db.scalars(select(DocGroup)).all()}
    assert "同名独立文件" in titles
    db.close()
