import asyncio
from functools import lru_cache

import cv2
import httpx
import numpy as np
from paddleocr import PaddleOCR

from app.config import settings


class RecognitionError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _ocr() -> PaddleOCR:
    return PaddleOCR(use_angle_cls=True, lang=settings.ocr_lang, show_log=False)


async def recognize_screenshot(url: str) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url)
        response.raise_for_status()

    image = cv2.imdecode(np.frombuffer(response.content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise RecognitionError("VK вернул файл, который не удалось открыть как изображение.")

    result = await asyncio.to_thread(_ocr().ocr, image, cls=True)
    lines: list[str] = []
    for page in result or []:
        for item in page or []:
            if len(item) >= 2 and isinstance(item[1], (list, tuple)):
                text, confidence = item[1][0], float(item[1][1])
                if confidence >= 0.45 and str(text).strip():
                    lines.append(str(text).strip())

    recognized = "\n".join(lines)
    if len(recognized) < 5:
        raise RecognitionError(
            "❌ На скриншоте распознано слишком мало текста. Пришлите чёткий скрин целиком: "
            "должны быть видны команды/игроки, турнир, дата и коэффициенты."
        )
    return recognized


async def make_forecast(recognized_text: str) -> str:
    system_prompt = """
Ты — профессиональный аналитик спортивных событий. Тебе передают сырой OCR-текст со скриншота букмекера.
1. Восстанови вид спорта, турнир, участников, дату и доступные коэффициенты.
2. Не выдумывай данные, которых нет в тексте.
3. Если данных недостаточно, прямо откажись от ставки.
4. Выбери наиболее обоснованный исход из показанных рынков.

ОБЯЗАТЕЛЬНЫЙ ФОРМАТ ОТВЕТА (строго соблюдай пустые строки между блоками для удобного чтения):

🏆 Событие: [Вид спорта, турнир, команды]

🔥 СТАВКА: [Конкретный исход, на который нужно ставить, и коэффициент]
📊 Вероятность захода: [Оценка в процентах, например 75%]

✅ Почему такой выбор:
• [Короткий аргумент 1]
• [Короткий аргумент 2]

⚠️ РИСКИ:
• [Риск 1]
• [Риск 2]

📌 Помни: это вероятностная оценка, а не 100% гарантия результата.
""".strip()
    payload = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"OCR-текст:\n{recognized_text[:12000]}"},
        ],
        "temperature": 0.2,
        "max_tokens": 1000,
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
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError, AttributeError) as error:
        raise RuntimeError("DeepSeek вернул ответ неизвестного формата.") from error
