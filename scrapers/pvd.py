"""PVD 真空镀膜领域抓取器"""
from .base import BaseScraper


class PVDScraper(BaseScraper):
    """PVD 真空镀膜领域"""

    def __init__(self, config: dict):
        super().__init__("pvd", config)

    def run(self) -> list[dict]:
        articles = super().run()
        # PVD 领域特有逻辑可在此扩展
        return articles
