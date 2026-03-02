"""
数据模型 - 使用 dataclass 定义公告和配置的结构化类型
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Announcement:
    """单条公告的数据模型（不可变）"""

    title: str
    url: str
    date: str
    section: str

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Announcement):
            return NotImplemented
        return self.url == other.url

    def __hash__(self) -> int:
        return hash(self.url)

    def to_dict(self) -> dict[str, str]:
        return {
            "title": self.title,
            "url": self.url,
            "date": self.date,
            "section": self.section,
        }

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> Announcement:
        return cls(
            title=data["title"],
            url=data["url"],
            date=data.get("date", ""),
            section=data.get("section", ""),
        )
