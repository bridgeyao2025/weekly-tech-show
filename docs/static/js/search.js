/**
 * Weekly Tech Show — 客户端搜索
 * 无需后端，纯前端实时筛选
 */
(function () {
    const searchInput = document.getElementById("searchInput");
    const domainFilter = document.getElementById("domainFilter");
    const categoryFilter = document.getElementById("categoryFilter");

    if (!searchInput) return;

    // 获取当前页面的领域 (从 URL path 推断)
    const pathParts = window.location.pathname.split("/");
    let currentDomain = "";
    for (const part of pathParts) {
        if (["cmf", "pvd", "optical"].includes(part)) {
            currentDomain = part;
            break;
        }
    }
    if (currentDomain && domainFilter) {
        domainFilter.value = currentDomain;
    }

    function filterArticles() {
        const query = searchInput.value.toLowerCase().trim();
        const selDomain = domainFilter ? domainFilter.value : "";
        const selCategory = categoryFilter ? categoryFilter.value : "";

        // 查找所有文章项
        const items = document.querySelectorAll(".article-item");
        let visibleCount = 0;

        items.forEach(function (item) {
            let show = true;

            // 关键词匹配（标题 + 摘要）
            if (query) {
                const title = (item.querySelector("h4") || {}).textContent || "";
                const summary = (item.querySelector(".summary") || {}).textContent || "";
                const source = (item.querySelector(".source") || {}).textContent || "";
                const text = (title + " " + summary + " " + source).toLowerCase();
                if (!text.includes(query)) {
                    show = false;
                }
            }

            // 领域筛选（通过文章的 domain 数据属性或 URL）
            if (show && selDomain) {
                const links = item.querySelectorAll("a");
                let matchDomain = false;
                links.forEach(function (link) {
                    if (link.href.includes("/" + selDomain + "/")) {
                        matchDomain = true;
                    }
                });
                // 如果当前在领域页内，且筛选的是当前领域，则匹配
                if (currentDomain === selDomain) {
                    matchDomain = true;
                }
                if (!matchDomain) {
                    show = false;
                }
            }

            // 分类筛选
            if (show && selCategory) {
                const catTag = item.querySelector(".cat-tag");
                const catClass = catTag ? catTag.className : "";
                if (!catClass.includes("cat-" + selCategory)) {
                    show = false;
                }
            }

            if (show) {
                item.classList.remove("hidden");
                visibleCount++;
            } else {
                item.classList.add("hidden");
            }
        });

        // 处理空状态提示
        document.querySelectorAll(".col-list, .domain-preview, .domain-grid").forEach(function (container) {
            const items = container.querySelectorAll(".article-item");
            const visibleItems = container.querySelectorAll(".article-item:not(.hidden)");
            const emptyHint = container.querySelector(".empty-search");

            if (emptyHint) emptyHint.remove();

            if (items.length > 0 && visibleItems.length === 0 && query) {
                const hint = document.createElement("p");
                hint.className = "empty-hint empty-search";
                hint.textContent = "无匹配结果，请尝试其他关键词";
                container.appendChild(hint);
            }
        });
    }

    // 事件绑定
    searchInput.addEventListener("input", filterArticles);
    if (domainFilter) domainFilter.addEventListener("change", filterArticles);
    if (categoryFilter) categoryFilter.addEventListener("change", filterArticles);

    // 键盘快捷键: / 聚焦搜索框
    document.addEventListener("keydown", function (e) {
        if (e.key === "/" && document.activeElement !== searchInput) {
            e.preventDefault();
            searchInput.focus();
        }
    });
})();
