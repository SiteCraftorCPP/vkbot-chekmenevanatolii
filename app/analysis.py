import asyncio
import random
import re
from functools import lru_cache

import cv2
import httpx
import numpy as np
from paddleocr import PaddleOCR

from app.config import settings

ODDS_PATTERN = re.compile(r"\b\d+[.,]\d{2}\b")
MAX_FORECAST_CHARS = 1000

OPENING_LINES = (
    "Разобрал скрин — вот мой вывод 👇",
    "Собрал данные по матчу, держи прогноз 🔥",
    "Проанализировал событие, вот что вижу 📊",
    "Готов прогноз по этому событию ⚽",
    "Посмотрел линию — вот оптимальный вариант 🎯",
)


class RecognitionError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _ocr() -> PaddleOCR:
    return PaddleOCR(
        use_angle_cls=True,
        lang=settings.ocr_lang,
        show_log=False,
        det_db_box_thresh=0.4,
        det_db_unclip_ratio=1.8,
        rec_batch_num=8,
    )


def _upscale_if_needed(image: np.ndarray, min_width: int = 1400) -> np.ndarray:
    height, width = image.shape[:2]
    if width >= min_width:
        return image
    scale = min_width / width
    return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)


def _enhance_contrast(image: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    lightness, alpha, beta = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
    lightness = clahe.apply(lightness)
    merged = cv2.merge((lightness, alpha, beta))
    return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)


def _sharpen(image: np.ndarray) -> np.ndarray:
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    return cv2.filter2D(image, -1, kernel)


def _preprocess_variants(image: np.ndarray) -> list[np.ndarray]:
    base = _upscale_if_needed(image)
    gray = cv2.cvtColor(base, cv2.COLOR_BGR2GRAY)
    adaptive = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 8
    )
    return [
        base,
        _enhance_contrast(base),
        _sharpen(_enhance_contrast(base)),
        cv2.cvtColor(adaptive, cv2.COLOR_GRAY2BGR),
    ]


def _extract_lines(result: object, min_confidence: float) -> list[tuple[float, float, str, float]]:
    items: list[tuple[float, float, str, float]] = []
    for page in result or []:
        for item in page or []:
            if len(item) < 2 or not isinstance(item[1], (list, tuple)):
                continue
            box, payload = item[0], item[1]
            text = str(payload[0]).strip()
            confidence = float(payload[1])
            if confidence < min_confidence or not text:
                continue
            y_center = sum(point[1] for point in box) / 4
            x_center = sum(point[0] for point in box) / 4
            items.append((y_center, x_center, text, confidence))
    return items


def _merge_ocr_items(items: list[tuple[float, float, str, float]]) -> list[str]:
    if not items:
        return []

    items.sort(key=lambda row: (row[0], row[1]))
    merged: list[str] = []
    current_row: list[str] = []
    current_y = items[0][0]

    for y_center, _x_center, text, _confidence in items:
        if abs(y_center - current_y) > 18:
            if current_row:
                merged.append(" ".join(current_row))
            current_row = [text]
            current_y = y_center
        else:
            current_row.append(text)

    if current_row:
        merged.append(" ".join(current_row))

    unique: list[str] = []
    seen: set[str] = set()
    for line in merged:
        normalized = re.sub(r"\s+", " ", line).strip()
        key = normalized.lower()
        if normalized and key not in seen:
            seen.add(key)
            unique.append(normalized)
    return unique


def _structure_recognized_text(lines: list[str]) -> str:
    odds = sorted({match.replace(",", ".") for line in lines for match in ODDS_PATTERN.findall(line)})
    body = "\n".join(f"• {line}" for line in lines)
    odds_block = ", ".join(odds[:30]) if odds else "не распознаны"
    return (
        "СТРОКИ СО СКРИНШОТА (сверху вниз):\n"
        f"{body}\n\n"
        f"НАЙДЕННЫЕ КОЭФФИЦИЕНТЫ: {odds_block}"
    )


def _run_ocr(image: np.ndarray) -> list[str]:
    all_items: list[tuple[float, float, str, float]] = []
    for variant in _preprocess_variants(image):
        result = _ocr().ocr(variant, cls=True)
        all_items.extend(_extract_lines(result, min_confidence=0.32))
    return _merge_ocr_items(all_items)


async def recognize_screenshot(url: str) -> str:
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.get(url)
        response.raise_for_status()

    image = cv2.imdecode(np.frombuffer(response.content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise RecognitionError(
            "❌ Не удалось открыть изображение. Пришлите скрин как фото, не как файл."
        )

    lines = await asyncio.to_thread(_run_ocr, image)
    recognized = _structure_recognized_text(lines)
    if len("\n".join(lines)) < 8:
        raise RecognitionError(
            "❌ Скрин плохо читается. Пришлите полный скрин события: "
            "команды/игроки, турнир, дата и коэффициенты без обрезки."
        )
    return recognized


def _build_system_prompt() -> str:
    opening_hint = random.choice(OPENING_LINES)
    return f"""
Ты — эксперт по спортивным ставкам. Получаешь OCR-текст со скриншота букмекера.

Задача:
1. Восстанови событие: вид спорта, турнир, участники, время/дата, рынки и коэффициенты.
2. Кратко собери статистику обеих сторон из своих знаний: форма, таблица, серия, очки, H2H — только если команды/игроки узнаваемы.
3. Выбери одну конкретную ставку из рынков, видимых в тексте.
4. Сформулируй живой прогноз без шаблонных повторов.

Правила:
- Не выдумывай коэффициенты и рынки, которых нет в OCR-тексте.
- Если коэффициент не виден — укажи ставку без числа или попроси более чёткий скрин.
- Вероятность успеха прогноза: только от 75% до 100%.
- Ответ строго до {MAX_FORECAST_CHARS} символов.
- Используй эмодзи, но без воды.
- Начни ответ фразой в духе: «{opening_hint}»
- Не используй блоки «Почему такой выбор» и «РИСКИ» в одном и том же виде каждый раз — меняй формулировки.

Формат (компактно, с пустыми строками):
[короткое вступление с эмодзи]
🏆 Событие: ...
📊 Статистика: [2-4 факта по обеим сторонам]
🎯 Ставка: [конкретный исход + коэффициент если есть]
💯 Прогноз верен на: [75-100]%
🔥 Аргумент: [1-2 коротких пункта]
⚠️ Риск: [1 короткий пункт]
""".strip()


def _trim_forecast(text: str) -> str:
    cleaned = text.strip()
    if len(cleaned) <= MAX_FORECAST_CHARS:
        return cleaned
    return cleaned[: MAX_FORECAST_CHARS - 1].rstrip() + "…"


async def make_forecast(recognized_text: str) -> str:
    payload = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": _build_system_prompt()},
            {
                "role": "user",
                "content": (
                    "Посмотри OCR-текст со скриншота спортивного события, "
                    "собери статистику обеих сторон и дай предполагаемую ставку.\n\n"
                    f"{recognized_text[:12000]}"
                ),
            },
        ],
        "temperature": 0.78,
        "max_tokens": 700,
        "presence_penalty": 0.35,
        "frequency_penalty": 0.25,
    }
    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }
    url = f"{settings.deepseek_base_url.rstrip('/')}/chat/completions"
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    try:
        return _trim_forecast(data["choices"][0]["message"]["content"].strip())
    except (KeyError, IndexError, TypeError, AttributeError) as error:
        raise RuntimeError("DeepSeek вернул ответ неизвестного формата.") from error
