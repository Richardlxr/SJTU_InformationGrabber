"""
数据模型 - 使用 dataclass 定义公告和配置的结构化类型
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Announcement:
    """单条公告的数据模型（不可变）"""

    title: str
    url: str
    date: str  # 公告为发布日期；活动为举办时间（如 "2026-09-30 12:00-13:30"）
    section: str  # 板块 / 分类（如「学生事务」「学术活动·ChalkTalk」）
    source: str = ""  # 来源站点（教务处 / 计算机学院 / 致远学院）
    summary: str = ""  # 正文摘要（列表页自带，或发送前从详情页补全）
    location: str = ""  # 活动地点（仅活动）
    speaker: str = ""  # 主讲人（仅活动）

    @property
    def is_event(self) -> bool:
        """是否为讲座 / 活动（有地点或主讲人）"""
        return bool(self.location or self.speaker)

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
            "source": self.source,
            "summary": self.summary,
            "location": self.location,
            "speaker": self.speaker,
        }

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> Announcement:
        return cls(
            title=data["title"],
            url=data["url"],
            date=data.get("date", ""),
            section=data.get("section", ""),
            source=data.get("source", ""),
            summary=data.get("summary", ""),
            location=data.get("location", ""),
            speaker=data.get("speaker", ""),
        )
