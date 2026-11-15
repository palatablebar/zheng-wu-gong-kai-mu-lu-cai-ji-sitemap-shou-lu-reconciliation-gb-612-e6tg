-- 算法 C｜跨站转载稿件组（人类可读 DDL 参考，运行时不执行；建表由 SQLAlchemy create_all 完成）
--
-- 口径：
-- - doc_groups.group_key = doc-<sha1(原稿 canonical)[:16]>，自然键唯一，重跑巡检幂等复用；
-- - doc_group_members.catalog_item_id 唯一：一条目录入口只属于一个稿件组，不重复记账；
-- - 成员保留各站真实入口（entry_url）与判定证据（evidence.declared_canonical），
--   缺陷整改仍按入口维度流转，主站整改不掩盖其他站漏收。

ALTER TABLE catalog_items
    ADD COLUMN page_canonical VARCHAR(300) NULL COMMENT '详情页 rel=canonical 声明值（normalize 后）；跨站=转载证据';

CREATE TABLE doc_groups (
    id                 INT AUTO_INCREMENT PRIMARY KEY,
    group_key          VARCHAR(40)  NOT NULL UNIQUE,
    origin_url         VARCHAR(300) NOT NULL DEFAULT '',
    origin_site_code   VARCHAR(40)  NOT NULL DEFAULT '',
    title              VARCHAR(200) NOT NULL DEFAULT '',
    entry_count        INT NOT NULL DEFAULT 1,
    site_count         INT NOT NULL DEFAULT 1,
    first_seen_run_id  INT NULL,
    last_seen_run_id   INT NULL,
    created_at         DATETIME NOT NULL,
    updated_at         DATETIME NOT NULL,
    INDEX ix_doc_groups_origin_site (origin_site_code),
    CONSTRAINT fk_doc_groups_first_run FOREIGN KEY (first_seen_run_id) REFERENCES crawl_runs(id) ON DELETE SET NULL,
    CONSTRAINT fk_doc_groups_last_run  FOREIGN KEY (last_seen_run_id)  REFERENCES crawl_runs(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE doc_group_members (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    group_id         INT NOT NULL,
    catalog_item_id  INT NOT NULL,
    site_id          INT NOT NULL,
    site_code        VARCHAR(40)  NOT NULL DEFAULT '',
    entry_url        VARCHAR(300) NOT NULL DEFAULT '',
    canonical_url    VARCHAR(300) NOT NULL DEFAULT '',
    role             VARCHAR(12)  NOT NULL DEFAULT 'origin',   -- origin / repost
    via              VARCHAR(20)  NOT NULL DEFAULT 'self',     -- self / canonical_tag
    evidence         JSON,
    created_at       DATETIME NOT NULL,
    CONSTRAINT uq_doc_member_catalog_item UNIQUE (catalog_item_id),
    INDEX ix_doc_member_group (group_id),
    INDEX ix_doc_member_site (site_id),
    INDEX ix_doc_member_canonical (canonical_url),
    CONSTRAINT fk_doc_member_group FOREIGN KEY (group_id) REFERENCES doc_groups(id) ON DELETE CASCADE,
    CONSTRAINT fk_doc_member_item  FOREIGN KEY (catalog_item_id) REFERENCES catalog_items(id) ON DELETE CASCADE,
    CONSTRAINT fk_doc_member_site  FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
