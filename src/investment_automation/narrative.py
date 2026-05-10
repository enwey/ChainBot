from __future__ import annotations

from typing import Any

from .db import Database
from .news import event_match_score
from .settings import Settings


class NarrativeService:
    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database

    def apply_overlay(self, scan: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.narrative_enabled:
            return scan

        text = self._overlay_text(payload)
        narrative_hits = [word for word in self.settings.narrative_keywords if word and word in text]
        kol_hits = [word for word in self.settings.trusted_kol_keywords if word and word in text]
        news_matches = self.match_news_events(text)

        news_bonus = min(sum(float(match["bonus"]) for match in news_matches), self.settings.news_max_bonus)
        keyword_bonus = min(
            len(narrative_hits) * self.settings.narrative_score_bonus
            + len(kol_hits) * self.settings.trusted_kol_score_bonus,
            self.settings.narrative_max_bonus,
        )
        bonus = min(keyword_bonus + news_bonus, self.settings.narrative_max_bonus + self.settings.news_max_bonus)
        if bonus <= 0:
            scan["narrative_score"] = 0.0
            scan["narrative_tags"] = []
            scan["narrative_label"] = "-"
            scan["news_matches"] = []
            return scan

        news_tags = [hit for match in news_matches for hit in match["hits"]]
        tags = sorted(set(narrative_hits + kol_hits + news_tags))
        scan["score"] = min(float(scan["score"]) + bonus, 100.0)
        scan["narrative_score"] = bonus
        scan["narrative_tags"] = tags
        scan["news_matches"] = news_matches[:3]
        scan["narrative_label"] = ",".join(tags[:4])
        return scan

    def match_news_events(self, text: str) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        if not self.settings.news_enabled:
            return matches

        for event in self.database.active_news_events():
            if float(event.get("confidence") or 0.0) < self.settings.news_min_confidence:
                continue
            score, hits = event_match_score(text, event)
            if score <= 0:
                continue
            matches.append(
                {
                    "title": event.get("title"),
                    "source": event.get("source"),
                    "sentiment": event.get("sentiment"),
                    "confidence": event.get("confidence"),
                    "hits": hits,
                    "bonus": score * self.settings.news_match_bonus,
                }
            )

        matches.sort(key=lambda item: float(item["bonus"]), reverse=True)
        return matches

    def _overlay_text(self, payload: dict[str, Any]) -> str:
        return " ".join(
            str(payload.get(key) or "")
            for key in ("symbol", "name", "description", "twitter", "telegram", "website")
        ).lower()
