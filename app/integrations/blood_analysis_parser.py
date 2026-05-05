"""Blood lab report parser.

Supports:
  - PDF text extraction via pypdf
  - Image analysis via OpenAI Vision (if configured)
  - Structured biomarker extraction via LLM (preferred) or regex fallback
  - Both English and Spanish lab terminology (common in Argentina)
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Reference ranges — general (not sex-adjusted; LLM provides context)
# ---------------------------------------------------------------------------

_REFERENCE_RANGES: dict[str, dict[str, Any]] = {
    "hemoglobin":          {"ref_min": 12.0, "ref_max": 17.5, "unit": "g/dL",    "display_name": "Hemoglobin",            "category": "blood_count"},
    "hematocrit":          {"ref_min": 36.0, "ref_max": 53.0, "unit": "%",        "display_name": "Hematocrit",            "category": "blood_count"},
    "wbc":                 {"ref_min": 4.5,  "ref_max": 11.0, "unit": "10³/μL",   "display_name": "White Blood Cells",     "category": "blood_count"},
    "rbc":                 {"ref_min": 3.8,  "ref_max": 5.9,  "unit": "10⁶/μL",   "display_name": "Red Blood Cells",       "category": "blood_count"},
    "platelets":           {"ref_min": 150,  "ref_max": 400,  "unit": "10³/μL",   "display_name": "Platelets",             "category": "blood_count"},
    "mcv":                 {"ref_min": 80,   "ref_max": 100,  "unit": "fL",        "display_name": "MCV",                   "category": "blood_count"},
    "iron":                {"ref_min": 60,   "ref_max": 170,  "unit": "μg/dL",    "display_name": "Serum Iron",            "category": "iron"},
    "ferritin":            {"ref_min": 10,   "ref_max": 322,  "unit": "ng/mL",    "display_name": "Ferritin",              "category": "iron"},
    "transferrin_sat":     {"ref_min": 20,   "ref_max": 50,   "unit": "%",         "display_name": "Transferrin Saturation","category": "iron"},
    "glucose":             {"ref_min": 70,   "ref_max": 100,  "unit": "mg/dL",    "display_name": "Glucose (fasting)",     "category": "metabolic"},
    "creatinine":          {"ref_min": 0.5,  "ref_max": 1.3,  "unit": "mg/dL",    "display_name": "Creatinine",            "category": "metabolic"},
    "urea":                {"ref_min": 15,   "ref_max": 45,   "unit": "mg/dL",    "display_name": "Urea (BUN)",            "category": "metabolic"},
    "sodium":              {"ref_min": 136,  "ref_max": 145,  "unit": "mEq/L",    "display_name": "Sodium",                "category": "electrolytes"},
    "potassium":           {"ref_min": 3.5,  "ref_max": 5.0,  "unit": "mEq/L",    "display_name": "Potassium",             "category": "electrolytes"},
    "cholesterol_total":   {"ref_min": None, "ref_max": 200,  "unit": "mg/dL",    "display_name": "Total Cholesterol",     "category": "lipids"},
    "ldl":                 {"ref_min": None, "ref_max": 130,  "unit": "mg/dL",    "display_name": "LDL Cholesterol",       "category": "lipids"},
    "hdl":                 {"ref_min": 40,   "ref_max": None, "unit": "mg/dL",    "display_name": "HDL Cholesterol",       "category": "lipids"},
    "triglycerides":       {"ref_min": None, "ref_max": 150,  "unit": "mg/dL",    "display_name": "Triglycerides",         "category": "lipids"},
    "ast":                 {"ref_min": None, "ref_max": 40,   "unit": "U/L",       "display_name": "AST (TGO)",             "category": "liver"},
    "alt":                 {"ref_min": None, "ref_max": 56,   "unit": "U/L",       "display_name": "ALT (TGP)",             "category": "liver"},
    "ggt":                 {"ref_min": None, "ref_max": 60,   "unit": "U/L",       "display_name": "GGT",                   "category": "liver"},
    "bilirubin_total":     {"ref_min": None, "ref_max": 1.2,  "unit": "mg/dL",    "display_name": "Total Bilirubin",       "category": "liver"},
    "tsh":                 {"ref_min": 0.4,  "ref_max": 4.0,  "unit": "μIU/mL",   "display_name": "TSH",                   "category": "thyroid"},
    "t4_free":             {"ref_min": 0.8,  "ref_max": 1.8,  "unit": "ng/dL",    "display_name": "Free T4",               "category": "thyroid"},
    "vitamin_d":           {"ref_min": 30,   "ref_max": 100,  "unit": "ng/mL",    "display_name": "Vitamin D (25-OH)",     "category": "vitamins"},
    "vitamin_b12":         {"ref_min": 200,  "ref_max": 900,  "unit": "pg/mL",    "display_name": "Vitamin B12",           "category": "vitamins"},
    "folate":              {"ref_min": 5.4,  "ref_max": None, "unit": "ng/mL",    "display_name": "Folate / Folic Acid",   "category": "vitamins"},
    "zinc":                {"ref_min": 70,   "ref_max": 120,  "unit": "μg/dL",    "display_name": "Zinc",                  "category": "minerals"},
    "magnesium":           {"ref_min": 1.7,  "ref_max": 2.5,  "unit": "mg/dL",    "display_name": "Magnesium",             "category": "minerals"},
    "calcium":             {"ref_min": 8.5,  "ref_max": 10.5, "unit": "mg/dL",    "display_name": "Calcium",               "category": "minerals"},
    "insulin":             {"ref_min": 2,    "ref_max": 25,   "unit": "μIU/mL",   "display_name": "Insulin (fasting)",     "category": "hormones"},
}


# ---------------------------------------------------------------------------
# Regex patterns — English + Spanish labels
# ---------------------------------------------------------------------------

_REGEX_PATTERNS: dict[str, list[str]] = {
    "hemoglobin":       [r"h[ae]moglobin[a-z\s]*[:\s]+([\d.,]+)", r"hgb\s*[:\s]+([\d.,]+)"],
    "hematocrit":       [r"hematocrit[a-z\s]*[:\s]+([\d.,]+)", r"hematocrito[a-z\s]*[:\s]+([\d.,]+)", r"hto\s*[:\s]+([\d.,]+)"],
    "wbc":              [r"(?:white\s+blood\s+cells?|wbc|leucocit[oa]s?)[a-z\s]*[:\s]+([\d.,]+)"],
    "rbc":              [r"(?:red\s+blood\s+cells?|rbc|eritrocit[oa]s?|glóbulos\s+rojos)[a-z\s]*[:\s]+([\d.,]+)"],
    "platelets":        [r"(?:platelets?|plaquetas?)[a-z\s]*[:\s]+([\d.,]+)"],
    "mcv":              [r"\bvcm\b[a-z\s]*[:\s]+([\d.,]+)", r"\bmcv\b[a-z\s]*[:\s]+([\d.,]+)"],
    "iron":             [r"(?:serum\s+)?(?:iron|hierro\s+s[eé]rico|hierro)[a-z\s]*[:\s]+([\d.,]+)", r"\bfe\b[a-z\s]*[:\s]+([\d.,]+)"],
    "ferritin":         [r"ferritin[ae]?[a-z\s]*[:\s]+([\d.,]+)"],
    "transferrin_sat":  [r"(?:transferrin\s+saturation|saturaci[oó]n\s+de\s+transferrina)[a-z\s]*[:\s]+([\d.,]+)"],
    "glucose":          [r"(?:glucose|glucemia|gluc[oó]sa)\s*(?:en\s+ayunas)?[a-z\s]*[:\s]+([\d.,]+)"],
    "creatinine":       [r"creatinin[ae]?[a-z\s]*[:\s]+([\d.,]+)"],
    "urea":             [r"\burea\b[a-z\s]*[:\s]+([\d.,]+)", r"\bbun\b[a-z\s]*[:\s]+([\d.,]+)"],
    "sodium":           [r"(?:sodium|sodio|na\+?)\s*[:\s]+([\d.,]+)"],
    "potassium":        [r"(?:potassium|potasio|k\+?)\s*[:\s]+([\d.,]+)"],
    "cholesterol_total":[r"(?:total\s+cholesterol|colesterol\s+total|colesterol\s+t)[a-z\s]*[:\s]+([\d.,]+)"],
    "ldl":              [r"(?:ldl[\s\-]?c(?:holesterol)?|ldl)[a-z\s]*[:\s]+([\d.,]+)"],
    "hdl":              [r"(?:hdl[\s\-]?c(?:holesterol)?|hdl)[a-z\s]*[:\s]+([\d.,]+)"],
    "triglycerides":    [r"(?:triglycerides?|triglic[eé]ridos?|tg)\s*[:\s]+([\d.,]+)"],
    "ast":              [r"(?:ast|tgo|aspartato\s+aminotransferasa)[a-z\s]*[:\s]+([\d.,]+)"],
    "alt":              [r"(?:alt|tgp|alanina\s+aminotransferasa)[a-z\s]*[:\s]+([\d.,]+)"],
    "ggt":              [r"\bggt\b[a-z\s]*[:\s]+([\d.,]+)", r"gamma[\s\-]?gt[a-z\s]*[:\s]+([\d.,]+)"],
    "bilirubin_total":  [r"(?:bilirubin\s+total|bilirrubina\s+total)[a-z\s]*[:\s]+([\d.,]+)"],
    "tsh":              [r"\btsh\b[a-z\s]*[:\s]+([\d.,]+)"],
    "t4_free":          [r"(?:t4\s+free|t4\s+libre|free\s+t4)[a-z\s]*[:\s]+([\d.,]+)"],
    "vitamin_d":        [r"(?:vitamin\s+d[\s\-]?(?:25[\s\-]?oh)?|vitamina\s+d|25[\s\-]?oh[\s\-]?d)[a-z\s]*[:\s]+([\d.,]+)"],
    "vitamin_b12":      [r"(?:vitamin\s+b[\s\-]?12|vitamina\s+b[\s\-]?12|cobalamina|b12)[a-z\s]*[:\s]+([\d.,]+)"],
    "folate":           [r"(?:folic\s+acid|folate|[aá]cido\s+f[oó]lico|folatos?)[a-z\s]*[:\s]+([\d.,]+)"],
    "zinc":             [r"\bzinc\b[a-z\s]*[:\s]+([\d.,]+)"],
    "magnesium":        [r"(?:magnesium|magnesio|mg\+?)\s*[:\s]+([\d.,]+)"],
    "calcium":          [r"(?:calcium|calcio|ca\+?)\s*[:\s]+([\d.,]+)"],
    "insulin":          [r"(?:insulin[ae]?|insulina?)[a-z\s]*[:\s]+([\d.,]+)"],
}


def _determine_status(key: str, value: float) -> str:
    ref = _REFERENCE_RANGES.get(key)
    if not ref:
        return "unknown"
    lo = ref.get("ref_min")
    hi = ref.get("ref_max")
    if lo is not None and value < lo:
        return "critical_low" if value < lo * 0.7 else "low"
    if hi is not None and value > hi:
        return "critical_high" if value > hi * 1.5 else "high"
    return "normal"


def _make_marker(key: str, value: float, unit: str | None = None) -> dict[str, Any]:
    ref = _REFERENCE_RANGES.get(key, {})
    status = _determine_status(key, value)
    return {
        "value": round(value, 3),
        "unit": unit or ref.get("unit", ""),
        "ref_min": ref.get("ref_min"),
        "ref_max": ref.get("ref_max"),
        "status": status,
        "display_name": ref.get("display_name", key.replace("_", " ").title()),
        "category": ref.get("category", "other"),
    }


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def extract_text_from_pdf(file_bytes: bytes) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(file_bytes))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages).strip()
    except Exception as exc:
        logger.warning("PDF text extraction failed: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# LLM parsing
# ---------------------------------------------------------------------------

_LLM_SYSTEM = """You are a medical lab report parser. Extract blood test values from the text.
Return ONLY a valid JSON object. Keys are snake_case biomarker names (e.g. "hemoglobin", "ldl", "vitamin_d").
Each value is an object with: value (number), unit (string), ref_min (number|null), ref_max (number|null), display_name (string).
Include analysis_date (YYYY-MM-DD string or null) and lab_name (string or null) at the top level.
Only include markers explicitly present in the text. No markdown, no explanation, just JSON."""


async def _parse_with_llm(text: str) -> dict[str, Any] | None:
    try:
        from app.core.config import get_settings
        settings = get_settings()
        if not settings.nlp_enabled or not settings.openai_api_key:
            return None

        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _LLM_SYSTEM},
                {"role": "user", "content": f"Lab report:\n\n{text[:6000]}"},
            ],
            temperature=0,
            max_tokens=2000,
        )
        raw = response.choices[0].message.content or ""
        # Strip markdown code fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw.strip())
        return json.loads(raw)
    except Exception as exc:
        logger.warning("LLM blood analysis parsing failed: %s", exc)
        return None


async def _parse_image_with_llm(file_bytes: bytes, mime_type: str) -> dict[str, Any] | None:
    try:
        from app.core.config import get_settings
        settings = get_settings()
        if not settings.nlp_enabled or not settings.openai_api_key:
            return None

        b64 = base64.b64encode(file_bytes).decode()
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": _LLM_SYSTEM + "\n\nExtract from this lab report image:"},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}", "detail": "high"}},
                ],
            }],
            temperature=0,
            max_tokens=2000,
        )
        raw = response.choices[0].message.content or ""
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw.strip())
        return json.loads(raw)
    except Exception as exc:
        logger.warning("LLM image blood analysis parsing failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Regex parsing fallback
# ---------------------------------------------------------------------------

def _parse_with_regex(text: str) -> dict[str, Any]:
    text_lower = text.lower()
    values: dict[str, Any] = {}
    for key, patterns in _REGEX_PATTERNS.items():
        for pattern in patterns:
            m = re.search(pattern, text_lower)
            if m:
                raw_val = m.group(1).replace(",", ".")
                try:
                    val = float(raw_val)
                    values[key] = _make_marker(key, val)
                    break
                except ValueError:
                    continue
    return values


def _extract_date(text: str) -> date | None:
    patterns = [
        r"(\d{2})[/\-](\d{2})[/\-](\d{4})",  # DD/MM/YYYY or DD-MM-YYYY
        r"(\d{4})[/\-](\d{2})[/\-](\d{2})",  # YYYY-MM-DD
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            groups = m.groups()
            try:
                if len(groups[0]) == 4:
                    return date(int(groups[0]), int(groups[1]), int(groups[2]))
                return date(int(groups[2]), int(groups[1]), int(groups[0]))
            except ValueError:
                continue
    return None


def _extract_lab_name(text: str) -> str | None:
    patterns = [
        r"(?:laboratorio|laboratory|lab)\s+([A-Z][A-Za-z\s&.]{2,50})",
        r"^([A-Z][A-Za-z\s&.]{5,50})\s*\n",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.MULTILINE | re.IGNORECASE)
        if m:
            return m.group(1).strip()[:100]
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class ParsedBloodAnalysis:
    values: dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""
    analysis_date: date | None = None
    lab_name: str | None = None
    parsing_method: str = "regex"
    ai_summary: str | None = None


def _normalize_llm_output(llm_data: dict[str, Any]) -> tuple[dict[str, Any], date | None, str | None]:
    """Convert LLM JSON output into our canonical marker format."""
    values: dict[str, Any] = {}
    analysis_date: date | None = None
    lab_name: str | None = llm_data.get("lab_name")

    raw_date = llm_data.get("analysis_date")
    if raw_date:
        try:
            from datetime import date as _date
            parts = raw_date.split("-")
            analysis_date = _date(int(parts[0]), int(parts[1]), int(parts[2]))
        except Exception:
            pass

    for key, raw in llm_data.items():
        if key in ("analysis_date", "lab_name") or not isinstance(raw, dict):
            continue
        if "value" not in raw:
            continue
        try:
            val = float(raw["value"])
        except (TypeError, ValueError):
            continue
        unit = raw.get("unit") or _REFERENCE_RANGES.get(key, {}).get("unit", "")
        ref = _REFERENCE_RANGES.get(key, {})
        ref_min = raw.get("ref_min") or ref.get("ref_min")
        ref_max = raw.get("ref_max") or ref.get("ref_max")
        display_name = raw.get("display_name") or ref.get("display_name") or key.replace("_", " ").title()
        category = ref.get("category", "other")
        values[key] = {
            "value": round(val, 3),
            "unit": unit,
            "ref_min": ref_min,
            "ref_max": ref_max,
            "status": _determine_status(key, val),
            "display_name": display_name,
            "category": category,
        }

    return values, analysis_date, lab_name


async def analyze_file(file_bytes: bytes, mime_type: str, filename: str) -> ParsedBloodAnalysis:
    """Main entry point: extract text, parse, return structured analysis."""
    result = ParsedBloodAnalysis(raw_text="", parsing_method="regex")

    is_image = mime_type.startswith("image/")
    is_pdf = mime_type == "application/pdf" or filename.lower().endswith(".pdf")

    if is_image:
        llm_data = await _parse_image_with_llm(file_bytes, mime_type)
        if llm_data:
            values, analysis_date, lab_name = _normalize_llm_output(llm_data)
            result.values = values
            result.analysis_date = analysis_date
            result.lab_name = lab_name
            result.parsing_method = "llm"
            result.raw_text = "[Extracted from image via LLM]"
            return result
        result.raw_text = "[Image upload — LLM not configured. Please use PDF or enter values manually.]"
        return result

    if is_pdf:
        text = extract_text_from_pdf(file_bytes)
        result.raw_text = text
        if not text:
            result.raw_text = "[Could not extract text from PDF — try a higher-quality scan.]"
            return result

        # Try LLM first
        llm_data = await _parse_with_llm(text)
        if llm_data:
            values, analysis_date, lab_name = _normalize_llm_output(llm_data)
            result.values = values
            result.analysis_date = analysis_date
            result.lab_name = lab_name
            result.parsing_method = "llm"
            return result

        # Regex fallback
        result.values = _parse_with_regex(text)
        result.analysis_date = _extract_date(text)
        result.lab_name = _extract_lab_name(text)
        result.parsing_method = "regex"
        return result

    result.raw_text = f"[Unsupported file type: {mime_type}]"
    return result
