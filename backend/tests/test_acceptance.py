"""端到端验收：用 demo_config 的真实口径离线跑「fixture → 算法 A → 算法 B」，核对 seed 数量。

不依赖数据库、不依赖网络：只在 tmp_path 里写静态站，跑的就是容器里跑的那套代码。
"""
from __future__ import annotations

from src.constants.demo_data import PAGE_SIZE, SITES
from src.services import crawl_engine, fixture_site, reconcile_engine, repost_engine
from src.services.url_normalize import normalize_url


def _run_site(spec, base):
    root = fixture_site.fixture_root(spec["code"], base=base)
    fixture_site.build_site(spec, root)
    crawl = crawl_engine.crawl_site(
        home_url=spec["home_url"], fixture_root=root, page_size=PAGE_SIZE, max_depth=3
    )
    catalog_rows = fixture_site.catalog_rows(spec)
    sitemap_rows = fixture_site.sitemap_rows(spec)
    probes = dict(crawl["probes"])
    extra = [row["canonical_url"] for row in sitemap_rows if normalize_url(row["canonical_url"]) not in probes]
    if extra:
        probes.update(crawl_engine.probe_urls(root, extra))
    result = reconcile_engine.reconcile(
        site_code=spec["code"],
        catalog_items=catalog_rows,
        sitemap_locs=sitemap_rows,
        probes=probes,
        home_url=spec["home_url"],
    )
    return crawl, result


def test_seed_shape_matches_plan(tmp_path):
    catalog_total = sitemap_total = defects_total = 0
    hit_total = 0
    p0 = p1 = p2 = 0
    pages_total = 0
    for spec in SITES:
        crawl, result = _run_site(spec, tmp_path)
        catalog_total += result["stats"]["catalog_total"]
        sitemap_total += result["stats"]["sitemap_total"]
        hit_total += result["stats"]["hit"]
        defects_total += len(result["defects"])
        p0 += result["stats"]["p0"]
        p1 += result["stats"]["p1"]
        p2 += result["stats"]["p2"]
        pages_total += crawl["stats"]["pages"]

    assert catalog_total == 360           # 3 站 × 120 条目录
    assert sitemap_total == 362           # 360 - 12 漏收 + 14 死链
    assert pages_total == 36              # 3 站 × 12 个分页清单页
    assert defects_total == 31            # batch-plan 验收值
    assert (p0, p1, p2) == (14, 12, 5)    # 14 死链全部 P0
    assert round(hit_total / catalog_total, 4) == 0.9667


def test_site_one_page_three_acceptance(tmp_path):
    """某站第 3 页有 12 条链接、sitemap 缺其中 5 条 → 5 条 only_in_catalog。"""
    spec = SITES[0]
    crawl, result = _run_site(spec, tmp_path)
    page_three = [page for page in crawl["pages"] if page["url"].endswith("/xxgk/zcwj/index_3.html")]
    assert len(page_three) == 1
    assert page_three[0]["links_in_page"] == 12

    missing = [item for item in result["defects"] if item["category"] == "only_in_catalog"]
    assert len(missing) == 5
    assert all(item["evidence"]["page_no"] == 3 for item in missing)
    assert all(item["evidence"]["column"] == "政策文件" for item in missing)


def test_dead_links_all_p0(tmp_path):
    dead_total = 0
    for spec in SITES:
        _crawl, result = _run_site(spec, tmp_path)
        dead = [item for item in result["defects"] if item["category"] == "only_in_sitemap_dead"]
        assert all(item["severity"] == "p0" for item in dead)
        dead_total += len(dead)
    assert dead_total == 14


def test_fixture_build_is_deterministic(tmp_path):
    spec = SITES[0]
    root_a = fixture_site.fixture_root(spec["code"] + "-a", base=tmp_path)
    root_b = fixture_site.fixture_root(spec["code"] + "-b", base=tmp_path)
    fixture_site.build_site(spec, root_a)
    fixture_site.build_site(spec, root_b)
    files_a = sorted(path.relative_to(root_a) for path in root_a.rglob("*") if path.is_file())
    files_b = sorted(path.relative_to(root_b) for path in root_b.rglob("*") if path.is_file())
    assert files_a == files_b
    sample = "xxgk/zcwj/index_3.html"
    assert (root_a / sample).read_bytes() == (root_b / sample).read_bytes()


def _doc_entries() -> list[dict]:
    """全量目录条目（含页面 canonical 证据），模拟 execute_run 落库后的全量重算输入。"""
    entries: list[dict] = []
    next_id = 0
    for site_id, spec in enumerate(SITES, start=1):
        for row in fixture_site.catalog_rows(spec):
            next_id += 1
            entries.append({**row, "id": next_id, "site_id": site_id, "site_code": spec["code"]})
    return entries


def test_repost_groups_acceptance():
    """算法 C 验收：7 个跨站组、8 条转载入口；跨站稿件 352 = 入口 360 − 合并 8。"""
    result = repost_engine.build_doc_groups(_doc_entries())
    stats = result["stats"]
    assert stats["entry_total"] == 360            # 各站真实入口逐站保留
    assert stats["document_total"] == 352         # 跨站稿件数（全局去重）
    assert stats["merged_entries"] == 8           # 360 − 352，对平
    assert stats["repost_entries"] == 8
    assert stats["multi_site_groups"] == 7        # zwgk 原创 7 份被转载
    assert stats["dangling"] == 0

    by_site = {item["code"]: item for item in result["by_site"]}
    assert by_site["zwgk-hezhou"]["entries"] == 120
    assert by_site["zwgk-hezhou"]["origin_out"] == 7
    assert by_site["zwgk-hezhou"]["repost_in"] == 0
    assert by_site["zwfw-hezhou"]["repost_in"] == 4
    assert by_site["quxian-hezhou"]["repost_in"] == 4
    assert sum(item["entries"] for item in result["by_site"]) == 360

    # 三站联合转载 zcwj-027：一组三入口，原稿在主站
    joint = [group for group in result["groups"] if group["site_count"] == 3]
    assert len(joint) == 1
    assert joint[0]["origin_site_code"] == "zwgk-hezhou"
    assert joint[0]["origin_url"].endswith("/xxgk/zcwj/zcwj-027.html")

    # 同名文件不合并：zcwj-001 三站同 slug 但无 canonical 证据 → 3 个独立单入口组
    slug001 = [group for group in result["groups"] if group["origin_url"].endswith("/xxgk/zcwj/zcwj-001.html")]
    assert len(slug001) == 3
    assert all(group["entry_count"] == 1 for group in slug001)


def test_repost_evidence_written_into_fixture_html(tmp_path):
    """转载页的 rel=canonical 真的指向主站原稿（证据可追溯，探测能取到）。"""
    spec = next(item for item in SITES if item["code"] == "zwfw-hezhou")
    root = fixture_root = fixture_site.fixture_root(spec["code"], base=tmp_path)
    fixture_site.build_site(spec, root)
    crawl = crawl_engine.crawl_site(home_url=spec["home_url"], fixture_root=fixture_root, page_size=PAGE_SIZE)
    probe = crawl["probes"]["https://zwfw.hezhou.gov.cn/xxgk/zcwj/zcwj-021.html"]
    assert probe["status"] == 200
    assert probe["canonical_tag"] == "https://zwgk.hezhou.gov.cn/xxgk/zcwj/zcwj-021.html"

    # 跨站 canonical 不报 canonical_mismatch（缺陷口径 31 不变），转为转载信号
    _crawl, result = _run_site(spec, tmp_path)
    mismatch = [item for item in result["defects"] if item["category"] == "canonical_mismatch"]
    assert len(mismatch) == 2  # 只剩同站口径问题（zdxm-004 / yjgl-007）
    assert len(result["sets"]["repost_signals"]) == 4
