"""算法 C｜跨站转载归组（纯函数、确定性，两次调用结果完全一致）。

证据口径（可追溯、不误并）：
- 只认「详情页 rel=canonical 指向**外站已登记**的目录入口」这一条证据
  （fixture 转载页同时写 <meta name="repost-source">，二者一致）；
- **同名文件不合并**：slug / 标题相同但没有 canonical 证据的入口各自成组，
  各站真实入口全部保留，不丢独立文件的入口证据；
- 指向外站但未登记的 URL 记 `dangling`（可追溯，该入口暂按独立稿件成组；
  原稿登记后下次巡检自动并入原稿所在组）。

分组：有证据边相连的入口并成一组；组内无外向转载边的入口为原稿（origin），
其余为转载入口（repost）。每个目录入口恰好属于一个组（单入口组 = 独立稿件）。

对平口径：跨站稿件数 document_total = 组数；入口合计 entry_total = 目录条目数；
转载合并 merged = entry_total − document_total = Σ(组入口数 − 1)。
"""
from __future__ import annotations

import hashlib

from src.services.url_normalize import normalize_url, same_site

ROLE_ORIGIN = "origin"
ROLE_REPOST = "repost"
VIA_SELF = "self"
VIA_CANONICAL_TAG = "canonical_tag"


def group_key_for(origin_canonical: str) -> str:
    """稿件组自然键：由原稿 canonical 决定，重跑 / 换 run 都稳定。"""
    digest = hashlib.sha1(origin_canonical.encode("utf-8")).hexdigest()[:16]
    return f"doc-{digest}"


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, item: int) -> int:
        self.parent.setdefault(item, item)
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != root:
            self.parent[item], item = root, self.parent[item]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def build_doc_groups(entries: list[dict]) -> dict:
    """输入全量目录条目，输出稿件组 + 统计 + dangling 证据。

    entries 元素：{id, site_id, site_code, entry_url, canonical_url, page_canonical, title, column_name}
    """
    by_canonical: dict[str, dict] = {}
    for entry in entries:
        canonical = normalize_url(entry.get("canonical_url") or entry.get("entry_url"))
        if canonical:
            by_canonical[canonical] = entry

    uf = _UnionFind()
    outbound: dict[int, str] = {}  # catalog_item_id -> 声明的跨站 canonical（normalize 后）
    dangling: list[dict] = []

    for entry in entries:
        declared = normalize_url(entry.get("page_canonical"))
        if not declared:
            continue
        own = normalize_url(entry.get("canonical_url") or entry.get("entry_url"))
        if declared == own or same_site(declared, own):
            continue  # 自指或同站口径问题（同站 mismatch 由算法 B 判缺陷），不建转载边
        target = by_canonical.get(declared)
        item_id = int(entry["id"])
        if target is None:
            dangling.append(
                {
                    "catalog_item_id": item_id,
                    "site_code": entry.get("site_code") or "",
                    "entry_url": entry.get("entry_url") or "",
                    "declared_canonical": declared,
                }
            )
            continue
        outbound[item_id] = declared
        uf.union(item_id, int(target["id"]))

    buckets: dict[int, list[dict]] = {}
    for entry in entries:
        item_id = int(entry["id"])
        buckets.setdefault(uf.find(item_id), []).append(entry)

    groups: list[dict] = []
    for root in sorted(buckets):
        members_raw = sorted(
            buckets[root],
            key=lambda item: (normalize_url(item.get("canonical_url") or item.get("entry_url")), int(item["id"])),
        )
        # 原稿 = 组内无跨站转载边的入口；异常多份时取 canonical 字典序最小者（确定性）
        origins = [item for item in members_raw if int(item["id"]) not in outbound]
        origin = origins[0] if origins else members_raw[0]
        origin_canonical = normalize_url(origin.get("canonical_url") or origin.get("entry_url"))
        members = [
            {
                "catalog_item_id": int(item["id"]),
                "site_id": int(item.get("site_id") or 0),
                "site_code": item.get("site_code") or "",
                "entry_url": item.get("entry_url") or "",
                "canonical_url": normalize_url(item.get("canonical_url") or item.get("entry_url")),
                "role": ROLE_ORIGIN if int(item["id"]) == int(origin["id"]) else ROLE_REPOST,
                "via": VIA_SELF if int(item["id"]) == int(origin["id"]) else VIA_CANONICAL_TAG,
                "declared_canonical": outbound.get(int(item["id"]), ""),
            }
            for item in members_raw
        ]
        members.sort(key=lambda item: (item["role"] != ROLE_ORIGIN, item["site_code"], item["canonical_url"]))
        site_codes = sorted({item["site_code"] for item in members})
        groups.append(
            {
                "group_key": group_key_for(origin_canonical),
                "origin_url": origin_canonical,
                "origin_site_code": origin.get("site_code") or "",
                "title": origin.get("title") or "",
                "entry_count": len(members),
                "site_count": len(site_codes),
                "members": members,
            }
        )
    groups.sort(key=lambda item: item["group_key"])

    entry_total = len(entries)
    document_total = len(groups)
    repost_entries = sum(1 for group in groups for member in group["members"] if member["role"] == ROLE_REPOST)
    multi_site_groups = sum(1 for group in groups if group["site_count"] > 1)

    by_site: dict[str, dict] = {}
    for entry in entries:
        code = entry.get("site_code") or ""
        by_site.setdefault(code, {"code": code, "entries": 0, "documents": 0, "repost_in": 0, "origin_out": 0})
        by_site[code]["entries"] += 1
    for group in groups:
        codes = {member["site_code"] for member in group["members"]}
        for code in codes:
            by_site.setdefault(code, {"code": code, "entries": 0, "documents": 0, "repost_in": 0, "origin_out": 0})
            by_site[code]["documents"] += 1
        for member in group["members"]:
            if member["role"] == ROLE_REPOST:
                by_site[member["site_code"]]["repost_in"] += 1
        if group["site_count"] > 1:
            by_site[group["origin_site_code"]]["origin_out"] += 1

    return {
        "groups": groups,
        "dangling": sorted(dangling, key=lambda item: (item["site_code"], item["entry_url"])),
        "stats": {
            "entry_total": entry_total,
            "document_total": document_total,
            "merged_entries": entry_total - document_total,
            "repost_entries": repost_entries,
            "multi_site_groups": multi_site_groups,
            "dangling": len(dangling),
        },
        "by_site": [by_site[code] for code in sorted(by_site)],
    }
