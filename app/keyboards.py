from vkbottle import Callback, Keyboard, KeyboardButtonColor, OpenLink, Text


def registration_keyboard(link: str, is_admin: bool = False) -> str:
    keyboard = Keyboard(inline=True)
    if link:
        keyboard.add(OpenLink(link, "ПРОЙДИ РЕГИСТРАЦИЮ И ПОЛУЧИ ДОСТУП"))
        keyboard.row()
    keyboard.add(
        Callback("Отправить скриншот 📸", payload={"action": "registration_help"}),
        color=KeyboardButtonColor.PRIMARY,
    )
    if is_admin:
        keyboard.row()
        keyboard.add(Text("Админ-панель ⚙️"), color=KeyboardButtonColor.SECONDARY)
    return keyboard.get_json()


def forecast_keyboard(is_admin: bool = False) -> str:
    keyboard = Keyboard(inline=True)
    keyboard.add(
        Text("ПОЛУЧИТЬ ПРОГНОЗ ⚽", payload={"action": "forecast"}),
        color=KeyboardButtonColor.POSITIVE,
    )
    if is_admin:
        keyboard.row()
        keyboard.add(Text("Админ-панель ⚙️"), color=KeyboardButtonColor.SECONDARY)
    return keyboard.get_json()


def moderation_keyboard(registration_id: int) -> str:
    keyboard = Keyboard(inline=True)
    keyboard.add(
        Callback(
            "Подтвердить ✅",
            payload={"action": "approve", "registration_id": registration_id},
        ),
        color=KeyboardButtonColor.POSITIVE,
    )
    keyboard.add(
        Callback(
            "Отклонить ❌",
            payload={"action": "reject", "registration_id": registration_id},
        ),
        color=KeyboardButtonColor.NEGATIVE,
    )
    return keyboard.get_json()

def browsing_moderation_keyboard(registration_id: int, offset: int, total: int) -> str:
    keyboard = Keyboard(inline=True)
    keyboard.add(
        Callback(
            "Подтвердить ✅",
            payload={"action": "approve", "registration_id": registration_id, "offset": offset},
        ),
        color=KeyboardButtonColor.POSITIVE,
    )
    keyboard.add(
        Callback(
            "Отклонить ❌",
            payload={"action": "reject", "registration_id": registration_id, "offset": offset},
        ),
        color=KeyboardButtonColor.NEGATIVE,
    )
    if total > 1:
        keyboard.row()
        next_offset = (offset + 1) % total
        keyboard.add(
            Callback(
                f"Далее ➡️ ({offset + 1}/{total})",
                payload={"action": "next_pending", "offset": next_offset},
            ),
            color=KeyboardButtonColor.SECONDARY,
        )
    return keyboard.get_json()


def admin_keyboard() -> str:
    keyboard = Keyboard(inline=True)
    keyboard.add(Text("Заявки 📝", payload={"action": "admin_pending"}), color=KeyboardButtonColor.PRIMARY)
    keyboard.row()
    keyboard.add(Text("Текст оффера", payload={"action": "admin_offer_text"}))
    keyboard.row()
    keyboard.add(Text("Фото оффера", payload={"action": "admin_offer_photo"}))
    keyboard.row()
    keyboard.add(Text("Ссылка регистрации", payload={"action": "admin_offer_link"}))
    keyboard.row()
    keyboard.add(Text("Рассылка", payload={"action": "admin_broadcast"}))
    return keyboard.get_json()

def admin_cancel_keyboard() -> str:
    keyboard = Keyboard(inline=True)
    keyboard.add(
        Callback("Назад 🔙", payload={"action": "admin_cancel"}),
        color=KeyboardButtonColor.SECONDARY,
    )
    return keyboard.get_json()
