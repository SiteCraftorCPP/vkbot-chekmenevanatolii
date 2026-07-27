import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import func, select
from vkbottle import Bot, GroupEventType, GroupTypes
from vkbottle.bot import Message

from app.analysis import RecognitionError, make_forecast, recognize_screenshot
from app.config import settings
from app.db import create_tables, session_scope
from app.keyboards import (
    admin_cancel_keyboard,
    admin_keyboard,
    browsing_moderation_keyboard,
    forecast_keyboard,
    moderation_keyboard,
    registration_keyboard,
)
from app.models import AccessStatus, BotSetting, Forecast, Registration, User

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
bot = Bot(settings.vk_bot_token)

DEFAULT_OFFER = (
    "👋 Привет!\n\n"
    "Единственный в мире бот на основе искуственного интеллекта, который выдаёт бесплатные прогнозы на спортивные события с проходимостью 999999% 🔥\n\n"
    "Чтобы получить доступ к функциям, пройдите быструю регистрацию на нашем проекте по кнопке ниже 👇"
)


def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


async def touch_user(user_id: int) -> User:
    async with session_scope() as session:
        user = await session.get(User, user_id)
        if user is None:
            user = User(vk_id=user_id)
            session.add(user)
        else:
            user.last_seen_at = datetime.now(UTC)
        await session.flush()
        return user


async def set_state(user_id: int, state: str | None) -> None:
    async with session_scope() as session:
        user = await session.get(User, user_id)
        if user is None:
            user = User(vk_id=user_id, state=state)
            session.add(user)
        else:
            user.state = state


async def get_setting(key: str, default: str = "") -> str:
    async with session_scope() as session:
        setting = await session.get(BotSetting, key)
        return setting.value if setting else default


async def save_setting(key: str, value: str) -> None:
    async with session_scope() as session:
        setting = await session.get(BotSetting, key)
        if setting is None:
            session.add(BotSetting(key=key, value=value))
        else:
            setting.value = value


def get_photo(message: Message) -> tuple[str, str] | None:
    for attachment in message.attachments or []:
        photo = getattr(attachment, "photo", None)
        if photo is None or not photo.sizes:
            continue
        largest = max(photo.sizes, key=lambda size: (size.width or 0) * (size.height or 0))
        token = f"photo{photo.owner_id}_{photo.id}"
        if photo.access_key:
            token += f"_{photo.access_key}"
        return str(largest.url), token
    return None


def get_attachments(message: Message) -> str:
    tokens: list[str] = []
    for attachment in message.attachments or []:
        item = getattr(attachment, attachment.type.value, None)
        if item is None:
            continue
        token = f"{attachment.type.value}{item.owner_id}_{item.id}"
        if getattr(item, "access_key", None):
            token += f"_{item.access_key}"
        tokens.append(token)
    return ",".join(tokens)


async def show_entrypoint(message: Message) -> None:
    user = await touch_user(message.from_id)
    if user.access_status == AccessStatus.APPROVED:
        await message.answer(
            "✅ Доступ подтверждён!\n\nНажмите кнопку ниже и пришлите скриншот спортивного события, чтобы нейросеть выдала прогноз 🤖",
            keyboard=forecast_keyboard(is_admin(message.from_id)),
        )
        return
    if user.access_status == AccessStatus.PENDING:
        await message.answer("⏳ Ваш скриншот уже проверяется администратором. Пожалуйста, подождите!")
        return

    offer_text = await get_setting("offer_text", DEFAULT_OFFER)
    offer_link = await get_setting("offer_link")
    offer_photo = await get_setting("offer_photo")
    await message.answer(
        offer_text,
        attachment=offer_photo or None,
        keyboard=registration_keyboard(offer_link, is_admin(message.from_id)),
    )


async def show_pending_registration(peer_id: int, offset: int = 0) -> None:
    async with session_scope() as session:
        total = await session.scalar(
            select(func.count(Registration.id)).where(Registration.status == AccessStatus.PENDING)
        )
        if total == 0:
            await bot.api.messages.send(
                peer_id=peer_id,
                random_id=0,
                message="🎉 Все заявки обработаны! Новых заявок нет.",
            )
            return

        if offset >= total:
            offset = 0

        registration = (
            await session.scalars(
                select(Registration)
                .where(Registration.status == AccessStatus.PENDING)
                .order_by(Registration.created_at)
                .offset(offset)
                .limit(1)
            )
        ).first()

    if registration:
        await bot.api.messages.send(
            peer_id=peer_id,
            random_id=0,
            message=f"Заявка #{registration.id}\n👤 Пользователь: vk.com/id{registration.user_id}",
            attachment=registration.screenshot_attachment,
            keyboard=browsing_moderation_keyboard(registration.id, offset, total),
        )

@bot.on.message(text=["/start", "Начать"])
async def start_handler(message: Message) -> None:
    logger.info("ZASHEL POLZOVATEL S ID: %s", message.from_id)
    await show_entrypoint(message)


@bot.on.message(text=["/admin", "Админ-панель ⚙️", "Админ-панель"])
async def admin_handler(message: Message) -> None:
    await touch_user(message.from_id)
    if not is_admin(message.from_id):
        await message.answer("❌ У вас нет доступа к этой команде.")
        return
    await set_state(message.from_id, None)
    await message.answer("🛠 Админ-панель:", keyboard=admin_keyboard())


async def receive_registration(message: Message, attachment: str) -> None:
    async with session_scope() as session:
        user = await session.get(User, message.from_id, with_for_update=True)
        if user is None:
            user = User(vk_id=message.from_id)
            session.add(user)
            await session.flush()
        if user.access_status == AccessStatus.APPROVED:
            await message.answer("✅ Ваш доступ уже подтверждён!", keyboard=forecast_keyboard(is_admin(message.from_id)))
            return
        if user.access_status == AccessStatus.PENDING:
            await message.answer("⏳ Ваша заявка уже находится на проверке.")
            return
        registration = Registration(
            user_id=message.from_id,
            screenshot_attachment=attachment,
        )
        session.add(registration)
        user.access_status = AccessStatus.PENDING
        user.state = None
        await session.flush()
        registration_id = registration.id

    await message.answer("✅ Скриншот успешно отправлен! Ожидайте подтверждения от администраторов ⏳")
    for admin_id in settings.admin_ids:
        try:
            await bot.api.messages.send(
                peer_id=admin_id,
                random_id=0,
                message=f"🆕 Новая заявка #{registration_id}\n👤 Пользователь: vk.com/id{message.from_id}",
                attachment=attachment,
                keyboard=moderation_keyboard(registration_id),
            )
        except Exception:
            logger.exception("Не удалось уведомить администратора %s", admin_id)


async def process_forecast(message: Message, url: str, attachment: str) -> None:
    await set_state(message.from_id, None)
    await message.answer("🤖 Читаю скриншот и анализирую событие... Это займёт около минуты ⏳")
    recognized = ""
    answer: str | None = None
    error_text: str | None = None
    try:
        recognized = await recognize_screenshot(url)
        answer = await make_forecast(recognized)
        await message.answer(f"🎯 Прогноз готов:\n\n{answer}", keyboard=forecast_keyboard(is_admin(message.from_id)))
    except RecognitionError as error:
        error_text = str(error)
        await message.answer(f"❌ {error_text}", keyboard=forecast_keyboard(is_admin(message.from_id)))
    except httpx.HTTPStatusError as error:
        error_text = f"Ошибка внешнего API: HTTP {error.response.status_code}"
        logger.exception("Ошибка API при обработке прогноза")
        await message.answer(
            "⚠️ Сервис аналитики сейчас перегружен. Пожалуйста, попробуйте чуть позже.",
            keyboard=forecast_keyboard(is_admin(message.from_id)),
        )
    except Exception as error:
        error_text = str(error)
        logger.exception("Не удалось сформировать прогноз")
        await message.answer(
            "❌ К сожалению, мне не удалось прочитать этот скриншот. Попробуйте обрезать лишнее или отправить другой файл.",
            keyboard=forecast_keyboard(is_admin(message.from_id)),
        )
    finally:
        async with session_scope() as session:
            session.add(
                Forecast(
                    user_id=message.from_id,
                    screenshot_attachment=attachment,
                    recognized_text=recognized,
                    answer=answer,
                    error=error_text,
                )
            )


async def handle_admin_input(message: Message, state: str) -> bool:
    if not is_admin(message.from_id):
        return False

    if state == "admin_offer_text":
        if not message.text:
            await message.answer("Нужен текст.")
            return True
        await save_setting("offer_text", message.text)
        await set_state(message.from_id, None)
        await message.answer("Текст сохранён.", keyboard=admin_keyboard())
        return True

    if state == "admin_offer_link":
        link = (message.text or "").strip()
        if not link.startswith(("https://", "http://")):
            await message.answer("Пришлите полную ссылку, начинающуюся с https://")
            return True
        await save_setting("offer_link", link)
        await set_state(message.from_id, None)
        await message.answer("Ссылка сохранена.", keyboard=admin_keyboard())
        return True

    if state == "admin_offer_photo":
        photo = get_photo(message)
        if not photo:
            await message.answer("Пришлите изображение как фото.")
            return True
        await save_setting("offer_photo", photo[1])
        await set_state(message.from_id, None)
        await message.answer("Фото сохранено.", keyboard=admin_keyboard())
        return True

    if state == "admin_broadcast":
        if not message.text and not message.attachments:
            await message.answer("Сообщение рассылки пустое.")
            return True
        await set_state(message.from_id, None)
        async with session_scope() as session:
            user_ids = list((await session.scalars(select(User.vk_id))).all())
        attachments = get_attachments(message)
        sent = 0
        failed = 0
        for user_id in user_ids:
            try:
                await bot.api.messages.send(
                    peer_id=user_id,
                    random_id=0,
                    message=message.text or "",
                    attachment=attachments or None,
                )
                sent += 1
            except Exception:
                failed += 1
                logger.warning("Рассылка пользователю %s не удалась", user_id)
            await asyncio.sleep(0.06)
        await message.answer(
            f"✅ Рассылка завершена: доставлено {sent}.",
            keyboard=admin_keyboard(),
        )
        return True
    return False


@bot.on.message()
async def message_handler(message: Message) -> None:
    user = await touch_user(message.from_id)
    text = (message.text or "").strip()

    admin_actions = {
        "Текст оффера": ("admin_offer_text", "Пришлите новый текст оффера."),
        "Фото оффера": ("admin_offer_photo", "Пришлите новое фото оффера."),
        "Ссылка регистрации": ("admin_offer_link", "Пришлите новую ссылку регистрации."),
        "Рассылка": ("admin_broadcast", "Пришлите сообщение для рассылки всем пользователям."),
    }
    if is_admin(message.from_id) and text == "Заявки 📝":
        await set_state(message.from_id, None)
        await show_pending_registration(message.from_id, 0)
        return

    if is_admin(message.from_id) and text in admin_actions:
        state, prompt = admin_actions[text]
        await set_state(message.from_id, state)
        
        if state == "admin_offer_text":
            current_text = await get_setting("offer_text", DEFAULT_OFFER)
            prompt = f"Текущий текст оффера:\n\n{current_text}\n\n---\n{prompt}"
        elif state == "admin_offer_link":
            current_link = await get_setting("offer_link", "не задана")
            prompt = f"Текущая ссылка:\n{current_link}\n\n---\n{prompt}"
            
        await message.answer(prompt, keyboard=admin_cancel_keyboard())
        return

    if user.state and await handle_admin_input(message, user.state):
        return

    photo = get_photo(message)
    if user.access_status != AccessStatus.APPROVED:
        if photo:
            await receive_registration(message, photo[1])
        else:
            await show_entrypoint(message)
        return

    if text in ["ПОЛУЧИТЬ ПРОГНОЗ ⚽", "ПОЛУЧИТЬ ПРОГНОЗ"]:
        await set_state(message.from_id, "awaiting_forecast")
        await message.answer(
            "Пришлите скриншот спортивного события (без обрезки коэффициентов) 📸"
        )
        return

    if photo:
        await process_forecast(message, photo[0], photo[1])
        return

    await message.answer(
        "👇 Чтобы нейросеть сделала прогноз, нажмите «ПОЛУЧИТЬ ПРОГНОЗ ⚽» и отправьте скриншот.",
        keyboard=forecast_keyboard(is_admin(message.from_id)),
    )


async def answer_callback(event: GroupTypes.MessageEvent) -> None:
    await bot.api.messages.send_message_event_answer(
        event_id=event.object.event_id,
        user_id=event.object.user_id,
        peer_id=event.object.peer_id,
    )


@bot.on.raw_event(GroupEventType.MESSAGE_EVENT, dataclass=GroupTypes.MessageEvent)
async def callback_handler(event: GroupTypes.MessageEvent) -> None:
    payload: dict[str, Any] = event.object.payload or {}
    if isinstance(payload, str):
        payload = json.loads(payload)
    action = payload.get("action")
    user_id = event.object.user_id
    await touch_user(user_id)

    if action == "registration_help":
        await set_state(user_id, "awaiting_registration")
        await answer_callback(event)
        await bot.api.messages.send(
            peer_id=event.object.peer_id,
            random_id=0,
            message="Отлично! Теперь просто отправьте сюда скриншот, подтверждающий вашу регистрацию 📸",
        )
        return

    if action == "admin_cancel":
        await set_state(user_id, None)
        await answer_callback(event)
        await bot.api.messages.send(
            peer_id=event.object.peer_id,
            random_id=0,
            message="Отменено. Возвращаю в админ-панель:",
            keyboard=admin_keyboard()
        )
        return

    if action == "next_pending":
        await answer_callback(event)
        await show_pending_registration(user_id, int(payload.get("offset", 0)))
        return

    if action not in {"approve", "reject"} or not is_admin(user_id):
        await answer_callback(event)
        return

    registration_id = int(payload["registration_id"])
    approved = action == "approve"
    applicant_id: int | None = None
    already_reviewed = False
    async with session_scope() as session:
        registration = await session.scalar(
            select(Registration)
            .where(Registration.id == registration_id)
            .with_for_update()
        )
        if registration is None or registration.status != AccessStatus.PENDING:
            already_reviewed = True
        else:
            applicant_id = registration.user_id
            registration.status = (
                AccessStatus.APPROVED if approved else AccessStatus.REJECTED
            )
            registration.reviewed_by = user_id
            registration.reviewed_at = datetime.now(UTC)
            applicant = await session.get(User, applicant_id, with_for_update=True)
            if applicant is not None:
                applicant.access_status = registration.status
                applicant.state = None

    await answer_callback(event)
    if already_reviewed:
        await bot.api.messages.send(
            peer_id=event.object.peer_id,
            random_id=0,
            message=f"Заявка #{registration_id} уже обработана другим администратором.",
        )
        return

    decision = "✅ ПОДТВЕРЖДЕНА" if approved else "❌ ОТКЛОНЕНА"
    await bot.api.messages.send(
        peer_id=event.object.peer_id,
        random_id=0,
        message=f"Заявка #{registration_id} {decision}.",
    )
    if applicant_id is not None:
        if approved:
            await bot.api.messages.send(
                peer_id=applicant_id,
                random_id=0,
                message="🎉 Ваша регистрация подтверждена! Теперь вам доступен анализ событий.\nНажмите кнопку ниже, чтобы начать.",
                keyboard=forecast_keyboard(is_admin(applicant_id)),
            )
        else:
            link = await get_setting("offer_link")
            await bot.api.messages.send(
                peer_id=applicant_id,
                random_id=0,
                message="К сожалению, ваша регистрация не подтверждена 😔\nУбедитесь, что всё сделали верно, и отправьте новый скриншот.",
                keyboard=registration_keyboard(link, is_admin(applicant_id)),
            )

    # Если админ смотрел заявки через раздел "Заявки", покажем ему следующую
    if "offset" in payload:
        await show_pending_registration(user_id, int(payload["offset"]))


if __name__ == "__main__":
    bot.loop_wrapper.on_startup.append(create_tables())
    bot.run_forever()
