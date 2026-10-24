"""Extract holdings from screenshots, CSV, PDF, DOCX or Excel files."""

import base64
import json
import logging
import re
import zipfile
from io import BytesIO
from typing import Any, Dict, Optional

from services.ai_insights import LLMClient
from services.document_parser import ExtractedHolding, ParsedPortfolio

logger = logging.getLogger(__name__)

IMAGE_TYPES = {"png", "jpg", "jpeg", "webp", "gif"}
DOCUMENT_TYPES = {"pdf", "csv", "txt", "xlsx", "xls", "docx"}

MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
}

NOTIONAL = 100000.0
MAX_ATTEMPTS = 3

SYSTEM = (
    "You extract portfolio holdings from statements, broker screenshots and exports. "
    "You never invent a holding. If a field is unreadable, omit it."
)

SCHEMA = """{
  "currency": "USD",
  "account_value": 100000,
  "holdings": [
    {"ticker": "AAPL", "name": "Apple Inc", "weight": 0.12, "value": 12000, "shares": 50, "sector": "Technology"}
  ]
}"""

RULES = """Rules:
- ticker: the exchange symbol in capitals (BRK.B is allowed). Drop currency suffixes.
- name: the full company name exactly as printed. Never strip letters that happen to match
  the ticker: "JPMorgan Chase" stays "JPMorgan Chase", not "Morgan Chase".
- sector: GICS-style sector (Technology, Financials, Healthcare, Consumer Discretionary,
  Consumer Staples, Industrials, Energy, Utilities, Real Estate, Communication Services,
  Materials). Use the document when given, otherwise assign the most plausible GICS sector.
- weight: fraction of the portfolio, 0 to 1. Percentages must be divided by 100.
- value or shares: include only when the document shows them.
- If weights are missing but values are present, leave weight out; totals are normalised.
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


def _ask_json(messages: list) -> Dict[str, Any]:
    """Call the model with a small retry budget; no token cap."""
    client = LLMClient(temperature=0.1)
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw = client.chat(messages, temperature=0.1)
            payload = json.loads(_strip_json(raw))
            if payload.get("holdings"):
                return payload
            last_error = ValueError("no holdings in response")
        except Exception as exc:  # malformed JSON, transport error, empty answer
            last_error = exc
            logger.warning("Extraction attempt %s/%s failed: %s", attempt, MAX_ATTEMPTS, exc)
    raise ValueError(f"Extraction failed after {MAX_ATTEMPTS} attempts: {last_error}")


def _vision_messages(file_bytes: bytes, mime: str) -> list:
    encoded = base64.b64encode(file_bytes).decode("ascii")
    prompt = f"Extract every holding from this document.\n\nReturn ONLY a JSON object:\n{SCHEMA}\n\n{RULES}"
    return [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
            ],
        },
    ]


def _text_messages(text: str) -> list:
    prompt = (
        "Extract every holding from this document text.\n\n"
        f"Return ONLY a JSON object:\n{SCHEMA}\n\n{RULES}\n\nDOCUMENT:\n{text[:20000]}"
    )
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": prompt},
    ]


def _docx_text(file_bytes: bytes) -> str:
    with zipfile.ZipFile(BytesIO(file_bytes)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8", "ignore")
    xml = xml.replace("</w:p>", "\n").replace("</w:tr>", "\n")
    text = re.sub(r"<[^>]+>", " ", xml)
    return re.sub(r"[ \t]+", " ", text).strip()


def _xlsx_text(file_bytes: bytes) -> str:
    try:
        import openpyxl
        workbook = openpyxl.load_workbook(BytesIO(file_bytes), read_only=True, data_only=True)
        lines = []
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows(values_only=True):
                cells = [str(cell) for cell in row if cell is not None]
                if cells:
                    lines.append("\t".join(cells))
        return "\n".join(lines)
    except Exception as exc:
        logger.warning("XLSX text extraction failed: %s", exc)
        return ""


def _pdf_text(file_bytes: bytes) -> str:
    try:
        import pdfplumber
        with pdfplumber.open(BytesIO(file_bytes)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages).strip()
    except Exception as exc:
        logger.warning("PDF text extraction failed: %s", exc)
        return ""


def _text_for(filename: str, file_bytes: bytes) -> str:
    ext = filename.lower().rsplit(".", 1)[-1]
    if ext == "docx":
        return _docx_text(file_bytes)
    if ext == "pdf":
        return _pdf_text(file_bytes)
    if ext in {"xlsx", "xls"}:
        return _xlsx_text(file_bytes)
    if ext in {"txt", "csv"}:
        return file_bytes.decode("utf-8", "ignore")
    return ""


def extract_with_llm(file_bytes: bytes, mime: str) -> ParsedPortfolio:
    return _to_portfolio(_ask_json(_vision_messages(file_bytes, mime)))


def extract_document(file_bytes: bytes, filename: str, broker_hint: Optional[str]) -> ParsedPortfolio:
    ext = filename.lower().rsplit(".", 1)[-1]
    if ext not in {"docx", "pdf", "xlsx", "xls"}:
        from services.document_parser import document_parser
        parsed = document_parser.parse(file_bytes, filename, broker_hint)
        if len(parsed.holdings) >= 2:
            return parsed
    elif ext in {"xlsx", "xls"}:
        from services.document_parser import document_parser
        parsed = document_parser.parse(file_bytes, filename, broker_hint)
        complete = (
            len(parsed.holdings) >= 2
            and all(h.ticker and h.asset_class for h in parsed.holdings)
            and any(h.market_value for h in parsed.holdings)
        )
        if complete:
            return parsed

    text = _text_for(filename, file_bytes)
    if text:
        try:
            return _to_portfolio(_ask_json(_text_messages(text)))
        except Exception as exc:
            logger.warning("Text extraction fallback failed: %s", exc)

    if ext == "docx":
        raise ValueError("No readable holdings in the document")
    from services.document_parser import document_parser
    return document_parser.parse(file_bytes, filename, broker_hint)


def extract(file_bytes: bytes, filename: str, broker_hint: Optional[str] = None) -> ParsedPortfolio:
    ext = filename.lower().rsplit(".", 1)[-1]
    if ext in IMAGE_TYPES:
        return extract_with_llm(file_bytes, MIME[ext])
    if ext in DOCUMENT_TYPES:
        return extract_document(file_bytes, filename, broker_hint)
    raise ValueError(f"Unsupported file type: {ext}")
