#!/usr/bin/env python3
"""
Weekly Tech Show — 主程序
1. 抓取三大领域资讯（RSS + 网页 + 专利）
2. 去重归档
3. 生成静态 HTML
"""

import os
import shutil
import sys
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

from scrapers import CMFScraper, PVDScraper, OpticalScraper
from scrapers.base import (
    load_articles,
    save_articles,
    load_update_log,
    save_update_log,
    now_cst,
)

BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
CONFIG_FILE = BASE_DIR / "config.yaml"

CATEGORY_LABELS = {
    "tech": "技术前沿",
    "patent": "专利信息",
    "industry": "行业动态",
}


def load_config() -> dict:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def init_jinja() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
    )


def classify_articles(articles: list[dict]) -> dict:
    """将文章按三分类拆分，确保每栏至少有一条"""
    result = {"tech": [], "patent": [], "industry": []}
    for art in articles:
        art_copy = dict(art)  # 浅拷贝，避免修改原数据
        cat = art.get("category", "industry")
        if cat not in result:
            cat = "industry"
        result[cat].append(art_copy)

    for cat in result:
        result[cat].sort(key=lambda x: x.get("published_at", ""), reverse=True)

    # 平衡：空栏从仍有多条的栏匀过来（不全搬空）
    if len(articles) >= 2:
        for target in ["patent", "tech", "industry"]:
            if len(result[target]) > 0:
                continue
            candidates = {k: len(v) for k, v in result.items() if k != target and len(v) > 1}
            if not candidates:
                continue
            richest = max(candidates, key=candidates.get)
            move_count = max(1, len(result[richest]) // 3)
            moved = result[richest][-move_count:]
            result[richest] = result[richest][:-move_count]
            for a in moved:
                a["category"] = target
            result[target] = moved

    return result


def generate_html(env: Environment, config: dict):
    """生成所有静态 HTML 页面"""
    articles = load_articles()
    logs = load_update_log()
    last_update = logs[0]["date"] if logs else now_cst()
    update_status = "success" if logs else "idle"

    domains_config = config.get("domains", {})
    domains_meta = []
    for slug, dc in domains_config.items():
        if not dc.get("enabled", True):
            continue
        domain_articles = [a for a in articles if a.get("domain") == slug]
        domains_meta.append({
            "slug": slug,
            "name": dc.get("name", slug),
            "count": len(domain_articles),
            "latest": domain_articles[:6],
        })

    # 公共模板变量
    base_ctx = {
        "domains": domains_meta,
        "last_update": last_update,
        "update_status": update_status,
        "total_count": len(articles),
    }

    # --- 首页 ---
    index_tpl = env.get_template("index.html")
    index_ctx = {**base_ctx, "active_domain": "index", "base_path": "."}
    index_html = index_tpl.render(**index_ctx)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "index.html").write_text(index_html, encoding="utf-8")

    # --- 领域页面 ---
    domain_tpl = env.get_template("domain.html")
    for dm in domains_meta:
        domain_articles = [a for a in articles if a.get("domain") == dm["slug"]]
        classified = classify_articles(domain_articles)

        ctx = {
            **base_ctx,
            "active_domain": dm["slug"],
            "domain_name": dm["name"],
            "articles": domain_articles,
            "tech_articles": classified["tech"],
            "patent_articles": classified["patent"],
            "industry_articles": classified["industry"],
            "base_path": "..",
        }
        domain_output_dir = OUTPUT_DIR / dm["slug"]
        domain_output_dir.mkdir(parents=True, exist_ok=True)
        html = domain_tpl.render(**ctx)
        (domain_output_dir / "index.html").write_text(html, encoding="utf-8")

    # --- 复制静态资源 ---
    output_static = OUTPUT_DIR / "static"
    if output_static.exists():
        shutil.rmtree(output_static)
    shutil.copytree(STATIC_DIR, output_static)

    # --- 复制 data JSON 到 output（供可选的前端直接加载） ---
    output_data = OUTPUT_DIR / "data"
    output_data.mkdir(parents=True, exist_ok=True)
    if (DATA_DIR / "articles.json").exists():
        shutil.copy(DATA_DIR / "articles.json", output_data / "articles.json")
    if (DATA_DIR / "update_log.json").exists():
        shutil.copy(DATA_DIR / "update_log.json", output_data / "update_log.json")

    print(f"\n  HTML 生成完成 → {OUTPUT_DIR}/")


def run_scrapers(config: dict) -> int:
    """执行所有领域的抓取，返回新增资讯数"""
    domains_config = config.get("domains", {})
    total_new = 0

    scrapers = {
        "cmf": CMFScraper,
        "pvd": PVDScraper,
        "optical": OpticalScraper,
    }

    for slug, scraper_cls in scrapers.items():
        dc = domains_config.get(slug)
        if not dc or not dc.get("enabled", True):
            print(f"  跳过 (已禁用): {slug}")
            continue

        scraper = scraper_cls(dc)
        scraper.run()
        added = scraper.deduplicate_and_save()
        total_new += added

    return total_new


def main():
    print("=" * 50)
    print("  Weekly Tech Show — 资讯更新")
    print(f"  执行时间: {now_cst()}")
    print("=" * 50)

    config = load_config()
    env = init_jinja()

    # Step 1: 抓取
    new_count = run_scrapers(config)

    # Step 2: 生成 HTML
    generate_html(env, config)

    # Step 3: 输出摘要
    articles = load_articles()
    logs = load_update_log()
    print(f"\n{'='*50}")
    print(f"  更新完成")
    print(f"  本次新增: {new_count} 条")
    print(f"  数据库总计: {len(articles)} 条")
    print(f"  输出目录: {OUTPUT_DIR}")
    print(f"{'='*50}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
