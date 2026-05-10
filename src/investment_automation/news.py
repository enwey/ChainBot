from __future__ import annotations

import email.utils
import hashlib
import html
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, List, Optional

import requests

from .runtime_safety import DependencyFailureRecord, RuntimeSafetyManager
from .settings import Settings

logger = logging.getLogger(__name__)


TOPIC_RULES = {
    "ai": ("ai", "artificial intelligence", "openai", "nvidia", "chatgpt"),
    "exchange": ("binance", "coinbase", "okx", "bybit", "kraken", "listing", "cex"),
    "regulation": ("sec", "cftc", "court", "lawsuit", "settlement", "etf", "fed", "cpi"),
    "macro": ("fed", "rate", "inflation", "cpi", "jobs", "election"),
    "celebrity": ("elon", "trump", "cz", "vitalik", "musk"),
    "security": ("hack", "exploit", "rug", "drain", "stolen"),
    "solana": ("solana", "sol", "pump.fun", "pumpfun", "jupiter"),
}

ENTITY_WORDS = (
    "cz",
    "binance",
    "coinbase",
    "elon",
    "trump",
    "vitalik",
    "solana",
    "openai",
    "nvidia",
    "sec",
    "fed",
    "etf",
    "jupiter",
)

TOKEN_ALIAS_RULES = {
    "doge": ("doge", "dogecoin"),
    "bnb": ("bnb", "binance", "build"),
    "eth": ("eth", "ethereum", "vitalik"),
    "sol": ("sol", "solana"),
    "grok": ("grok",),
    "tesla": ("tesla",),
    "x": ("x", "twitter"),
}

POSITIVE_WORDS = (
    "approved",
    "launch",
    "listing",
    "partnership",
    "buy",
    "bull",
    "surge",
    "record",
    "win",
)
NEGATIVE_WORDS = (
    "hack",
    "exploit",
    "lawsuit",
    "charged",
    "ban",
    "crash",
    "stolen",
    "fraud",
    "probe",
)


@dataclass
class NewsEvent:
    event_id: str
    source: str
    title: str
    url: str
    published_ts: int
    fetched_ts: int
    topics: list[str]
    entities: list[str]
    sentiment: str
    confidence: float
    source_weight: float
    active_until_ts: int
    raw_text: str


class NewsClient:
    def __init__(
        self, settings: Settings, *, failure_sink: RuntimeSafetyManager | None = None
    ) -> None:
        self.settings = settings
        self.failure_sink = failure_sink
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "investment-automation-news/0.1"})
        self.kol_profiles = self._parse_kol_profiles()

    def close(self) -> None:
        self.session.close()

    def fetch_events(self) -> list[NewsEvent]:
        events: list[NewsEvent] = []
        for feed_url in self.settings.news_feeds:
            try:
                events.extend(self._fetch_feed(feed_url))
                self._record_dependency_success("rss_feed", feed_url)
            except Exception as exc:
                self._record_dependency_failure("rss_feed", feed_url, exc)
                logger.exception("Failed to fetch news feed %s", feed_url)
        if self.settings.kol_monitor_enabled:
            for profile_name, feed_url in self._kol_feed_entries():
                try:
                    events.extend(
                        self._fetch_feed(feed_url, source=f"kol:{profile_name}", is_kol=True)
                    )
                    self._record_dependency_success("kol_feed", profile_name)
                except Exception as exc:
                    self._record_dependency_failure("kol_feed", profile_name, exc)
                    logger.exception("Failed to fetch KOL feed %s", feed_url)
        return events

    def _fetch_feed(self, feed_url: str, source: str = "", is_kol: bool = False) -> list[NewsEvent]:
        response = self.session.get(feed_url, timeout=8)
        response.raise_for_status()
        content = response.content.lstrip(b"\xef\xbb\xbf\r\n\t ")
        root = ET.fromstring(content)
        source = source or self._source_name(feed_url)
        items = list(root.findall(".//item")) or list(root.findall(".//{*}entry"))
        return [
            event
            for event in (self._parse_item(source, item, is_kol=is_kol) for item in items[:30])
            if event is not None
        ]

    def _parse_item(
        self, source: str, item: ET.Element, is_kol: bool = False
    ) -> Optional[NewsEvent]:
        title = self._text(item, "title")
        url = self._text(item, "link")
        if not url:
            link_el = item.find("{*}link")
            url = (link_el.attrib.get("href") if link_el is not None else "") or ""
        description = self._text(item, "description") or self._text(item, "summary")
        published = (
            self._text(item, "pubDate")
            or self._text(item, "published")
            or self._text(item, "updated")
        )
        if not title:
            return None
        if self._is_placeholder_feed_item(title, description):
            return None
        published_ts = self._parse_time(published) or int(time.time())
        fetched_ts = int(time.time())
        raw_text = self._clean(f"{title} {description}")
        topics, entities = self._classify(raw_text)
        if is_kol or source.startswith("kol:"):
            kol_topics, kol_entities = self._classify_kol_text(source, raw_text)
            topics = sorted(set(topics + kol_topics + ["kol"]))
            entities = sorted(set(entities + kol_entities))
        sentiment = self._sentiment(raw_text)
        source_weight = (
            self.settings.kol_source_weight
            if is_kol or source.startswith("kol:")
            else self._source_weight(source, url)
        )
        freshness = max(
            0.0,
            1.0 - max(fetched_ts - published_ts, 0) / max(self.settings.news_event_ttl_seconds, 1),
        )
        confidence = min(
            1.0,
            0.2
            + 0.25 * len(topics)
            + 0.12 * len(entities)
            + 0.25 * source_weight
            + 0.18 * freshness,
        )
        ttl = (
            self.settings.kol_event_ttl_seconds
            if is_kol or source.startswith("kol:")
            else self.settings.news_event_ttl_seconds
        )
        event_id = hashlib.sha1(f"{source}|{title}|{url}".encode("utf-8")).hexdigest()
        return NewsEvent(
            event_id=event_id,
            source=source,
            title=title.strip(),
            url=url.strip(),
            published_ts=published_ts,
            fetched_ts=fetched_ts,
            topics=topics,
            entities=entities,
            sentiment=sentiment,
            confidence=confidence,
            source_weight=source_weight,
            active_until_ts=published_ts + ttl,
            raw_text=raw_text,
        )

    def _classify(self, text: str) -> tuple[list[str], list[str]]:
        lowered = text.lower()
        topics = [
            topic
            for topic, words in TOPIC_RULES.items()
            if any(self._contains_term(lowered, word) for word in words)
        ]
        entities = [word for word in ENTITY_WORDS if self._contains_term(lowered, word)]
        entities.extend(self._extract_token_candidates(text))
        return sorted(set(topics)), sorted(set(entities))

    def _classify_kol_text(self, source: str, text: str) -> tuple[list[str], list[str]]:
        lowered = text.lower()
        profile = source.split(":", 1)[-1].lower()
        aliases = self.kol_profiles.get(profile, ())
        entities = [profile]
        entities.extend(alias for alias in aliases if self._contains_term(lowered, alias))
        for token, words in TOKEN_ALIAS_RULES.items():
            if any(self._contains_term(lowered, word) for word in words):
                entities.append(token)
        entities.extend(self._extract_token_candidates(text))
        return ["kol", "celebrity"], sorted(set(item for item in entities if item))

    def _extract_token_candidates(self, text: str) -> list[str]:
        candidates = []
        candidates.extend(
            match.group(1).lower() for match in re.finditer(r"\$([a-zA-Z][a-zA-Z0-9_]{1,12})", text)
        )
        candidates.extend(
            match.group(1).lower() for match in re.finditer(r"#([a-zA-Z][a-zA-Z0-9_]{2,20})", text)
        )
        candidates.extend(
            match.group(1).lower() for match in re.finditer(r"\b([A-Z]{2,8})\b", text)
        )
        return [
            item
            for item in candidates
            if item not in {"the", "and", "for", "you", "this", "that", "with", "from"}
        ]

    def _sentiment(self, text: str) -> str:
        lowered = text.lower()
        pos = sum(1 for word in POSITIVE_WORDS if self._contains_term(lowered, word))
        neg = sum(1 for word in NEGATIVE_WORDS if self._contains_term(lowered, word))
        if neg > pos:
            return "bearish"
        if pos > neg:
            return "bullish"
        return "neutral"

    def _source_weight(self, source: str, url: str) -> float:
        text = f"{source} {url}".lower()
        if any(word in text for word in ("sec.gov", "federalreserve", "binance", "coinbase")):
            return 1.0
        if any(word in text for word in ("coindesk", "cointelegraph", "decrypt")):
            return 0.65
        return 0.45

    def _text(self, item: ET.Element, tag: str) -> str:
        node = item.find(tag) or item.find(f"{{*}}{tag}")
        return "".join(node.itertext()).strip() if node is not None else ""

    def _parse_time(self, value: str) -> int:
        if not value:
            return 0
        try:
            parsed = email.utils.parsedate_to_datetime(value)
            return int(parsed.timestamp())
        except (TypeError, ValueError):
            return 0

    def _clean(self, value: str) -> str:
        return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()

    def _source_name(self, feed_url: str) -> str:
        host = feed_url.split("//", 1)[-1].split("/", 1)[0]
        return host.replace("www.", "")

    def _contains_term(self, text: str, term: str) -> bool:
        if " " in term or "." in term:
            return term in text
        return re.search(rf"(^|[^a-z0-9]){re.escape(term)}([^a-z0-9]|$)", text) is not None

    def _is_placeholder_feed_item(self, title: str, description: str) -> bool:
        text = f"{title} {description}".lower()
        blocked = (
            "rss reader not yet whitelisted",
            "not yet whitelisted",
            "enable javascript",
            "cloudflare",
            "access denied",
            "rate limited",
            "too many requests",
        )
        return any(term in text for term in blocked)

    def _parse_kol_profiles(self) -> dict[str, tuple[str, ...]]:
        profiles = {}
        for entry in self.settings.kol_profiles:
            if "=" not in entry:
                continue
            name, aliases = entry.split("=", 1)
            profiles[name.strip().lower()] = tuple(
                item.strip().lower() for item in aliases.split("|") if item.strip()
            )
        return profiles

    def _kol_feed_entries(self) -> list[tuple[str, str]]:
        entries = []
        for raw in self.settings.kol_feeds:
            if "=" not in raw:
                continue
            name, url = raw.split("=", 1)
            name = name.strip().lower()
            url = url.strip()
            if name and url:
                entries.append((name, url))
        return entries

    def _record_dependency_failure(self, dependency: str, operation: str, exc: Exception) -> None:
        if self.failure_sink is None:
            return
        self.failure_sink.record_dependency_failure(
            DependencyFailureRecord(
                component="news",
                dependency=dependency,
                operation=operation,
                message=str(exc),
                occurred_ts=int(time.time()),
                failure_class=exc.__class__.__name__,
                metadata={},
            )
        )

    def _record_dependency_success(self, dependency: str, operation: str) -> None:
        if self.failure_sink is None:
            return
        self.failure_sink.record_dependency_success("news", dependency, operation)


def event_match_score(scan_text: str, event: dict[str, Any]) -> tuple[float, list[str]]:
    lowered = scan_text.lower()
    terms: List[str] = []
    terms.extend(str(item).lower() for item in event.get("entities", []) if item)
    terms.extend(str(item).lower() for item in event.get("topics", []) if item)
    hits = sorted(
        {
            term
            for term in terms
            if term and re.search(rf"(^|[^a-z0-9]){re.escape(term)}([^a-z0-9]|$)", lowered)
        }
    )
    if not hits:
        return 0.0, []
    confidence = float(event.get("confidence") or 0.0)
    source_weight = float(event.get("source_weight") or 0.0)
    sentiment_multiplier = 0.6 if event.get("sentiment") == "bearish" else 1.0
    return min(
        1.0,
        (0.35 + 0.25 * len(hits) + 0.25 * confidence + 0.15 * source_weight) * sentiment_multiplier,
    ), hits
