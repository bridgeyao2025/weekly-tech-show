"""
基础抓取器：RSS 阅读、网页解析、专利搜索、数据管理
"""
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

# 北京时间
CST = timezone(timedelta(hours=8))

DATA_DIR = Path(__file__).parent.parent / "data"
ARTICLES_FILE = DATA_DIR / "articles.json"
UPDATE_LOG_FILE = DATA_DIR / "update_log.json"


def load_articles() -> list[dict]:
    """加载已有资讯"""
    if ARTICLES_FILE.exists():
        with open(ARTICLES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_articles(articles: list[dict]):
    """保存资讯到 JSON"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(ARTICLES_FILE, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)


def load_update_log() -> list[dict]:
    """加载更新日志"""
    if UPDATE_LOG_FILE.exists():
        with open(UPDATE_LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_update_log(logs: list[dict]):
    """保存更新日志"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(UPDATE_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)


def article_id(url: str) -> str:
    """基于 URL 生成唯一 ID"""
    return hashlib.md5(url.encode()).hexdigest()[:12]


def url_exists(articles: list[dict], url: str) -> bool:
    """检查 URL 是否已存在"""
    aid = article_id(url)
    return any(article_id(a["url"]) == aid for a in articles)


def now_cst() -> str:
    """返回北京时间 ISO 字符串"""
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")


def today_cst() -> str:
    """返回北京时间日期"""
    return datetime.now(CST).strftime("%Y-%m-%d")


class BaseScraper:
    """基础抓取器，封装通用抓取逻辑"""

    def __init__(self, domain_slug: str, config: dict):
        self.domain_slug = domain_slug
        self.config = config
        self.keywords = config.get("keywords", [])
        self.rss_sources = config.get("rss_sources", [])
        self.web_sources = config.get("web_sources", [])
        self.patent_queries = config.get("patent_queries", [])
        self.summary_max_len = 200
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.bilibili.com/",
        })
        # B站 API 需要简单 cookie
        self.session.cookies.set("buvid3", "weekly-tech-show", domain=".bilibili.com")
        self.new_articles: list[dict] = []

    # 国内无法访问的域名黑名单，链接包含这些的自动跳过
    BLOCKED_URL_PATTERNS = [
        "news.google.com",
        "patents.google.com",
        "google.com/patents",
        "scholar.google.com",
    ]

    def _is_url_blocked(self, url: str) -> bool:
        """检查 URL 是否来自被屏蔽的境外服务"""
        return any(p in url for p in self.BLOCKED_URL_PATTERNS)

    # ========== 正文提取 & 摘要生成 ==========

    # 常见正文容器的 CSS 选择器（按优先级）
    CONTENT_SELECTORS = [
        "article", ".article-content", ".article-body", ".post-content",
        ".content", ".entry-content", "#article", "#content",
        ".news-content", ".detail-content", ".article-detail",
        ".main-content", "#main-content", ".post-body",
        ".rich_media_content", "#js_content",  # 微信公众号
    ]

    # 非正文元素（导航、广告、侧栏等）
    NOISE_SELECTORS = [
        "nav", "header", "footer", ".sidebar", ".aside", ".ad",
        ".advertisement", ".related", ".recommend", ".comment",
        ".share", ".nav", ".menu", ".breadcrumb", "script", "style",
        ".copyright", ".disclaimer",
    ]

    def _fetch_article_text(self, url: str) -> str:
        """抓取文章正文，去除导航/广告等干扰内容"""
        try:
            resp = self._fetch_url(url, timeout=10)
            if resp is None or resp.status_code != 200:
                return ""
            soup = BeautifulSoup(resp.text, "lxml")

            # 去除干扰元素
            for sel in self.NOISE_SELECTORS:
                for el in soup.select(sel):
                    el.decompose()

            # 按优先级查找正文容器
            for sel in self.CONTENT_SELECTORS:
                container = soup.select_one(sel)
                if container:
                    text = container.get_text(separator="\n", strip=True)
                    if len(text) > 100:
                        return text

            # 兜底策略：收集所有 <p> 标签文本，找中文最多的区域
            paragraphs = []
            for p in soup.select("p, .text, .desc, span.txt"):
                text = p.get_text(strip=True)
                cn = sum(1 for c in text if '一' <= c <= '鿿')
                if cn > 10 and len(text) < 2000:
                    paragraphs.append(text)

            if paragraphs:
                combined = "\n".join(paragraphs[:20])
                if len(combined) > 100:
                    return combined

            # 最后兜底：找中文密度最高的 div
            best_div, best_score = "", 0
            for div in soup.select("div"):
                text = div.get_text(strip=True)
                cn = sum(1 for c in text if '一' <= c <= '鿿')
                # 要求中文占比 > 40% 才认为是正文
                if cn > 100 and len(text) > 0 and cn / len(text) > 0.4:
                    if cn > best_score:
                        best_score = cn
                        best_div = text

            return best_div if best_div else ""

        except Exception:
            return ""

    def _summarize(self, text: str, max_chars: int = 400) -> str:
        """将长文精简为短摘要：取前几句 + 关键词句，控制在 max_chars 以内"""
        if not text:
            return ""

        # 按句号、问号、感叹号、换行分段
        import re
        sentences = re.split(r"[。！？!?\n]+", text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 10]

        if not sentences:
            return text[:max_chars] + ("..." if len(text) > max_chars else "")

        # 优先取含关键词的句子，再补充分段开头的句子
        keyword_sentences = []
        other_sentences = []
        for s in sentences:
            if any(kw.lower() in s.lower() for kw in self.keywords[:5]):
                keyword_sentences.append(s)
            else:
                other_sentences.append(s)

        # 组合：前2句 + 关键词句 + 补足
        result_lines = []
        result_lines.extend(other_sentences[:2])
        result_lines.extend(keyword_sentences[:3])

        # 去重，保持顺序
        seen = set()
        ordered = []
        for s in result_lines:
            key = s[:20]
            if key not in seen:
                seen.add(key)
                ordered.append(s)

        summary = "。".join(ordered) + "。"
        if len(summary) > max_chars:
            summary = summary[:max_chars] + "..."
        return summary

    # ========== URL 解析 ==========

    def _resolve_url(self, url: str) -> str:
        """解析重定向 URL，返回真实目标地址（如 Google News → 真实文章）"""
        redirect_domains = [
            "news.google.com/rss/articles",
            "news.google.com/articles",
        ]
        if not any(d in url for d in redirect_domains):
            return url
        try:
            resp = self.session.head(url, timeout=10, allow_redirects=True)
            final_url = resp.url
            # 如果最终 URL 仍然是 Google，用 GET 再试一次
            if "google.com" in final_url:
                resp = self.session.get(url, timeout=10, allow_redirects=True)
                final_url = resp.url
            if final_url != url and "google.com" not in final_url:
                return final_url
        except Exception:
            pass
        return url

    def _fetch_url(self, url: str, timeout: int = 15) -> requests.Response | None:
        """GET 请求，兼容 SSL 证书问题 + 自动修正编码"""
        try:
            resp = self.session.get(url, timeout=timeout)
        except requests.exceptions.SSLError:
            resp = self.session.get(url, timeout=timeout, verify=False)
        except Exception:
            return None

        # 自动修正中文网页编码
        if resp.text and ("charset" not in (resp.headers.get("Content-Type", "")).lower()
                          or resp.encoding.lower() in ("iso-8859-1", "latin-1", "windows-1252")):
            if resp.apparent_encoding:
                resp.encoding = resp.apparent_encoding
        return resp

    # ========== RSS 抓取 ==========

    def fetch_rss(self, url: str) -> list[dict]:
        """抓取单个 RSS 源，返回文章列表"""
        articles = []
        try:
            resp = self._fetch_url(url, timeout=15)
            if resp is None or resp.status_code != 200:
                print(f"  [WARN] RSS 下载失败 ({url}): HTTP {resp.status_code if resp else 'error'}")
                return articles
            feed = feedparser.parse(resp.text)
            if not feed.entries:
                feed = feedparser.parse(resp.content)
            for entry in feed.entries[:30]:  # 每个源最多取30条
                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()
                if not title or not link:
                    continue
                # 跳过国内无法访问的境外链接
                if self._is_url_blocked(link):
                    continue
                if not self._match_keywords(title):
                    continue
                # 解析重定向链接 → 真实来源 URL
                link = self._resolve_url(link)
                published = entry.get("published", "") or entry.get("updated", "")
                summary = self._extract_summary(entry)
                source = feed.feed.get("title", urlparse(url).netloc)
                articles.append(self._build_article(
                    title=title,
                    url=link,
                    source=source,
                    summary=summary,
                    published_at=published,
                ))
        except Exception as e:
            print(f"  [WARN] RSS 抓取失败 ({url}): {e}")
        return articles

    # ========== 网页抓取（通用：自动找文章链接）==========

    # 文章详情页 URL 常见特征
    ARTICLE_URL_PATTERNS = [
        "/article/", "/news/", "/detail/", "/p/", "/a/",
        "/info/", "/show", "/view/", "/read/",
        "/link?url=",  # 搜狗微信文章重定向链接
    ]

    def _is_article_url(self, url: str, base_domain: str) -> bool:
        """判断 URL 是否像文章详情页"""
        if self._is_url_blocked(url):
            return False
        # 排除非文章页面
        skip = [
            "tag/", "category/", "author/", "search", "login",
            "about", "page/", "javascript:", "#", "mailto:",
            ".jpg", ".png", ".pdf", ".zip",
            "/product/", "/Live_column", "/Academic_world",
            "/member", "/register", "/user/", "/account",
        ]
        if any(s.lower() in url.lower() for s in skip):
            return False
        # 同域名 + 包含文章路径特征
        if base_domain not in url:
            return False
        return any(p in url for p in self.ARTICLE_URL_PATTERNS) or url.endswith(".html")

    def fetch_page_links(self, page_url: str) -> list[dict]:
        """通用：抓取页面中所有文章链接，无需配置 CSS 选择器"""
        articles = []
        try:
            resp = self._fetch_url(page_url, timeout=15)
            if resp is None or resp.status_code != 200:
                return articles
            soup = BeautifulSoup(resp.text, "lxml")
            parsed = urlparse(page_url)
            base_domain = parsed.netloc

            seen_urls = set()
            for a in soup.select("a[href]"):
                href = a.get("href", "").strip()
                title = a.get_text(strip=True)
                if not title or len(title) < 8 or len(title) > 200:
                    continue
                # 补全相对 URL
                if href.startswith("/"):
                    href = f"{parsed.scheme}://{parsed.netloc}{href}"
                elif not href.startswith("http"):
                    continue

                if not self._is_article_url(href, base_domain):
                    continue
                if href in seen_urls:
                    continue
                seen_urls.add(href)

                if not self._match_keywords(title):
                    continue

                # 尝试找发布时间
                pub_text = ""
                parent_text = a.parent.get_text(strip=True) if a.parent else ""
                import re
                date_match = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})", parent_text)
                if date_match:
                    pub_text = date_match.group(1)

                source = base_domain.replace("www.", "")
                articles.append(self._build_article(
                    title=title, url=href, source=source,
                    summary="", published_at=pub_text,
                ))

            # 限制数量，优先取发布时间较新的
            articles = articles[:30]
        except Exception as e:
            print(f"  [WARN] 页面抓取失败 ({page_url}): {e}")
        return articles

    def fetch_web(self, url: str, selector: str) -> list[dict]:
        """抓取单个网页源，按 CSS 选择器提取文章列表"""
        articles = []
        try:
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            items = soup.select(selector)
            for item in items[:30]:
                link_el = item.select_one("a[href]") or item.find("a")
                if not link_el:
                    continue
                title = link_el.get_text(strip=True)
                href = link_el.get("href", "")
                if not title or not href:
                    continue
                if self._is_url_blocked(href):
                    continue
                if not self._match_keywords(title):
                    continue
                # 补全相对 URL
                if href.startswith("/"):
                    parsed = urlparse(url)
                    href = f"{parsed.scheme}://{parsed.netloc}{href}"
                summary = ""
                desc_el = item.select_one(".description, .summary, .excerpt, p")
                if desc_el:
                    summary = desc_el.get_text(strip=True)
                source = urlparse(url).netloc
                articles.append(self._build_article(
                    title=title,
                    url=href,
                    source=source,
                    summary=summary,
                ))
        except Exception as e:
            print(f"  [WARN] 网页抓取失败 ({url}): {e}")
        return articles

    # ========== 专利搜索 ==========

    def fetch_soopat_patents(self, query: str, max_results: int = 10) -> list[dict]:
        """从 SooPAT 搜索中国专利（国内可访问）"""
        articles = []
        try:
            search_url = f"http://www.soopat.com/Home/Result"
            params = {
                "SearchWord": query,
                "PatentIndex": "0",  # 中国专利
            }
            resp = self._fetch_url(
                f"http://www.soopat.com/Home/Result?SearchWord={requests.utils.quote(query)}&PatentIndex=0",
                timeout=20,
            )
            if resp is None or resp.status_code != 200:
                return articles
            soup = BeautifulSoup(resp.text, "lxml")
            items = soup.select(".PatentInfoBlock, .result-item, .patent-item")
            for item in items[:max_results]:
                title_el = item.select_one("a[href*='Patent'], a[href*='patent']") or item.find("a")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                href = title_el.get("href", "")
                if not title or not href:
                    continue
                if href.startswith("/"):
                    href = f"http://www.soopat.com{href}"
                summary = item.get_text(strip=True)[:self.summary_max_len]
                articles.append(self._build_article(
                    title=f"[中国专利] {title}",
                    url=href,
                    source="SooPAT 专利搜索",
                    summary=summary,
                    category="patent",
                ))
        except Exception as e:
            print(f"  [WARN] 专利搜索失败 (SooPAT): {e}")
        return articles

    # ========== B站搜索 ==========

    def fetch_bilibili_search(self, keyword: str, max_results: int = 10) -> list[dict]:
        """从 B站 搜索视频内容（含标题+描述=资讯摘要）"""
        articles = []
        try:
            api_url = (
                "https://api.bilibili.com/x/web-interface/search/type"
                f"?search_type=video&keyword={requests.utils.quote(keyword)}&page=1"
            )
            resp = self._fetch_url(api_url, timeout=15)
            if resp is None or resp.status_code != 200:
                return articles

            import json as _json
            data = _json.loads(resp.text)
            results = data.get("data", {}).get("result", [])
            for v in results[:max_results]:
                title = v.get("title", "").strip()
                title = title.replace("<em>", "").replace("</em>", "")  # 去搜索高亮标签
                bvid = v.get("bvid", "")
                if not title or not bvid:
                    continue
                if not self._match_keywords(title):
                    continue
                desc = v.get("description", "").strip()
                author = v.get("author", "")
                pub_ts = v.get("pubdate", 0)
                pub_date = ""
                if pub_ts:
                    from datetime import datetime as _dt
                    pub_date = _dt.fromtimestamp(pub_ts).strftime("%Y-%m-%d")
                articles.append(self._build_article(
                    title=f"[B站] {title}" + (f" — {author}" if author else ""),
                    url=f"https://www.bilibili.com/video/{bvid}",
                    source=f"B站·{author}" if author else "B站",
                    summary=desc[:self.summary_max_len],
                    category="tech",
                    published_at=pub_date,
                ))
        except Exception as e:
            print(f"  [WARN] B站搜索失败: {e}")
        return articles

    # ========== 关键词匹配 ==========

    def _match_keywords(self, text: str) -> bool:
        """检查文本是否匹配任意关键词（不区分大小写）"""
        if not self.keywords:
            return True  # 无关键词时不过滤
        text_lower = text.lower()
        for kw in self.keywords:
            if kw.lower() in text_lower:
                return True
        return False

    # ========== 摘要提取 ==========

    def _extract_summary(self, entry) -> str:
        """从 RSS entry 提取摘要"""
        summary = entry.get("summary", "") or entry.get("description", "") or ""
        if not summary:
            content = entry.get("content", [])
            if content:
                summary = content[0].get("value", "")
        # 去除 HTML 标签
        summary = re.sub(r"<[^>]+>", "", summary)
        summary = re.sub(r"\s+", " ", summary).strip()
        if len(summary) > self.summary_max_len:
            summary = summary[:self.summary_max_len] + "..."
        return summary

    # ========== 构建文章数据 ==========

    def _build_article(
        self,
        title: str,
        url: str,
        source: str = "",
        summary: str = "",
        category: str = "",
        published_at: str = "",
    ) -> dict:
        """构建标准化的文章数据"""
        # 自动推断分类（标题+摘要联合判断）
        if not category:
            text = (title + summary).lower()
            if any(w in text for w in ["专利", "patent", "发明", "实用新型", "授权",
                                        "知识产权", "创新专利", "专利申请", "专利布局"]):
                category = "patent"
            elif any(w in text for w in ["技术", "technology", "研发", "突破", "算法",
                                          "工艺", "工艺", "新技术", "技术突破", "攻克"]):
                category = "tech"
            else:
                category = "industry"

        # 解析发布时间
        pub_dt = ""
        if published_at:
            try:
                # 尝试多种格式
                for fmt in [
                    "%a, %d %b %Y %H:%M:%S %z",
                    "%Y-%m-%dT%H:%M:%S%z",
                    "%Y-%m-%dT%H:%M:%SZ",
                    "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%d",
                ]:
                    try:
                        pub_dt = datetime.strptime(published_at.strip(), fmt).strftime("%Y-%m-%d")
                        break
                    except ValueError:
                        continue
            except Exception:
                pass
        if not pub_dt:
            pub_dt = today_cst()

        return {
            "id": article_id(url),
            "title": title.strip(),
            "url": url.strip(),
            "source": source.strip(),
            "domain": self.domain_slug,
            "category": category,
            "summary": summary.strip()[:self.summary_max_len],
            "published_at": pub_dt,
            "fetched_at": now_cst(),
        }

    # ========== 主抓取流程 ==========

    def run(self) -> list[dict]:
        """执行完整抓取流程"""
        print(f"\n{'='*50}")
        print(f"  开始抓取: {self.config.get('name', self.domain_slug)}")
        print(f"{'='*50}")

        # 1. RSS 抓取
        if self.rss_sources:
            print(f"  [RSS] 共 {len(self.rss_sources)} 个源")
            for rss_url in self.rss_sources:
                print(f"    抓取: {rss_url[:60]}...")
                articles = self.fetch_rss(rss_url)
                self.new_articles.extend(articles)
                print(f"    获取 {len(articles)} 条有效资讯")
                time.sleep(1)  # 礼貌延时

        # 2. 页面链接抓取（无需选择器，自动识别文章链接）
        page_urls = self.config.get("page_urls", [])
        if page_urls:
            print(f"  [PAGE] 共 {len(page_urls)} 个页面")
            for page_url in page_urls:
                print(f"    抓取: {page_url[:60]}...")
                articles = self.fetch_page_links(page_url)
                self.new_articles.extend(articles)
                print(f"    获取 {len(articles)} 条有效资讯")
                time.sleep(1)

        # 3. 网页抓取（高级：指定 CSS 选择器）
        if self.web_sources:
            print(f"  [WEB] 共 {len(self.web_sources)} 个源")
            for ws in self.web_sources:
                url = ws.get("url", "")
                selector = ws.get("selector", "article, .news-item, .post")
                print(f"    抓取: {url[:60]}...")
                articles = self.fetch_web(url, selector)
                self.new_articles.extend(articles)
                print(f"    获取 {len(articles)} 条有效资讯")
                time.sleep(1)

        # 4. 专利搜索
        if self.patent_queries:
            print(f"  [PATENT] 共 {len(self.patent_queries)} 个查询")
            for pq in self.patent_queries:
                query = pq.get("query", "")
                ptype = pq.get("type", "invention")
                print(f"    搜索: {query[:50]}...")
                articles = self.fetch_soopat_patents(query)
                self.new_articles.extend(articles)
                print(f"    获取 {len(articles)} 条专利信息")
                time.sleep(2)

        # 5. B站搜索
        bilibili_queries = self.config.get("bilibili_queries", [])
        if bilibili_queries:
            print(f"  [B站] 共 {len(bilibili_queries)} 个搜索")
            for query in bilibili_queries:
                print(f"    搜索: {query[:50]}...")
                articles = self.fetch_bilibili_search(query)
                self.new_articles.extend(articles)
                print(f"    获取 {len(articles)} 条视频资讯")
                time.sleep(1)

        # 6. 内容增强：对摘要不足的文章，抓取正文重新生成摘要
        self._enrich_summaries()

        print(f"  总计: {len(self.new_articles)} 条新资讯")
        return self.new_articles

    def _enrich_summaries(self):
        """对摘要过短的文章，抓取原文补充摘要"""
        need_enrich = [a for a in self.new_articles if len(a.get("summary", "")) < 50]
        if not need_enrich:
            return
        print(f"  [ENRICH] 为 {len(need_enrich)} 条资讯抓取正文摘要...")
        for art in need_enrich[:10]:  # 每次最多抓10篇，避免太慢
            try:
                text = self._fetch_article_text(art["url"])
                if len(text) > 50:
                    art["summary"] = self._summarize(text)
                time.sleep(0.5)
            except Exception:
                pass

    def deduplicate_and_save(self):
        """去重并保存到数据文件"""
        existing = load_articles()
        existing_urls = {a["url"] for a in existing}
        added = 0
        for art in self.new_articles:
            if art["url"] not in existing_urls:
                existing.insert(0, art)
                existing_urls.add(art["url"])
                added += 1

        # 只保留最近 history_weeks 周的资讯
        # 清理过旧数据（保留最近12周）
        save_articles(existing)

        # 记录更新日志
        logs = load_update_log()
        logs.insert(0, {
            "date": now_cst(),
            "domain": self.domain_slug,
            "fetched": len(self.new_articles),
            "new": added,
            "total": len(existing),
        })
        # 保留12周日志
        save_update_log(logs[:84])  # 12周 * 7天

        print(f"  去重后新增: {added} 条, 数据库总计: {len(existing)} 条")
        return added
