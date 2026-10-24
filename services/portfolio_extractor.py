"""Extract holdings from screenshots, CSV, PDF or Excel files."""

import base64
import json
import logging
import re
from typing import Any, Dict, Optional

from services.ai_insights import LLMClient
from services.document_parser import ExtractedHolding, ParsedPortfolio

logger = logging.getLogger(__name__)

IMAGE_TYPES = {"png", "jpg", "jpeg", "webp", "gif"}
DOCUMENT_TYPES = {"pdf", "csv", "txt", "xlsx", "xls"}

MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
}

NOTIONAL = 100000.0

SYSTEM = (
    "You extract portfolio holdings from statements, broker screenshots and exports. "
    "You never invent a holding. If a field is unreadable, omit it."
)

PROMPT = """Extract every holding from this document.

Return ONLY a JSON object:
{
  "currency": "USD",
  "account_value": 100000,
  "holdings": [
    {"ticker": "AAPL", "name": "Apple Inc", "weight": 0.12, "value": 12000, "shares": 50, "sector": "Technology"}
  ]
}

Rules:
- ticker: exchange symbol in capitals (BRK.B is allowed). Drop currency suffixes.
- weight: fraction of the portfolio, 0 to 1. If the document gives percentages, divide by 100.
- value or shares: include only when the document shows them.
- If weights are missing but values are present, leave weight out; totals will be normalised.
- Do not add commentary, totals, cash rows or disclaimers. JSON only."""


def _strip_json(text: str) -> str:
    text = text.strip()
    if "```" in text:
        parts = re.split(r"```(?:json)?", text)
        text = max(parts, key=len)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return text.strip()


def _to_portfolio(payload: Dict[str, Any]) -> ParsedPortfolio:
    holdings = []
    for item in payload.get("holdings") or []:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker") or "").upper().strip()
        if not ticker or not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,9}", ticker):
            continue
        holding = ExtractedHolding(
            ticker=ticker,
            name=(str(item["name"]).strip()[:120] if item.get("name") else None),
            asset_class=(str(item["sector"]).strip()[:80] if item.get("sector") else None),
            confidence=0.8,
        )
        for field in ("weight", "value", "shares"):
            number = item.get(field)
            if isinstance(number, (int, float)) and number > 0:
                setattr(holding, "_weight" if field == "weight" else field, float(number))
        if getattr(holding, "_weight", 0) > 1:
            holding._weight = holding._weight / 100
        holdings.append(holding)

    holdings = holdings[:50]
    for holding in holdings:
        weight = getattr(holding, "_weight", None)
        if weight and not holding.market_value:
            holding.market_value = weight * NOTIONAL
    holdings = [h for h in holdings if h.ticker]

    total_value = sum(h.market_value or 0 for h in holdings)
    for holding in holdings:
        if hasattr(holding, "_weight"):
            delattr(holding, "_weight")

    return ParsedPortfolio(
        holdings=holdings,
        account_value=payload.get("account_value") or total_value or NOTIONAL,
        broker=None,
        extraction_confidence=0.8 if holdings else 0.0,
        raw_text=None,
    )


def extract_with_llm(file_bytes: bytes, filename: str, mime: str) -> ParsedPortfolio:
    encoded = base64.b64encode(file_bytes).decode("ascii")
    messages = [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
            ],
        },
    ]
    client = LLMClient(temperature=0.1, max_tokens=4000)
    raw = client.chat(messages, temperature=0.1, max_tokens=4000)
    return _to_portfolio(json.loads(_strip_json(raw)))


def extract_document(file_bytes: bytes, filename: str, broker_hint: Optional[str]) -> ParsedPortfolio:
    from services.document_parser import document_parser

    return document_parser.parse(file_bytes, filename, broker_hint)


def extract(file_bytes: bytes, filename: str, broker_hint: Optional[str] = None) -> ParsedPortfolio:
    ext = filename.lower().rsplit(".", 1)[-1]
    if ext in IMAGE_TYPES:
        return extract_with_llm(file_bytes, filename, MIME[ext])
    if ext in DOCUMENT_TYPES:
        return extract_document(file_bytes, filename, broker_hint)
    raise ValueError(f"Unsupported file type: {ext}")
