"""CMF 领域抓取器"""
from .base import BaseScraper


class CMFScraper(BaseScraper):
    """CMF（色彩·材料·表面处理）领域"""

    def __init__(self, config: dict):
        super().__init__("cmf", config)

    def run(self) -> list[dict]:
        articles = super().run()
        # CMF 领域特有：额外搜索色彩趋势报告
        return articles
