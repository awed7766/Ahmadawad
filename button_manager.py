import copy
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import telegram
from telegram import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

BUTTONS_FILE = Path(__file__).with_name("buttons.json")
BUTTON_NAME, BUTTON_TYPE, BUTTON_VALUE, BUTTON_PARENT = range(4)
SETTINGS_BUTTON, SETTINGS_ACTION, SETTINGS_VALUE = range(4, 7)
BUTTON_TYPES = {"content", "url", "web_app", "inline_query", "copy_text", "shortcut"}


def load_buttons() -> dict[str, dict[str, object]]:
    if not BUTTONS_FILE.exists():
        return {}
    try:
        data = json.loads(BUTTONS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(name): value if isinstance(value, dict) else {"type": "content", "value": str(value)}
        for name, value in data.items()
    }


def save_buttons(buttons: dict[str, dict[str, object]]) -> None:
    BUTTONS_FILE.write_text(json.dumps(buttons, ensure_ascii=False, indent=2), encoding="utf-8")


def valid_name(value: str) -> bool:
    return bool(value) and len(value) <= 50 and len(value.encode("utf-8")) <= 40


def valid_url(value: str, web_app: bool = False) -> bool:
    parsed = urlparse(value.strip())
    if web_app:
        return parsed.scheme == "https" and bool(parsed.netloc)
    return parsed.scheme in {"http", "https", "tg"} and bool(parsed.netloc or parsed.scheme == "tg")


def resolve_button(buttons: dict[str, dict[str, object]], name: str) -> dict[str, object] | None:
    visited = set()
    button = buttons.get(name)
    while isinstance(button, dict) and button.get("type") == "shortcut":
        target = str(button.get("value", ""))
        if not target or target in visited:
            return None
        visited.add(target)
        button = buttons.get(target)
    return button if isinstance(button, dict) else None


def button_children(button: dict[str, object]) -> list[str]:
    children = button.get("children", [])
    return [str(child) for child in children] if isinstance(children, list) else []


def button_settings(button: dict[str, object]) -> dict[str, object]:
    settings = button.setdefault("settings", {})
    if not isinstance(settings, dict):
        settings = {}
        button["settings"] = settings
    settings.setdefault("hidden", False)
    settings.setdefault("alert", "")
    settings.setdefault("password", "")
    settings.setdefault("counter", False)
    settings.setdefault("reply_enabled", False)
    settings.setdefault("clicks", 0)
    settings.setdefault("users", [])
    settings.setdefault("last_clicks", [])
    settings.setdefault("protect_content", False)
    settings.setdefault("parse_mode", "HTML")
    settings.setdefault("icon", "")
    return settings


def is_admin(update: telegram.Update) -> bool:
    user = update.effective_user
    configured_id = os.getenv("TELEGRAM_ADMIN_ID")
    configured_name = os.getenv("TELEGRAM_ADMIN_USERNAME", "mARYAMALJhane").lstrip("@").casefold()
    return bool(user and ((configured_id and str(user.id) == configured_id) or (user.username or "").casefold() == configured_name))


def type_markup() -> InlineKeyboardMarkup:
    labels = [("content", "📝 محتوى"), ("url", "🔗 رابط"), ("web_app", "🌐 Web App"),
              ("inline_query", "🔀 Inline Query"), ("copy_text", "📋 Copy Text"), ("shortcut", "⚡ زر مختصر")]
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=f"bm:type:{kind}")] for kind, label in labels] +
                                [[InlineKeyboardButton("⬅️ إلغاء", callback_data="bm:cancel")]])


def settings_markup() -> InlineKeyboardMarkup:
    rows = [
        [("✏️ تعديل الاسم", "bm:set:rename"), ("🔄 تغيير النوع", "bm:set:type")],
        [("📝 تعديل المحتوى", "bm:set:content"), ("👁 إخفاء/إظهار", "bm:set:hidden")],
        [("🔔 التنبيه", "bm:set:alert"), ("🔐 كلمة المرور", "bm:set:password")],
        [("🛡 حماية المحتوى", "bm:set:protect"), ("📊 الإحصائيات", "bm:set:stats")],
        [("📊 العداد", "bm:set:counter"), ("💬 إعداد الرد", "bm:set:reply")],
        [("📋 نسخ الزر", "bm:set:copy"), ("🗑 حذف الزر", "bm:set:delete")],
        [("⬅️ إغلاق", "bm:cancel")],
    ]
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data) for label, data in row] for row in rows])


def back_markup(prefix: str = "bm:set:back") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data=prefix)]])


def settings_text(name: str, button: dict[str, object]) -> str:
    settings = button_settings(button)
    return (f"⚙️ إعدادات الزر: {name}\n"
            f"النوع: {button.get('type', 'content')}\n"
            f"الحالة: {'مخفي' if settings['hidden'] else 'ظاهر'}\n"
            f"النقرات: {settings['clicks']}\n"
            f"الحماية: {'مفعلة' if settings['protect_content'] else 'معطلة'}")


def button_item(name: str, buttons: dict[str, dict[str, object]]) -> InlineKeyboardButton | None:
    stored = buttons.get(name)
    resolved = resolve_button(buttons, name)
    if not stored or not resolved or button_settings(stored).get("hidden"):
        return None
    label = f"{button_settings(stored).get('icon', '')} {name}".strip()
    value = str(resolved.get("value", "")).strip()
    kind = resolved.get("type", "content")
    if kind == "url" and valid_url(value):
        return InlineKeyboardButton(label, url=value)
    if kind == "web_app" and valid_url(value, True):
        return InlineKeyboardButton(label, web_app=WebAppInfo(url=value))
    if kind == "inline_query":
        return InlineKeyboardButton(label, switch_inline_query=value)
    if kind == "copy_text" and 1 <= len(value) <= 256:
        return InlineKeyboardButton(label, copy_text=CopyTextButton(value))
    callback = f"custom:{name}"
    return InlineKeyboardButton(label, callback_data=callback) if len(callback.encode()) <= 64 else None


def menu_markup() -> InlineKeyboardMarkup:
    buttons = load_buttons()
    children = {child for button in buttons.values() for child in button_children(button)}
    rows = []
    for name in buttons:
        if name not in children and (item := button_item(name, buttons)):
            rows.append([item])
    return InlineKeyboardMarkup(rows)


async def add_start(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update):
        await update.effective_message.reply_text("هذا الأمر للمشرفين فقط.")
        return ConversationHandler.END
    await update.effective_message.reply_text("أرسل اسم الزر الجديد:", reply_markup=back_markup("bm:cancel"))
    return BUTTON_NAME


async def add_name(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if not valid_name(name) or name in load_buttons():
        await update.message.reply_text("الاسم غير صالح أو مستخدم مسبقًا:")
        return BUTTON_NAME
    context.user_data["bm_name"] = name
    await update.message.reply_text("اختر نوع الزر:", reply_markup=type_markup())
    return BUTTON_TYPE


async def add_type(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "bm:cancel":
        return await cancel(update, context)
    kind = query.data.removeprefix("bm:type:")
    if kind not in BUTTON_TYPES:
        return BUTTON_TYPE
    context.user_data["bm_type"] = kind
    prompt = {"content": "أرسل النص أو الوسائط:", "url": "أرسل الرابط:", "web_app": "أرسل رابط https://:",
              "inline_query": "أرسل نص الاستعلام:", "copy_text": "أرسل النص المنسوخ:", "shortcut": "أرسل اسم زر موجود:"}[kind]
    await query.edit_message_text(prompt, reply_markup=back_markup("bm:type:back"))
    return BUTTON_VALUE


async def add_value(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    kind = context.user_data["bm_type"]
    value = (update.message.text or "").strip()
    media_fields = (("photo", "content_photo"), ("video", "content_video"),
                    ("document", "content_document"), ("audio", "content_audio"),
                    ("voice", "content_voice"))
    media = next((getattr(update.message, field) for field, _ in media_fields
                  if getattr(update.message, field, None)), None)
    if kind == "content" and media:
        media_kind = next(media_kind for field, media_kind in media_fields
                          if getattr(update.message, field, None))
        item = media[-1] if media_kind == "content_photo" else media
        context.user_data["bm_value"] = {
            "type": media_kind,
            "value": item.file_id,
            "caption": update.message.caption or "",
        }
        await update.message.reply_text("اختر مكان الزر:", reply_markup=parent_markup(load_buttons()))
        return BUTTON_PARENT
    if kind == "url" and not valid_url(value):
        await update.message.reply_text("الرابط غير صالح:")
        return BUTTON_VALUE
    if kind == "web_app" and not valid_url(value, True):
        await update.message.reply_text("رابط Web App يجب أن يبدأ بـ https://:")
        return BUTTON_VALUE
    buttons = load_buttons()
    if kind == "shortcut" and value not in buttons:
        await update.message.reply_text("اسم الزر غير موجود:")
        return BUTTON_VALUE
    if kind == "copy_text" and not 1 <= len(value) <= 256:
        await update.message.reply_text("نص النسخ يجب أن يكون بين 1 و256 حرفًا:")
        return BUTTON_VALUE
    context.user_data["bm_value"] = {"type": kind, "value": value}
    if kind == "content":
        await update.message.reply_text("اختر مكان الزر:", reply_markup=parent_markup(buttons))
        return BUTTON_PARENT
    return await save_new(update, context, "main")


def parent_markup(buttons: dict[str, dict[str, object]]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("القائمة الرئيسية", callback_data="bm:parent:main")]]
    for name, button in buttons.items():
        if button.get("type") == "content":
            rows.append([InlineKeyboardButton(f"{name} - سطر جديد", callback_data=f"bm:parent:new:{name}"),
                         InlineKeyboardButton(f"{name} - نفس السطر", callback_data=f"bm:parent:same:{name}")])
    rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="bm:parent:back")])
    return InlineKeyboardMarkup(rows)


async def save_new(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE, placement: str) -> int:
    name = context.user_data["bm_name"]
    buttons = load_buttons()
    if name in buttons:
        await update.effective_message.reply_text("اسم الزر مستخدم مسبقًا.")
        return ConversationHandler.END
    buttons[name] = copy.deepcopy(context.user_data["bm_value"])
    if placement != "main":
        mode, parent = placement.split(":", 1)
        buttons[parent].setdefault("children", []).append(name)
        rows = buttons[parent].setdefault("children_rows", [])
        if mode == "same" and rows:
            rows[-1].append(name)
        else:
            rows.append([name])
    save_buttons(buttons)
    clear_draft(context)
    await update.effective_message.reply_text(f"✅ تم إنشاء الزر: {name}", reply_markup=menu_markup())
    return ConversationHandler.END


async def add_parent(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "bm:parent:back":
        await query.edit_message_text("أرسل محتوى الزر من جديد:", reply_markup=back_markup("bm:parent:back"))
        return BUTTON_VALUE
    placement = query.data.removeprefix("bm:parent:")
    if placement == "main":
        return await save_new(update, context, "main")
    mode, parent = placement.split(":", 1)
    if parent not in load_buttons() or parent == context.user_data.get("bm_name"):
        await query.edit_message_text("المكان غير صالح:", reply_markup=parent_markup(load_buttons()))
        return BUTTON_PARENT
    return await save_new(update, context, f"{mode}:{parent}")


async def settings_start(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update):
        await update.effective_message.reply_text("هذا الأمر للمشرفين فقط.")
        return ConversationHandler.END
    await update.effective_message.reply_text("أرسل اسم الزر الذي تريد تعديله:")
    return SETTINGS_BUTTON


async def settings_name(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if name not in load_buttons():
        await update.message.reply_text("الزر غير موجود:")
        return SETTINGS_BUTTON
    context.user_data["bm_settings_name"] = name
    await update.message.reply_text(settings_text(name, load_buttons()[name]), reply_markup=settings_markup())
    return SETTINGS_ACTION


async def settings_action(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    name = context.user_data.get("bm_settings_name")
    buttons = load_buttons()
    button = buttons.get(name)
    if not button:
        return ConversationHandler.END
    action = query.data.removeprefix("bm:set:")
    if query.data == "bm:cancel":
        clear_draft(context)
        await query.edit_message_text("تم إغلاق إعدادات الزر.")
        return ConversationHandler.END
    if action == "back":
        await query.edit_message_text(settings_text(name, button), reply_markup=settings_markup())
        return SETTINGS_ACTION
    if action == "type":
        await query.edit_message_text("اختر النوع الجديد:", reply_markup=type_markup())
        return SETTINGS_ACTION
    if action == "hidden" or action == "counter" or action == "protect":
        settings = button_settings(button)
        key = {"hidden": "hidden", "counter": "counter", "protect": "protect_content"}[action]
        settings[key] = not settings[key]
        save_buttons(buttons)
        await query.edit_message_text(settings_text(name, button), reply_markup=settings_markup())
        return SETTINGS_ACTION
    if action == "stats":
        await query.edit_message_text(settings_text(name, button) + f"\nالمستخدمون: {len(button_settings(button)['users'])}", reply_markup=settings_markup())
        return SETTINGS_ACTION
    prompts = {"rename": "أرسل الاسم الجديد:", "content": "أرسل النص الجديد:", "alert": "أرسل التنبيه أو off:",
               "password": "أرسل كلمة المرور أو off:", "reply": "أرسل on|رسالة الرد أو off:", "copy": "أرسل اسم النسخة:", "delete": "أرسل DELETE للتأكيد:"}
    if action in prompts:
        context.user_data["bm_settings_action"] = action
        await query.edit_message_text(prompts[action], reply_markup=back_markup())
        return SETTINGS_VALUE
    return SETTINGS_ACTION


async def settings_type(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "bm:type:back":
        name = context.user_data.get("bm_settings_name")
        await query.edit_message_text(settings_text(name, load_buttons()[name]), reply_markup=settings_markup())
        return SETTINGS_ACTION
    kind = query.data.removeprefix("bm:type:")
    if kind not in BUTTON_TYPES:
        return SETTINGS_ACTION
    context.user_data["bm_settings_action"] = "type"
    context.user_data["bm_new_type"] = kind
    prompt = {"content": "أرسل النص أو الوسائط الجديدة:", "url": "أرسل الرابط:", "web_app": "أرسل رابط https://:",
              "inline_query": "أرسل نص الاستعلام:", "copy_text": "أرسل نص النسخ:", "shortcut": "أرسل اسم زر موجود:"}[kind]
    await query.edit_message_text(prompt, reply_markup=back_markup())
    return SETTINGS_VALUE


async def settings_value(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = context.user_data["bm_settings_name"]
    action = context.user_data.pop("bm_settings_action")
    value = update.message.text.strip()
    buttons = load_buttons()
    button = buttons.get(name)
    if not button:
        return ConversationHandler.END
    settings = button_settings(button)
    if action == "type":
        new_type = context.user_data.pop("bm_new_type", "")
        if new_type not in BUTTON_TYPES:
            return SETTINGS_ACTION
        if new_type == "web_app" and not valid_url(value, True):
            await update.message.reply_text("رابط Web App يجب أن يبدأ بـ https://:")
            context.user_data["bm_settings_action"] = action
            context.user_data["bm_new_type"] = new_type
            return SETTINGS_VALUE
        if new_type == "url" and not valid_url(value):
            await update.message.reply_text("الرابط غير صالح:")
            context.user_data["bm_settings_action"] = action
            context.user_data["bm_new_type"] = new_type
            return SETTINGS_VALUE
        if new_type == "shortcut" and value not in buttons:
            await update.message.reply_text("اسم الزر غير موجود:")
            context.user_data["bm_settings_action"] = action
            context.user_data["bm_new_type"] = new_type
            return SETTINGS_VALUE
        if new_type == "copy_text" and not 1 <= len(value) <= 256:
            await update.message.reply_text("نص النسخ يجب أن يكون بين 1 و256 حرفًا:")
            context.user_data["bm_settings_action"] = action
            context.user_data["bm_new_type"] = new_type
            return SETTINGS_VALUE
        button["type"], button["value"] = new_type, value
        button.pop("caption", None)
    elif action == "content":
        media_fields = (("photo", "content_photo"), ("video", "content_video"),
                        ("document", "content_document"), ("audio", "content_audio"),
                        ("voice", "content_voice"))
        media = next((getattr(update.message, field) for field, _ in media_fields
                      if getattr(update.message, field, None)), None)
        if media:
            media_kind = next(media_kind for field, media_kind in media_fields
                              if getattr(update.message, field, None))
            item = media[-1] if media_kind == "content_photo" else media
            button["type"] = media_kind
            button["value"] = item.file_id
            button["caption"] = update.message.caption or ""
        elif value:
            button["type"], button["value"] = "content", value
        else:
            await update.message.reply_text("أرسل نصًا أو وسائط للمحتوى:")
            context.user_data["bm_settings_action"] = action
            return SETTINGS_VALUE
    elif action == "rename":
        if not valid_name(value) or value in buttons:
            await update.message.reply_text("الاسم غير صالح:")
            context.user_data["bm_settings_action"] = action
            return SETTINGS_VALUE
        buttons[value] = buttons.pop(name)
        for item in buttons.values():
            item["children"] = [value if child == name else child for child in button_children(item)]
        context.user_data["bm_settings_name"] = value
        name = value
    elif action == "content":
        button["type"], button["value"] = "content", value
    elif action == "alert":
        settings["alert"] = "" if value.casefold() == "off" else value
    elif action == "password":
        settings["password"] = "" if value.casefold() == "off" else value
    elif action == "reply":
        parts = value.split("|", 1)
        settings["reply_enabled"] = parts[0].casefold() == "on"
        settings["reply_message"] = parts[1] if len(parts) == 2 else "تم استلام ردك."
    elif action == "copy":
        if not valid_name(value) or value in buttons:
            await update.message.reply_text("اسم النسخة غير صالح:")
            context.user_data["bm_settings_action"] = action
            return SETTINGS_VALUE
        buttons[value] = copy.deepcopy(button)
        name = value
        context.user_data["bm_settings_name"] = value
    elif action == "delete":
        if value != "DELETE":
            await update.message.reply_text("أرسل DELETE للتأكيد:")
            context.user_data["bm_settings_action"] = action
            return SETTINGS_VALUE
        buttons.pop(name)
        for item in buttons.values():
            item["children"] = [child for child in button_children(item) if child != name]
        save_buttons(buttons)
        await update.message.reply_text("تم حذف الزر.")
        return ConversationHandler.END
    save_buttons(buttons)
    await update.message.reply_text(settings_text(name, buttons[name]), reply_markup=settings_markup())
    return SETTINGS_ACTION


def clear_draft(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in ("bm_name", "bm_type", "bm_value", "bm_settings_name", "bm_settings_action"):
        context.user_data.pop(key, None)


async def cancel(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    clear_draft(context)
    await update.effective_message.reply_text("تم الإلغاء.")
    return ConversationHandler.END


def build_button_handlers() -> list[ConversationHandler]:
    add_handler = ConversationHandler(
        entry_points=[CommandHandler("addbutton", add_start)],
        states={
            BUTTON_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_name)],
            BUTTON_TYPE: [CallbackQueryHandler(add_type, pattern=r"^bm:(type:|cancel$)")],
            BUTTON_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_value)],
            BUTTON_PARENT: [CallbackQueryHandler(add_parent, pattern=r"^bm:parent:")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="button_creation",
        persistent=False,
        per_message=False,
    )
    settings_handler = ConversationHandler(
        entry_points=[CommandHandler("buttonsettings", settings_start)],
        states={
            SETTINGS_BUTTON: [MessageHandler(filters.TEXT & ~filters.COMMAND, settings_name)],
            SETTINGS_ACTION: [
                CallbackQueryHandler(settings_action, pattern=r"^bm:set:|^bm:cancel$"),
                CallbackQueryHandler(settings_type, pattern=r"^bm:type:") ,
            ],
            SETTINGS_VALUE: [MessageHandler(
                (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL |
                 filters.AUDIO | filters.VOICE) & ~filters.COMMAND,
                settings_value,
            )],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="button_settings",
        persistent=False,
        per_message=False,
    )
    return [add_handler, settings_handler]


__all__ = ["build_button_handlers", "button_item", "button_children", "button_settings", "load_buttons", "menu_markup", "resolve_button"]
