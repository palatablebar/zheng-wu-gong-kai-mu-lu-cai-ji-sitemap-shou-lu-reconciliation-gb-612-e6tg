"""算法 C｜跨站转载归组单测：证据归组、同名不合并、dangling、确定性、对平。"""
from __future__ import annotations

from src.services import repost_engine

ZWGK = "https://zwgk.hezhou.gov.cn"
ZWFW = "https://zwfw.hezhou.gov.cn"
QUXIAN = "https://quxian.hezhou.gov.cn"

_next_id = 0


def entry(site: str, path: str, *, declared: str | None = None, title: str = "") -> dict:
    global _next_id
    _next_id += 1
    home = {"zwgk": ZWGK, "zwfw": ZWFW, "quxian": QUXIAN}[site]
    return {
        "id": _next_id,
        "site_id": {"zwgk": 1, "zwfw": 2, "quxian": 3}[site],
        "site_code": {"zwgk": "zwgk-hezhou", "zwfw": "zwfw-hezhou", "quxian": "quxian-hezhou"}[site],
        "entry_url": f"{home}{path}",
        "canonical_url": f"{home}{path}",
        "page_canonical": declared,
        "title": title or path,
        "column_name": "政策文件",
    }


def groups_of(result):
    return {group["group_key"]: group for group in result["groups"]}


def test_cross_site_canonical_forms_one_group():
    """zwfw 转载页 canonical 指向 zwgk 原稿 → 两入口并成一组，原稿是 zwgk。"""
    result = repost_engine.build_doc_groups(
        [
            entry("zwgk", "/xxgk/zcwj/zcwj-021.html", title="联合发文 A"),
            entry("zwfw", "/xxgk/zcwj/zcwj-021.html", declared=f"{ZWGK}/xxgk/zcwj/zcwj-021.html"),
        ]
    )
    assert result["stats"]["entry_total"] == 2
    assert result["stats"]["document_total"] == 1
    assert result["stats"]["merged_entries"] == 1
    assert result["stats"]["multi_site_groups"] == 1
    group = result["groups"][0]
    assert group["origin_url"] == f"{ZWGK}/xxgk/zcwj/zcwj-021.html"
    assert group["origin_site_code"] == "zwgk-hezhou"
    roles = {member["site_code"]: member["role"] for member in group["members"]}
    assert roles == {"zwgk-hezhou": "origin", "zwfw-hezhou": "repost"}
    repost = [member for member in group["members"] if member["role"] == "repost"][0]
    assert repost["via"] == "canonical_tag"
    assert repost["declared_canonical"] == f"{ZWGK}/xxgk/zcwj/zcwj-021.html"


def test_three_site_joint_repost():
    """三站联合转载：两个外站都指向同一原稿 → 一组三入口。"""
    result = repost_engine.build_doc_groups(
        [
            entry("zwgk", "/xxgk/zcwj/zcwj-027.html"),
            entry("zwfw", "/xxgk/zcwj/zcwj-027.html", declared=f"{ZWGK}/xxgk/zcwj/zcwj-027.html"),
            entry("quxian", "/xxgk/zcwj/zcwj-027.html", declared=f"{ZWGK}/xxgk/zcwj/zcwj-027.html"),
        ]
    )
    assert result["stats"]["document_total"] == 1
    group = result["groups"][0]
    assert group["site_count"] == 3
    assert group["entry_count"] == 3
    assert result["stats"]["repost_entries"] == 2


def test_same_slug_without_evidence_not_merged():
    """同名文件（同 slug 跨站）没有 canonical 证据 → 各自成组，入口证据不丢。"""
    result = repost_engine.build_doc_groups(
        [
            entry("zwgk", "/xxgk/zcwj/zcwj-001.html", title="政务公开管理办法"),
            entry("zwfw", "/xxgk/zcwj/zcwj-001.html", title="政务公开管理办法"),
            entry("quxian", "/xxgk/zcwj/zcwj-001.html", title="政务公开管理办法"),
        ]
    )
    assert result["stats"]["document_total"] == 3
    assert result["stats"]["multi_site_groups"] == 0
    for group in result["groups"]:
        assert group["entry_count"] == 1
        assert group["members"][0]["role"] == "origin"


def test_dangling_declared_canonical_traced_not_grouped():
    """指向外站但未登记的 URL → dangling 留痕，不成组；原稿登记后下次归并。"""
    result = repost_engine.build_doc_groups(
        [
            entry("zwfw", "/xxgk/zcwj/zcwj-099.html", declared=f"{ZWGK}/xxgk/zcwj/zcwj-099.html"),
        ]
    )
    assert result["stats"]["dangling"] == 1
    assert result["dangling"][0]["declared_canonical"] == f"{ZWGK}/xxgk/zcwj/zcwj-099.html"
    #  dangling 入口仍是独立稿件（单入口组），入口数与稿件数对平
    assert result["stats"]["document_total"] == 1
    assert result["stats"]["entry_total"] == 1


def test_same_site_canonical_not_a_repost_edge():
    """同站 canonical（站内口径问题）不建转载边，由算法 B 判缺陷。"""
    result = repost_engine.build_doc_groups(
        [
            entry("zwfw", "/xxgk/zdxm/zdxm-004.html", declared=f"{ZWFW}/xxgk/zdxm/"),
        ]
    )
    assert result["stats"]["repost_entries"] == 0
    assert result["stats"]["dangling"] == 0
    assert result["stats"]["document_total"] == 1


def test_group_key_stable_and_deterministic():
    """group_key 由原稿 canonical 决定；两次调用结果完全一致（确定性）。"""
    entries = [
        entry("zwgk", "/xxgk/zcwj/zcwj-021.html"),
        entry("zwfw", "/xxgk/zcwj/zcwj-021.html", declared=f"{ZWGK}/xxgk/zcwj/zcwj-021.html"),
    ]
    first = repost_engine.build_doc_groups(entries)
    second = repost_engine.build_doc_groups(list(reversed(entries)))
    assert first["groups"] == second["groups"]
    assert first["groups"][0]["group_key"] == repost_engine.group_key_for(f"{ZWGK}/xxgk/zcwj/zcwj-021.html")


def test_balance_equation_holds():
    """对平：跨站稿件数 = 入口合计 − Σ(组入口数 − 1)。"""
    entries = [
        entry("zwgk", "/a.html"),
        entry("zwfw", "/a.html", declared=f"{ZWGK}/a.html"),
        entry("quxian", "/a.html", declared=f"{ZWGK}/a.html"),
        entry("zwgk", "/b.html"),
        entry("zwfw", "/b.html", declared=f"{ZWGK}/b.html"),
        entry("zwgk", "/c.html"),
    ]
    result = repost_engine.build_doc_groups(entries)
    stats = result["stats"]
    assert stats["entry_total"] == 6
    assert stats["document_total"] == 3
    assert stats["merged_entries"] == stats["entry_total"] - stats["document_total"] == 3
    by_site = {item["code"]: item for item in result["by_site"]}
    assert by_site["zwgk-hezhou"]["entries"] == 3
    assert by_site["zwgk-hezhou"]["origin_out"] == 2
    assert by_site["zwfw-hezhou"]["repost_in"] == 2
    assert by_site["quxian-hezhou"]["repost_in"] == 1
    assert sum(item["entries"] for item in result["by_site"]) == stats["entry_total"]
