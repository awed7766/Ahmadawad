import os
import json
import logging
import re
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from store import build_store_handlers, store_category_start, store_panel_start
from store import category_by_command
import telegram
from telegram import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from auto import (
    build_data_request_handler,
    build_request_settings_handler,
    load_requests,
    request_config_enabled,
)
from button_manager import build_button_handlers
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

BUTTONS_FILE = Path(__file__).with_name("buttons.json")
ADMINS_FILE = Path(__file__).with_name("admins.json")
USERS_FILE = Path(__file__).with_name("users.json")
AUTO_REPLIES_FILE = Path(__file__).with_name("auto_replies.json")
NOTIFICATIONS_FILE = Path(__file__).with_name("notifications.json")
BUTTON_NAME, BUTTON_TYPE, BUTTON_VALUE, BUTTON_PARENT = range(4)
SETTINGS_BUTTON, SETTINGS_ACTION, SETTINGS_VALUE = range(4, 7)
ADMIN_USER, ADMIN_PERMISSIONS = range(7, 9)
AUTO_KEYWORD, AUTO_MATCH, AUTO_RESPONSE, AUTO_OPTIONS = range(9, 13)
BROADCAST_MODE, BROADCAST_MESSAGE, BROADCAST_OPTIONS, BROADCAST_AUDIENCE = range(13, 17)
ADMIN_USERNAME = "mARYAMALJhane"
ALL_PERMISSIONS = {"buttons", "settings", "admins", "cache"}
logger = logging.getLogger(__name__)


def load_buttons() -> dict[str, dict[str, str]]:
    if not BUTTONS_FILE.exists():
        return {}
    try:
        buttons = json.loads(BUTTONS_FILE.read_text(encoding="utf-8"))
        return {
            name: value if isinstance(value, dict) else {"type": "content", "value": value}
            for name, value in buttons.items()
        }
    except (json.JSONDecodeError, OSError):
        return {}


def save_buttons(buttons: dict[str, dict[str, str]]) -> None:
    BUTTONS_FILE.write_text(
        json.dumps(buttons, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_admins() -> dict[str, dict[str, object]]:
    if not ADMINS_FILE.exists():
        return {}
    try:
        data = json.loads(ADMINS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_admins(admins: dict[str, dict[str, object]]) -> None:
    ADMINS_FILE.write_text(json.dumps(admins, ensure_ascii=False, indent=2), encoding="utf-8")


def load_notifications() -> dict[str, bool]:
    if not NOTIFICATIONS_FILE.exists():
        return {"new_user": True}
    try:
        data = json.loads(NOTIFICATIONS_FILE.read_text(encoding="utf-8"))
        return {"new_user": bool(data.get("new_user", True))} if isinstance(data, dict) else {"new_user": True}
    except (json.JSONDecodeError, OSError):
        return {"new_user": True}


def save_notifications(settings: dict[str, bool]) -> None:
    NOTIFICATIONS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def load_auto_replies() -> list[dict[str, object]]:
    if not AUTO_REPLIES_FILE.exists():
        return []
    try:
        data = json.loads(AUTO_REPLIES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_auto_replies(replies: list[dict[str, object]]) -> None:
    AUTO_REPLIES_FILE.write_text(
        json.dumps(replies, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_users() -> dict[str, dict[str, str]]:
    if not USERS_FILE.exists():
        return {}
    try:
        data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


async def record_user(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    users = load_users()
    key = str(user.id)
    today = datetime.now().isoformat(timespec="seconds")
    changed = False
    if key not in users:
        users[key] = {"username": user.username or "", "name": user.full_name,
                      "joined": datetime.now().date().isoformat(), "last_active": today}
        changed = True
    elif users[key].get("last_active", "")[:16] != today[:16]:
        users[key]["last_active"] = today
        changed = True
    if changed:
        USERS_FILE.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")
        if load_notifications().get("new_user", True):
            admin_id = os.getenv("TELEGRAM_ADMIN_ID")
            if admin_id:
                username = f"@{user.username}" if user.username else "بدون معرف"
                try:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=(
                            "👤 عضو جديد دخل البوت\n\n"
                            f"الاسم: {escape(user.full_name)}\n"
                            f"المعرف: {escape(username)}\n"
                            f"الايدي: {user.id}\n"
                            f"إجمالي المستخدمين: {len(users)}"
                        ),
                        parse_mode=telegram.constants.ParseMode.HTML,
                    )
                except telegram.error.TelegramError:
                    pass


def admin_record(update: telegram.Update) -> tuple[str, dict[str, object]] | None:
    user = update.effective_user
    admin_id = os.getenv("TELEGRAM_ADMIN_ID")
    username = (user.username or "").casefold()
    if (admin_id and str(user.id) == admin_id) or username == os.getenv(
        "TELEGRAM_ADMIN_USERNAME", ADMIN_USERNAME
    ).casefold():
        return "owner", {"permissions": list(ALL_PERMISSIONS)}
    record = load_admins().get(str(user.id)) or load_admins().get(username)
    return ("admin", record) if isinstance(record, dict) else None


def has_permission(update: telegram.Update, permission: str) -> bool:
    record = admin_record(update)
    return bool(record and permission in record[1].get("permissions", []))


def permissions_label(permissions: object) -> str:
    if not isinstance(permissions, list):
        return "بدون صلاحيات"
    return ", ".join(str(permission) for permission in permissions) or "بدون صلاحيات"


def normalize_admin_identifier(value: str) -> str | None:
    identifier = value.strip().lstrip("@").casefold()
    if identifier.isdigit():
        return identifier if int(identifier) > 0 else None
    if re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{4,31}", identifier):
        return identifier
    return None


def button_settings(button: dict[str, str]) -> dict[str, object]:
    settings = button.setdefault("settings", {})
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
    settings.setdefault("style", "default")
    settings.setdefault("icon", "")
    settings.setdefault("schedule", "")
    return settings


def record_click(button: dict[str, str], user_id: int) -> dict[str, object]:
    settings = button_settings(button)
    settings["clicks"] = int(settings["clicks"]) + 1
    users = settings["users"]
    if user_id not in users:
        users.append(user_id)
    last_clicks = settings["last_clicks"]
    last_clicks.insert(0, str(user_id))
    del last_clicks[10:]
    return settings


def button_is_visible(button: dict[str, str]) -> bool:
    settings = button_settings(button)
    if settings["hidden"]:
        return False
    schedule = str(settings.get("schedule", "")).strip()
    if not schedule:
        return True
    try:
        start, end = [datetime.strptime(item.strip(), "%H:%M").time() for item in schedule.split("-", 1)]
    except (ValueError, IndexError):
        return True
    now = datetime.now().time()
    return start <= now <= end if start <= end else now >= start or now <= end


def settings_markup(button_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ تعديل الاسم", callback_data="set:rename")],
        [InlineKeyboardButton("🔄 تغيير نوع الزر", callback_data="set:type")],
        [InlineKeyboardButton("📝 إعدادات المحتوى", callback_data="set:content")],
        [InlineKeyboardButton("📋 إدارة الطلبات", callback_data="set:requests")],
        [InlineKeyboardButton("🛡️ حماية المحتوى", callback_data="set:protect")],
        [InlineKeyboardButton("🖥️ طريقة العرض", callback_data="set:display")],
        [InlineKeyboardButton("💬 إعدادات الرد", callback_data="set:reply")],
        [InlineKeyboardButton("🎨 المظهر", callback_data="set:appearance")],
        [InlineKeyboardButton("🔔 تنبيه", callback_data="set:alert")],
        [InlineKeyboardButton("🔐 كلمة مرور", callback_data="set:password")],
        [InlineKeyboardButton("👁 إخفاء/إظهار", callback_data="set:hidden")],
        [InlineKeyboardButton("📊 إحصائيات", callback_data="set:stats")],
        [InlineKeyboardButton("📊 عداد مباشر", callback_data="set:counter")],
        [InlineKeyboardButton("📍 نقل الزر", callback_data="set:move")],
        [InlineKeyboardButton("📋 نسخ الزر", callback_data="set:copy")],
        [InlineKeyboardButton("🗑 حذف الزر", callback_data="set:delete")],
        [InlineKeyboardButton("⬅️ إلغاء", callback_data="set:cancel")],
    ])


def settings_text(name: str, button: dict[str, str]) -> str:
    settings = button_settings(button)
    hidden = "مخفي" if settings["hidden"] else "ظاهر"
    counter = "مفعل" if settings["counter"] else "معطل"
    protection = "مفعل" if settings["protect_content"] else "معطل"
    schedule = settings["schedule"] or "دائمًا"
    return f"إعدادات الزر: {name}\nالحالة: {hidden}\nالعداد المباشر: {counter}\nحماية المحتوى: {protection}\nالجدولة: {schedule}"


def admin_panel_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="admin:stats")],
        [InlineKeyboardButton("📋 طلبات العملاء", callback_data="admin:requests")],
        [InlineKeyboardButton("📨 التواصل والإذاعة", callback_data="admin:broadcast")],
        [InlineKeyboardButton("🧾 إعدادات الطلبات", callback_data="admin:request_settings")],
        [InlineKeyboardButton("🛍 المتجر", callback_data="admin:store")],
        [InlineKeyboardButton("🧩 الأقسام", callback_data="admin:sections")],
        [InlineKeyboardButton("🔔 الإشعارات", callback_data="admin:notifications")],
        [InlineKeyboardButton("➕ إنشاء زر", callback_data="admin:add_button")],
        [InlineKeyboardButton("⚙️ إعدادات زر", callback_data="admin:button_settings")],
        [InlineKeyboardButton("👥 إدارة المشرفين", callback_data="admin:admins")],
        [InlineKeyboardButton("🧹 حذف التخزين المؤقت", callback_data="admin:clear_cache")],
        [InlineKeyboardButton("⬅️ القائمة الرئيسية", callback_data="admin:home")],
    ])


def broadcast_mode_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📨 رسالة مباشرة", callback_data="broadcast:direct")],
        [InlineKeyboardButton("↩️ توجيه رسالة", callback_data="broadcast:forward")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="admin:panel")],
    ])


def broadcast_options_markup(context: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardMarkup:
    options = context.user_data.setdefault("broadcast_options", {
        "pin": False,
        "silent": False,
        "protect": False,
        "preview": True,
    })
    labels = [
        ("pin", "📌 تثبيت الرسالة"),
        ("silent", "🔕 صامت"),
        ("protect", "🔒 حماية المحتوى"),
        ("preview", "🔗 معاينة الرابط"),
    ]
    rows = [[InlineKeyboardButton(
        f"{'✅' if options[key] else '❌'} {label}",
        callback_data=f"broadcastopt:{key}",
    )] for key, label in labels]
    rows.extend([
        [InlineKeyboardButton("🎯 اختيار الجمهور", callback_data="broadcast:audience")],
        [InlineKeyboardButton("📖 مساعدة", callback_data="broadcast:help")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="broadcast:cancel")],
    ])
    return InlineKeyboardMarkup(rows)


def broadcast_audience_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 جميع المستخدمين", callback_data="audience:all")],
        [InlineKeyboardButton("🆕 جديد خلال 7 أيام", callback_data="audience:new7")],
        [InlineKeyboardButton("🆕 جديد خلال 30 يومًا", callback_data="audience:new30")],
        [InlineKeyboardButton("💤 خامل 7 أيام", callback_data="audience:inactive7")],
        [InlineKeyboardButton("💤 خامل 30 يومًا", callback_data="audience:inactive30")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="broadcast:back_options")],
    ])


def notifications_markup() -> InlineKeyboardMarkup:
    enabled = load_notifications().get("new_user", True)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"{'✅' if enabled else '❌'} إشعار عضو جديد",
            callback_data="notify:toggle:new_user",
        )],
        [InlineKeyboardButton("⬅️ لوحة الإدارة", callback_data="admin:panel")],
    ])


def admin_sections_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 المحتوى", callback_data="admin:section:content")],
        [InlineKeyboardButton("🤖 الردود التلقائية", callback_data="admin:auto_replies")],
        [InlineKeyboardButton("⚙️ الإعدادات", callback_data="admin:section:settings")],
        [InlineKeyboardButton("👥 المستخدمون", callback_data="admin:section:users")],
        [InlineKeyboardButton("🔔 الاشتراك", callback_data="admin:section:subscription")],
        [InlineKeyboardButton("💰 المالية", callback_data="admin:section:finance")],
        [InlineKeyboardButton("📨 التواصل", callback_data="admin:section:contact")],
        [InlineKeyboardButton("🖥 النظام والدعم", callback_data="admin:section:system")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="admin:panel")],
    ])


def admin_permissions_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("الأزرار", callback_data="adminperm:buttons")],
        [InlineKeyboardButton("الإعدادات", callback_data="adminperm:settings")],
        [InlineKeyboardButton("الأزرار والإعدادات", callback_data="adminperm:buttons,settings")],
        [InlineKeyboardButton("إدارة المشرفين", callback_data="adminperm:admins")],
        [InlineKeyboardButton("التخزين المؤقت", callback_data="adminperm:cache")],
        [InlineKeyboardButton("كل الصلاحيات", callback_data="adminperm:all")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="admin:admins")],
    ])


def auto_match_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 مطابقة تامة", callback_data="automatch:exact")],
        [InlineKeyboardButton("📦 يحتوي على", callback_data="automatch:contains")],
        [InlineKeyboardButton("▶️ يبدأ بـ", callback_data="automatch:starts")],
        [InlineKeyboardButton("🔤 كلمة كاملة", callback_data="automatch:word")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="autoreply:keywordback")],
    ])


def auto_options_markup(context: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardMarkup:
    options = context.user_data.setdefault("auto_options", {
        "stop": False,
        "disable_web_page_preview": True,
        "protect_content": False,
        "silent": False,
        "spoiler": False,
        "caption_above": False,
        "buttons": [],
    })
    labels = [
        ("stop", "⏹ توقف بعد الرد"),
        ("disable_web_page_preview", "🔗 معاينة الرابط"),
        ("protect_content", "🔒 حماية المحتوى"),
        ("silent", "🔕 صامت"),
        ("spoiler", "🫣 سبويلر"),
        ("caption_above", "⬆️ التعليق فوق"),
    ]
    rows = []
    for key, label in labels:
        enabled = not options[key] if key == "disable_web_page_preview" else options[key]
        rows.append([InlineKeyboardButton(
            f"{'✅' if enabled else '❌'} {label}", callback_data=f"autoopt:toggle:{key}"
        )])
    for name in context.user_data.get("auto_button_names", []):
        selected = name in options["buttons"]
        rows.append([InlineKeyboardButton(
            f"{'✅' if selected else '⬜'} {name}", callback_data=f"autoopt:button:{name}"
        )])
    rows.extend([
        [InlineKeyboardButton("💾 حفظ الرد", callback_data="autoopt:save")],
        [InlineKeyboardButton("⬅️ رجوع إلى نص الرد", callback_data="autoreply:back")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="auto:cancel")],
    ])
    return InlineKeyboardMarkup(rows)


def auto_response_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ رجوع", callback_data="autoreply:back")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="auto:cancel")],
    ])


def auto_replies_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إضافة رد تلقائي", callback_data="auto:add")],
        [InlineKeyboardButton("📋 عرض الردود", callback_data="auto:list")],
        [InlineKeyboardButton("⬅️ لوحة الإدارة", callback_data="admin:panel")],
    ])


def auto_replies_list_markup(replies: list[dict[str, object]]) -> InlineKeyboardMarkup:
    rows = []
    for index, item in enumerate(replies):
        status = "✅" if item.get("enabled", True) else "❌"
        rows.append([InlineKeyboardButton(
            f"{status} {item.get('keyword', '')}", callback_data=f"auto:toggle:{index}"
        )])
        rows.append([
            InlineKeyboardButton("✏️ تعديل", callback_data=f"auto:edit:{index}"),
            InlineKeyboardButton("🗑 حذف", callback_data=f"auto:delete:{index}"),
        ])
    rows.append([InlineKeyboardButton("➕ إضافة رد", callback_data="auto:add")])
    rows.append([InlineKeyboardButton("⬅️ لوحة الإدارة", callback_data="admin:panel")])
    return InlineKeyboardMarkup(rows)


def button_children(button: dict[str, str]) -> list[str]:
    children = button.get("children", [])
    return children if isinstance(children, list) else []


def button_child_rows(button: dict[str, str]) -> list[list[str]]:
    rows = button.get("children_rows", [])
    return rows if isinstance(rows, list) else []


def interpolate(
    text: str,
    update: telegram.Update,
    bot_username: str = "",
    points: int = 0,
) -> str:
    user = update.effective_user
    username = f"@{user.username}" if user.username else "بدون معرف"
    name_user = f'<a href="tg://user?id={user.id}">{escape(user.full_name)}</a>'
    invite_link = f"https://t.me/{bot_username}?start={user.id}" if bot_username else ""
    values = {
        "#name_user": name_user,
        "#username": escape(username),
        "#name": escape(user.full_name),
        "#id": str(user.id),
        "#points": str(points),
        "#invitelink": invite_link,
    }
    for placeholder, value in values.items():
        text = text.replace(placeholder, value)
    return text


def button_item(name: str, buttons: dict[str, dict[str, str]]) -> InlineKeyboardButton | None:
    stored_button = buttons.get(name)
    button = resolve_button(buttons, name)
    if not stored_button or not button or not button_is_visible(stored_button):
        return None
    label = f"{button_settings(stored_button).get('icon', '')} {name}".strip()
    button_type = button.get("type", "content")
    if button_type == "url":
        return InlineKeyboardButton(label, url=button["value"])
    if button_type == "web_app":
        return InlineKeyboardButton(label, web_app=WebAppInfo(url=button["value"]))
    if button_type == "inline_query":
        return InlineKeyboardButton(label, switch_inline_query=button["value"])
    if button_type == "copy_text":
        return InlineKeyboardButton(label, copy_text=CopyTextButton(button["value"]))
    return InlineKeyboardButton(
        label,
        callback_data=(
            f"request:start:{name}" if request_config_enabled(name)
            else f"custom:{name}"
        ),
    )


def submenu_markup(
    buttons: dict[str, dict[str, str]],
    names: list[str],
    rows: list[list[str]] | None = None,
) -> InlineKeyboardMarkup:
    keyboard = []
    display_rows = rows or [[name] for name in names]
    for row in display_rows:
        items = [item for name in row if (item := button_item(name, buttons))]
        if items:
            keyboard.append(items)
    keyboard.append([InlineKeyboardButton("⬅️ القائمة الرئيسية", callback_data="home")])
    return InlineKeyboardMarkup(keyboard)


def resolve_button(buttons: dict[str, dict[str, str]], name: str) -> dict[str, str] | None:
    visited = set()
    button = buttons.get(name)
    while button and button.get("type") == "shortcut":
        target = button.get("value", "")
        if target in visited:
            return None
        visited.add(target)
        button = buttons.get(target)
    return button


def is_admin(update: telegram.Update) -> bool:
    return admin_record(update) is not None


def menu_markup() -> InlineKeyboardMarkup:
    buttons = load_buttons()
    child_names = {
        child
        for button in buttons.values()
        for child in button_children(button)
    }
    keyboard = []
    for name in buttons:
        if not button_is_visible(buttons[name]):
            continue
        if name in child_names:
            continue
        item = button_item(name, buttons)
        if item:
            keyboard.append([item])
    keyboard.extend([
    ])
    return InlineKeyboardMarkup(keyboard)


def user_menu_markup(update: telegram.Update) -> InlineKeyboardMarkup:
    markup = menu_markup()
    rows = [list(row) for row in markup.inline_keyboard]
    if is_admin(update):
        rows.insert(0, [InlineKeyboardButton("🛠 لوحة الإدارة", callback_data="admin:panel")])
    return InlineKeyboardMarkup(rows)


def button_types_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 محتوى", callback_data="newtype:content")],
        [InlineKeyboardButton("🔗 رابط", callback_data="newtype:url")],
        [InlineKeyboardButton("🌐 Web App", callback_data="newtype:web_app")],
        [InlineKeyboardButton("🔀 Inline Query", callback_data="newtype:inline_query")],
        [InlineKeyboardButton("📋 Copy Text", callback_data="newtype:copy_text")],
        [InlineKeyboardButton("⚡ زر مختصر", callback_data="newtype:shortcut")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="add:back")],
    ])


def change_button_types_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 رابط", callback_data="changetype:url")],
        [InlineKeyboardButton("🌐 Web App", callback_data="changetype:web_app")],
        [InlineKeyboardButton("🔀 Inline Query", callback_data="changetype:inline_query")],
        [InlineKeyboardButton("📋 Copy Text", callback_data="changetype:copy_text")],
        [InlineKeyboardButton("⚡ زر مختصر", callback_data="changetype:shortcut")],
        [InlineKeyboardButton("📝 محتوى", callback_data="changetype:content")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="set:back")],
    ])


def valid_button_url(value: str) -> bool:
    return value.startswith(("https://", "http://", "tg://"))


def back_to_settings_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ رجوع إلى إعدادات الزر", callback_data="set:back")],
    ])


def back_to_button_types_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ رجوع لاختيار النوع", callback_data="add:types")],
    ])


async def start(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await record_user(update, context)
    if context.args and (
        context.args[0].startswith("store_category_")
        or context.args[0].startswith("store_command_")
    ):
        if context.args[0].startswith("store_command_"):
            category_name = category_by_command(context.args[0].removeprefix("store_command_"))
            if category_name:
                context.args[0] = f"store_category_{category_name}"
        await store_category_start(update, context)
        return
    await update.message.reply_text(
        "هلا فيك! اختر من القائمة:",
        reply_markup=user_menu_markup(update),
    )


async def button_click(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await record_user(update, context)
    buttons = load_buttons()
    clicked_name = query.data.removeprefix("custom:") if query.data.startswith("custom:") else ""
    clicked_button = resolve_button(buttons, clicked_name) if clicked_name else None
    clicked_settings = button_settings(clicked_button) if clicked_button else {}
    await query.answer(
        text=str(clicked_settings.get("alert", "")) or None,
        show_alert=bool(clicked_settings.get("alert")),
    )

    responses = {
        "services": "الخدمات المتاحة:\n- الرد التلقائي\n- المساعدة",
        "help": "أرسل أي رسالة، وسأرد عليك تلقائياً. استخدم /start لعرض القائمة.",
    }
    if query.data.startswith("custom:"):
        button_name = query.data.removeprefix("custom:")
        button = resolve_button(load_buttons(), button_name)
        if button:
            settings = button_settings(button)
            record_click(button, update.effective_user.id)
            buttons[button_name]["settings"] = settings
            save_buttons(buttons)
            password = str(settings.get("password", ""))
            if password and button_name not in context.user_data.get("unlocked_buttons", set()):
                context.user_data["pending_password_button"] = button_name
                await query.message.reply_text("🔐 أرسل كلمة المرور لفتح هذا الزر:")
                return
        if button and button.get("type") == "content":
            settings = button_settings(button)
            parse_mode = (
                telegram.constants.ParseMode.HTML
                if settings.get("parse_mode", "HTML") == "HTML" else None
            )
            children = button_children(button)
            if children:
                await query.edit_message_text(
                    interpolate(
                        button.get("value", ""),
                        update,
                        context.bot.username,
                        context.user_data.get("points", 0),
                    ),
                    reply_markup=submenu_markup(
                        load_buttons(), children, button_child_rows(button)
                    ),
                    parse_mode=parse_mode,
                )
                return
            response = interpolate(
                button.get("value", ""),
                update,
                context.bot.username,
                context.user_data.get("points", 0),
            )
            await query.edit_message_text(
                response,
                reply_markup=user_menu_markup(update),
                parse_mode=parse_mode,
            )
            settings = button_settings(button)
            if settings.get("counter"):
                await query.message.reply_text(f"{button_name} ({settings['clicks']}👤)")
            if settings.get("reply_enabled"):
                context.user_data["pending_response_button"] = button_name
                await query.message.reply_text(str(settings.get("reply_message", "أرسل ردك الآن.")))
            return
        elif button and button.get("type", "").startswith("content_"):
            settings = button_settings(button)
            media_method = {
                "content_photo": query.message.reply_photo,
                "content_video": query.message.reply_video,
                "content_document": query.message.reply_document,
                "content_audio": query.message.reply_audio,
                "content_voice": query.message.reply_voice,
            }.get(button["type"])
            if media_method:
                await media_method(
                    button["value"],
                    caption=interpolate(
                        button.get("caption", ""),
                        update,
                        context.bot.username,
                        context.user_data.get("points", 0),
                    ) or None,
                    reply_markup=(
                        submenu_markup(
                            load_buttons(), button_children(button), button_child_rows(button)
                        )
                        if button_children(button) else None
                    ),
                    protect_content=bool(settings.get("protect_content")),
                )
            return
        else:
            response = "هذا الزر لم يعد متاحاً."
    elif query.data == "home":
        await query.edit_message_text("هلا فيك! اختر من القائمة:", reply_markup=menu_markup())
        return
    else:
        response = responses.get(query.data, "لا يوجد خيار بهذا الاسم.")
    await query.edit_message_text(response, reply_markup=user_menu_markup(update))


async def add_button_start(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    if not has_permission(update, "buttons"):
        await update.message.reply_text("لا تملك صلاحية إدارة الأزرار.")
        return ConversationHandler.END
    await update.message.reply_text("أرسل اسم الزر الجديد أو /cancel للإلغاء:")
    return BUTTON_NAME


async def receive_button_name(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    button_name = update.message.text.strip()
    if not button_name or len(button_name) > 50:
        await update.message.reply_text("اسم الزر مطلوب وأقصى طوله 50 حرفاً. أرسله مرة أخرى:")
        return BUTTON_NAME
    context.user_data["button_name"] = button_name
    await update.message.reply_text("اختر نوع الزر:", reply_markup=button_types_markup())
    return BUTTON_TYPE


async def choose_button_type(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()
    button_type = query.data.removeprefix("newtype:")
    context.user_data["button_type"] = button_type
    prompts = {
        "content": "أرسل النص أو الوسائط التي ستظهر عند الضغط:",
        "url": "أرسل الرابط الكامل، مثال: https://example.com",
        "web_app": "أرسل رابط Web App بصيغة https://",
        "inline_query": "أرسل نص الاستعلام المضمن (يمكن أن يكون فارغاً):",
        "copy_text": "أرسل النص الذي سيتم نسخه:",
        "shortcut": "أرسل اسم الزر الموجود الذي تريد إعادة استخدامه:",
    }
    await query.edit_message_text(
        prompts[button_type],
        reply_markup=back_to_button_types_markup(),
    )
    return BUTTON_VALUE


async def back_from_add_value(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data.pop("button_type", None)
    await query.edit_message_text(
        "اختر نوع الزر:",
        reply_markup=button_types_markup(),
    )
    return BUTTON_TYPE


async def back_from_add_type(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data.pop("button_name", None)
    context.user_data.pop("button_type", None)
    await query.edit_message_text("أرسل اسم الزر الجديد أو /cancel للإلغاء:")
    return BUTTON_NAME


async def receive_button_value(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    button_name = context.user_data["button_name"]
    button_type = context.user_data["button_type"]
    media_fields = (
        ("photo", "content_photo"),
        ("video", "content_video"),
        ("document", "content_document"),
        ("audio", "content_audio"),
        ("voice", "content_voice"),
    )
    media = next((getattr(update.message, field) for field, _ in media_fields
                  if getattr(update.message, field, None)), None)
    if button_type == "content" and media:
        media_type = next(kind for field, kind in media_fields if getattr(update.message, field, None))
        value = media[-1].file_id if media_type == "content_photo" else media.file_id
        button_record = {
            "type": media_type,
            "value": value,
            "caption": update.message.caption or "",
        }
    elif update.message.text:
        value = update.message.text.strip()
        button_record = {"type": button_type, "value": value}
    else:
        await update.message.reply_text("أرسل نصاً أو صورة مناسبة لهذا النوع:")
        return BUTTON_VALUE
    if button_type == "web_app" and not value.startswith("https://"):
        await update.message.reply_text("رابط Web App يجب أن يبدأ بـ https://. أعد المحاولة أو أرسل /cancel:")
        return BUTTON_VALUE
    if button_type == "url" and not valid_button_url(value):
        await update.message.reply_text("يجب أن يبدأ الرابط بـ https:// أو http:// أو tg://. أعد المحاولة أو أرسل /cancel:")
        return BUTTON_VALUE
    buttons = load_buttons()
    if button_type == "shortcut" and value not in buttons:
        await update.message.reply_text("هذا الزر غير موجود. أرسل اسماً موجوداً أو /cancel:")
        return BUTTON_VALUE
    context.user_data.pop("button_type")
    context.user_data["pending_button"] = button_record
    await update.message.reply_text(
        "هل تريد وضع الزر في القائمة الرئيسية أم داخل زر محتوى موجود؟",
        reply_markup=parent_buttons_markup(buttons),
    )
    return BUTTON_PARENT


def parent_buttons_markup(buttons: dict[str, dict[str, str]]) -> InlineKeyboardMarkup:
    keyboard = [[InlineKeyboardButton("القائمة الرئيسية", callback_data="parent:")]]
    for name, button in buttons.items():
        if button.get("type", "content") != "content" and not button.get("type", "").startswith("content_"):
            continue
        keyboard.append([
            InlineKeyboardButton(f"{name} - سطر جديد", callback_data=f"parent:new:{name}"),
            InlineKeyboardButton(f"{name} - نفس السطر", callback_data=f"parent:same:{name}"),
        ])
    keyboard.append([InlineKeyboardButton("⬅️ رجوع", callback_data="parent:back")])
    return InlineKeyboardMarkup(keyboard)


async def choose_button_parent(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "parent:back":
        return await back_from_add_parent(update, context)
    button_name = context.user_data.get("button_name")
    button_record = context.user_data.get("pending_button")
    if not button_name or not button_record:
        await query.edit_message_text("انتهت جلسة الإنشاء. أرسل /addbutton للبدء من جديد.")
        return ConversationHandler.END
    buttons = load_buttons()
    placement = query.data.removeprefix("parent:")
    placement_mode, _, parent_name = placement.partition(":")
    if placement_mode == "":
        parent_name = ""
    if parent_name and (parent_name == button_name or parent_name not in buttons):
        await query.edit_message_text("مكان الزر غير صالح، اختر مكاناً آخر:", reply_markup=parent_buttons_markup(buttons))
        return BUTTON_PARENT
    context.user_data.pop("pending_button")
    buttons[button_name] = button_record
    if parent_name:
        buttons[parent_name].setdefault("children", []).append(button_name)
        rows = buttons[parent_name].setdefault("children_rows", [])
        if placement_mode == "same" and rows:
            rows[-1].append(button_name)
        else:
            rows.append([button_name])
    save_buttons(buttons)
    await query.edit_message_text("تم إنشاء الزر وإضافته للقائمة.")
    return ConversationHandler.END


async def back_from_add_parent(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["button_type"] = "content"
    await query.edit_message_text(
        "أرسل محتوى الزر من جديد، أو اختر /cancel:",
        reply_markup=back_to_button_types_markup(),
    )
    return BUTTON_VALUE


async def cancel_add_button(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    context.user_data.pop("button_name", None)
    context.user_data.pop("button_type", None)
    context.user_data.pop("pending_button", None)
    context.user_data.pop("settings_button", None)
    context.user_data.pop("settings_action", None)
    await update.message.reply_text("تم إلغاء إنشاء الزر.")
    return ConversationHandler.END


async def button_settings_start(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    if not has_permission(update, "settings"):
        await update.message.reply_text("لا تملك صلاحية إعدادات الأزرار.")
        return ConversationHandler.END
    await update.message.reply_text("أرسل اسم الزر الذي تريد إدارته أو /cancel:")
    return SETTINGS_BUTTON


async def receive_settings_button(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    name = update.message.text.strip()
    buttons = load_buttons()
    if name not in buttons:
        await update.message.reply_text("الزر غير موجود. أرسل اسماً صحيحاً أو /cancel:")
        return SETTINGS_BUTTON
    context.user_data["settings_button"] = name
    await update.message.reply_text(
        settings_text(name, buttons[name]),
        reply_markup=settings_markup(name),
    )
    return SETTINGS_ACTION


async def settings_action(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()
    name = context.user_data["settings_button"]
    buttons = load_buttons()
    button = buttons.get(name)
    if not button:
        await query.edit_message_text("هذا الزر لم يعد موجوداً.")
        return ConversationHandler.END
    action = query.data.removeprefix("set:")
    settings = button_settings(button)
    if action == "cancel":
        await query.edit_message_text("تم إغلاق إعدادات الزر.")
        return ConversationHandler.END
    if action == "back":
        await query.edit_message_text(
            settings_text(name, button),
            reply_markup=settings_markup(name),
        )
        return SETTINGS_ACTION
    if action == "hidden":
        settings["hidden"] = not settings["hidden"]
        save_buttons(buttons)
        await query.edit_message_text(settings_text(name, button), reply_markup=settings_markup(name))
        return SETTINGS_ACTION
    if action == "counter":
        settings["counter"] = not settings["counter"]
        save_buttons(buttons)
        await query.edit_message_text(settings_text(name, button), reply_markup=settings_markup(name))
        return SETTINGS_ACTION
    if action == "protect":
        settings["protect_content"] = not settings["protect_content"]
        save_buttons(buttons)
        await query.edit_message_text(settings_text(name, button), reply_markup=settings_markup(name))
        return SETTINGS_ACTION
    if action == "stats":
        await query.edit_message_text(
            f"إحصائيات: {name}\nعدد النقرات: {settings['clicks']}\n"
            f"المستخدمون الفريدون: {len(settings['users'])}\n"
            f"آخر النقرات: {', '.join(settings['last_clicks']) or 'لا يوجد'}",
            reply_markup=settings_markup(name),
        )
        return SETTINGS_ACTION
    if action == "type":
        await query.edit_message_text(
            "اختر نوع الزر الجديد:",
            reply_markup=change_button_types_markup(),
        )
        return SETTINGS_ACTION
    if action == "requests":
        await query.edit_message_text(
            f"أرسل الأمر التالي لإدارة طلبات الزر:\n/requestsettings {name}",
            reply_markup=settings_markup(name),
        )
        return SETTINGS_ACTION
    prompts = {
        "rename": "أرسل الاسم الجديد للزر:",
        "content": "أرسل النص أو الوسائط الجديدة للمحتوى:",
        "display": "أرسل HTML أو PLAIN لتحديد طريقة العرض:",
        "reply": "أرسل on لتفعيل استقبال الردود أو off لتعطيله، ويمكن إضافة رسالة بعد | مثال: on|تم استلام ردك",
        "appearance": "أرسل الإعداد بصيغة style|icon|schedule أو اترك أي جزء فارغاً:",
        "alert": "أرسل رسالة التنبيه، أو off لإلغاء التنبيه:",
        "password": "أرسل كلمة المرور، أو off لإلغاء القفل:",
        "move": "أرسل اسم زر المحتوى الأب، أو main للقائمة الرئيسية:",
        "copy": "أرسل الاسم الجديد للنسخة:",
        "delete": "أرسل DELETE للتأكيد على الحذف النهائي:",
    }
    if action in prompts:
        context.user_data["settings_action"] = action
        await query.edit_message_text(prompts[action], reply_markup=back_to_settings_markup())
        return SETTINGS_VALUE
    return SETTINGS_ACTION


async def change_button_type(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()
    if not has_permission(update, "settings"):
        await query.edit_message_text("لا تملك صلاحية إعدادات الأزرار.")
        return ConversationHandler.END
    button_type = query.data.removeprefix("changetype:")
    context.user_data["settings_action"] = "type"
    context.user_data["settings_new_type"] = button_type
    prompts = {
        "content": "أرسل النص أو الوسائط الجديدة للمحتوى:",
        "url": "أرسل الرابط (https:// أو http:// أو tg://):",
        "web_app": "أرسل رابط Web App بصيغة https://:",
        "inline_query": "أرسل نص الاستعلام المضمن:",
        "copy_text": "أرسل النص الذي سيتم نسخه:",
        "shortcut": "أرسل اسم الزر الموجود الذي تريد إعادة استخدامه:",
    }
    await query.edit_message_text(prompts[button_type], reply_markup=back_to_settings_markup())
    return SETTINGS_VALUE


async def receive_settings_value(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    name = context.user_data["settings_button"]
    action = context.user_data.pop("settings_action")
    value = update.message.text.strip() if update.message.text else ""
    buttons = load_buttons()
    button = buttons.get(name)
    if not button:
        await update.message.reply_text("هذا الزر لم يعد موجوداً.")
        return ConversationHandler.END
    settings = button_settings(button)
    if action == "rename":
        if not value or value in buttons or len(value) > 50:
            await update.message.reply_text("الاسم فارغ أو مستخدم مسبقاً أو أطول من 50 حرفاً.")
            context.user_data["settings_action"] = action
            return SETTINGS_VALUE
        buttons[value] = buttons.pop(name)
        for item in buttons.values():
            item["children"] = [value if child == name else child for child in button_children(item)]
            item["children_rows"] = [
                [value if child == name else child for child in row]
                for row in button_child_rows(item)
            ]
        context.user_data["settings_button"] = value
        name = value
    elif action == "type":
        new_type = context.user_data.pop("settings_new_type", "")
        if new_type not in {"content", "url", "web_app", "inline_query", "copy_text", "shortcut"}:
            await update.message.reply_text("نوع الزر غير صالح.")
            return SETTINGS_ACTION
        media_fields = (
            ("photo", "content_photo"),
            ("video", "content_video"),
            ("document", "content_document"),
            ("audio", "content_audio"),
            ("voice", "content_voice"),
        )
        media = next((getattr(update.message, field) for field, _ in media_fields
                      if getattr(update.message, field, None)), None)
        if new_type == "content" and media:
            media_type = next(kind for field, kind in media_fields if getattr(update.message, field, None))
            button["type"] = media_type
            button["value"] = media[-1].file_id if media_type == "content_photo" else media.file_id
            button["caption"] = update.message.caption or ""
        elif new_type == "content" and value:
            button["type"] = "content"
            button["value"] = value
        elif new_type == "content":
            await update.message.reply_text("أرسل نصاً أو وسائط للمحتوى.")
            context.user_data["settings_action"] = action
            context.user_data["settings_new_type"] = new_type
            return SETTINGS_VALUE
        else:
            if new_type == "web_app" and not value.startswith("https://"):
                await update.message.reply_text("رابط Web App يجب أن يبدأ بـ https://.")
                context.user_data["settings_action"] = action
                context.user_data["settings_new_type"] = new_type
                return SETTINGS_VALUE
            if new_type == "url" and not valid_button_url(value):
                await update.message.reply_text("يجب أن يبدأ الرابط بـ https:// أو http:// أو tg://.")
                context.user_data["settings_action"] = action
                context.user_data["settings_new_type"] = new_type
                return SETTINGS_VALUE
            if new_type == "shortcut" and value not in buttons:
                await update.message.reply_text("هذا الزر غير موجود. أرسل اسم زر موجود.")
                context.user_data["settings_action"] = action
                context.user_data["settings_new_type"] = new_type
                return SETTINGS_VALUE
            button["type"] = new_type
            button["value"] = value
            button.pop("caption", None)
    elif action == "content":
        media_fields = (
            ("photo", "content_photo"),
            ("video", "content_video"),
            ("document", "content_document"),
            ("audio", "content_audio"),
            ("voice", "content_voice"),
        )
        media = next((getattr(update.message, field) for field, _ in media_fields
                      if getattr(update.message, field, None)), None)
        if media:
            media_type = next(kind for field, kind in media_fields if getattr(update.message, field, None))
            button["type"] = media_type
            button["value"] = media[-1].file_id if media_type == "content_photo" else media.file_id
            button["caption"] = update.message.caption or ""
        elif value:
            button["type"] = "content"
            button["value"] = value
        else:
            await update.message.reply_text("أرسل نصاً أو وسائط مناسبة للمحتوى:")
            context.user_data["settings_action"] = action
            return SETTINGS_VALUE
    elif action == "display":
        if value.upper() not in ("HTML", "PLAIN"):
            await update.message.reply_text("أرسل HTML أو PLAIN فقط.")
            context.user_data["settings_action"] = action
            return SETTINGS_VALUE
        settings["parse_mode"] = value.upper()
    elif action == "alert":
        settings["alert"] = "" if value.casefold() == "off" else value
    elif action == "password":
        settings["password"] = "" if value.casefold() == "off" else value
    elif action == "reply":
        parts = value.split("|", 1)
        settings["reply_enabled"] = parts[0].casefold() == "on"
        settings["reply_message"] = parts[1] if len(parts) == 2 else "تم استلام ردك."
    elif action == "appearance":
        style, icon, schedule = (value.split("|", 2) + ["", ""])[:3]
        settings["style"], settings["icon"], settings["schedule"] = style, icon, schedule
    elif action == "move":
        for item in buttons.values():
            item["children"] = [child for child in button_children(item) if child != name]
            item["children_rows"] = [
                [child for child in row if child != name]
                for row in button_child_rows(item)
                if any(child != name for child in row)
            ]
        if value.casefold() != "main":
            if value not in buttons or buttons[value].get("type") != "content":
                await update.message.reply_text("الأب غير موجود أو ليس زر محتوى.")
                context.user_data["settings_action"] = action
                return SETTINGS_VALUE
            buttons[value].setdefault("children", []).append(name)
            buttons[value].setdefault("children_rows", []).append([name])
    elif action == "copy":
        if not value or value in buttons or len(value) > 50:
            await update.message.reply_text("الاسم الجديد غير صالح أو مستخدم مسبقاً.")
            context.user_data["settings_action"] = action
            return SETTINGS_VALUE
        buttons[value] = json.loads(json.dumps(button))
        context.user_data["settings_button"] = value
        name = value
    elif action == "delete":
        if value != "DELETE":
            await update.message.reply_text("لم يتم الحذف. أرسل DELETE للتأكيد أو /cancel.")
            context.user_data["settings_action"] = action
            return SETTINGS_VALUE
        buttons.pop(name)
        for item in buttons.values():
            item["children"] = [child for child in button_children(item) if child != name]
            item["children_rows"] = [
                [child for child in row if child != name]
                for row in button_child_rows(item)
                if any(child != name for child in row)
            ]
        save_buttons(buttons)
        await update.message.reply_text("تم حذف الزر نهائياً.")
        return ConversationHandler.END
    save_buttons(buttons)
    await update.message.reply_text(
        settings_text(name, buttons[name]),
        reply_markup=settings_markup(name),
    )
    return SETTINGS_ACTION


async def back_from_settings_value(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()
    name = context.user_data.get("settings_button")
    button = load_buttons().get(name) if name else None
    context.user_data.pop("settings_action", None)
    if not button:
        await query.edit_message_text("تم إغلاق إعدادات الزر.")
        return ConversationHandler.END
    await query.edit_message_text(
        settings_text(name, button),
        reply_markup=settings_markup(name),
    )
    return SETTINGS_ACTION


async def admin_panel(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("هذا الأمر متاح للمشرفين فقط.")
        return
    await update.message.reply_text("لوحة تحكم البوت:", reply_markup=admin_panel_markup())


async def admin_requests(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not has_permission(update, "settings"):
        await update.message.reply_text("لا تملك صلاحية عرض طلبات العملاء.")
        return
    requests = load_requests()

    def answers_text(item: dict[str, object]) -> str:
        return "; ".join(
            f"{answer.get('question')}: {answer.get('answer')}"
            for answer in item.get("answers", [])
            if isinstance(answer, dict)
        )

    if not requests:
        text = "📋 طلبات العملاء\n\nلا توجد طلبات حتى الآن."
    else:
        lines = [
            f"{index}. {item.get('button') or 'طلب عام'} | {answers_text(item)} | "
            f"{item.get('created_at', '')}"
            for index, item in enumerate(requests[-20:], 1)
        ]
        text = "📋 آخر طلبات العملاء:\n\n" + "\n".join(lines)
    await update.message.reply_text(text, reply_markup=admin_panel_markup())


async def admin_panel_click(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(update):
        await query.edit_message_text("هذه اللوحة متاحة للمشرفين فقط.")
        return
    action = query.data.removeprefix("admin:")
    if action == "home":
        await query.edit_message_text("هلا فيك! اختر من القائمة:", reply_markup=user_menu_markup(update))
        return
    if action == "store":
        await store_panel_start(update, context)
        return
    if action == "stats":
        users = load_users()
        today = datetime.now().date().isoformat()
        new_today = sum(user.get("joined") == today for user in users.values())
        await query.edit_message_text(
            f"📊 إحصائيات البوت\nإجمالي المستخدمين: {len(users)}\nالجدد اليوم: {new_today}\nالأزرار: {len(load_buttons())}",
            reply_markup=admin_panel_markup(),
        )
        return
    if action == "requests":
        if not has_permission(update, "settings"):
            await query.edit_message_text("لا تملك صلاحية عرض طلبات العملاء.")
            return
        requests = load_requests()
        await query.edit_message_text(
            f"📋 طلبات العملاء\n\nإجمالي الطلبات: {len(requests)}\n\n"
            + ("لا توجد طلبات حتى الآن." if not requests else "افتح /requests لعرض آخر الطلبات."),
            reply_markup=admin_panel_markup(),
        )
        return
    if action == "broadcast":
        if not has_permission(update, "settings"):
            await query.edit_message_text("لا تملك صلاحية التواصل والإذاعة.")
            return
        await query.edit_message_text("📨 اختر نوع الإرسال:", reply_markup=broadcast_mode_markup())
        return
    if action == "request_settings":
        if not has_permission(update, "settings"):
            await query.edit_message_text("لا تملك صلاحية إعدادات الطلبات.")
            return
        await query.edit_message_text(
            "أرسل /requestsettings ثم اسم الزر، مثال:\n/requestsettings الطلبات",
            reply_markup=admin_panel_markup(),
        )
        return
    if action == "notifications":
        if not has_permission(update, "settings"):
            await query.edit_message_text("لا تملك صلاحية إدارة الإشعارات.")
            return
        enabled = load_notifications().get("new_user", True)
        await query.edit_message_text(
            "🔔 إعدادات الإشعارات\n\n"
            f"إشعار دخول عضو جديد: {'مفعل' if enabled else 'معطل'}",
            reply_markup=notifications_markup(),
        )
        return
    if action == "sections":
        await query.edit_message_text("🧩 أقسام الإدارة:", reply_markup=admin_sections_markup())
        return
    if action == "auto_replies":
        if not has_permission(update, "settings"):
            await query.edit_message_text("لا تملك صلاحية إدارة الردود التلقائية.")
            return
        await query.edit_message_text("🤖 إدارة الردود التلقائية:", reply_markup=auto_replies_markup())
        return
    if action.startswith("section:"):
        section = action.removeprefix("section:")
        section_names = {
            "content": "المحتوى: إدارة الأزرار والقوائم من خلال إنشاء زر وإعداداته.",
            "settings": "الإعدادات: إعدادات الردود والتنبيهات والحماية.",
            "users": "المستخدمون: الإحصائيات وإدارة المشرفين.",
            "subscription": "الاشتراك: هذه الواجهة جاهزة لإضافة نظام الاشتراكات.",
            "finance": "المالية: هذه الواجهة جاهزة لإضافة المدفوعات.",
            "contact": "التواصل: هذه الواجهة جاهزة لإضافة رسائل جماعية.",
            "system": "النظام والدعم: التخزين المؤقت وصحة النظام.",
        }
        await query.edit_message_text(
            section_names.get(section, "القسم غير موجود."),
            reply_markup=(broadcast_mode_markup() if section == "contact" else admin_sections_markup()),
        )
        return
    if action == "add_button":
        if not has_permission(update, "buttons"):
            await query.edit_message_text("لا تملك صلاحية إدارة الأزرار.")
            return
        await query.edit_message_text("أرسل /addbutton لبدء إنشاء زر.", reply_markup=admin_panel_markup())
        return
    if action == "button_settings":
        if not has_permission(update, "settings"):
            await query.edit_message_text("لا تملك صلاحية إعدادات الأزرار.")
            return
        await query.edit_message_text("أرسل /buttonsettings لفتح إعدادات زر.", reply_markup=admin_panel_markup())
        return
    if action == "admins":
        if not has_permission(update, "admins"):
            await query.edit_message_text("لا تملك صلاحية إدارة المشرفين.")
            return
        admins = load_admins()
        lines = [f"👤 {key}: {permissions_label(value.get('permissions'))}" for key, value in admins.items()]
        text = "المشرفون:\n" + ("\n".join(lines) if lines else "لا يوجد مشرفون إضافيون.")
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ إضافة مشرف", callback_data="admin:add_admin")],
            [InlineKeyboardButton("🗑 حذف مشرف", callback_data="admin:remove_admin")],
            [InlineKeyboardButton("⬅️ رجوع", callback_data="admin:panel")],
        ])
        await query.edit_message_text(text, reply_markup=markup)
        return
    if action == "clear_cache":
        if not has_permission(update, "cache"):
            await query.edit_message_text("لا تملك صلاحية حذف التخزين المؤقت.")
            return
        context.user_data.clear()
        await query.edit_message_text("تم حذف التخزين المؤقت وحالات هذه الجلسة.", reply_markup=admin_panel_markup())
        return
    if action == "panel":
        await query.edit_message_text("لوحة تحكم البوت:", reply_markup=admin_panel_markup())
        return
    if action == "add_admin":
        if has_permission(update, "admins"):
            await query.edit_message_text(
                "أرسل Telegram ID الرقمي أو اسم المستخدم مثل @example_user للمشرف الجديد، أو /cancel:",
                reply_markup=admin_panel_markup(),
            )
            context.user_data["adding_admin"] = True
        return
    if action == "remove_admin":
        if has_permission(update, "admins"):
            await query.edit_message_text("أرسل رقم أو اسم المستخدم للمشرف المراد حذفه أو /cancel:")
            context.user_data["removing_admin"] = True
        return


async def receive_admin_user(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not has_permission(update, "admins"):
        return ConversationHandler.END
    identifier = normalize_admin_identifier(update.message.text)
    if not identifier:
        await update.message.reply_text(
            "المعرف غير صالح. أرسل Telegram ID رقميًا أو اسم مستخدم مثل @example_user:"
        )
        return ADMIN_USER
    owner_username = os.getenv("TELEGRAM_ADMIN_USERNAME", ADMIN_USERNAME).casefold().lstrip("@")
    owner_id = os.getenv("TELEGRAM_ADMIN_ID")
    if identifier == owner_username or (owner_id and identifier == owner_id):
        await update.message.reply_text("هذا الحساب هو المالك الرئيسي ولا يحتاج إلى إضافة.")
        return ADMIN_USER
    context.user_data["new_admin"] = identifier
    await update.message.reply_text(
        "اختر الصلاحيات:",
        reply_markup=admin_permissions_markup(),
    )
    return ADMIN_PERMISSIONS


async def save_admin_permissions(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if not has_permission(update, "admins"):
        await query.edit_message_text("لا تملك صلاحية إدارة المشرفين.")
        return ConversationHandler.END
    identifier = context.user_data.pop("new_admin", "")
    permissions = (
        set(ALL_PERMISSIONS)
        if query.data == "adminperm:all"
        else set(query.data.removeprefix("adminperm:").split(",")) & ALL_PERMISSIONS
    )
    if not identifier or not permissions:
        await query.edit_message_text("بيانات المشرف أو الصلاحيات غير صالحة.", reply_markup=admin_panel_markup())
        return ConversationHandler.END
    admins = load_admins()
    admins[identifier] = {"permissions": sorted(permissions)}
    save_admins(admins)
    context.user_data.pop("adding_admin", None)
    await query.edit_message_text("تمت إضافة المشرف بالصلاحيات المحددة.", reply_markup=admin_panel_markup())
    return ConversationHandler.END


async def pending_admin_message(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await automatic_reply(update, context)
        return
    if context.user_data.get("removing_admin"):
        identifier = normalize_admin_identifier(update.message.text)
        if not identifier:
            await update.message.reply_text(
                "المعرف غير صالح. أرسل Telegram ID أو اسم المستخدم مرة أخرى:"
            )
            return
        owner_username = os.getenv("TELEGRAM_ADMIN_USERNAME", ADMIN_USERNAME).casefold().lstrip("@")
        owner_id = os.getenv("TELEGRAM_ADMIN_ID")
        if identifier == owner_username or (owner_id and identifier == owner_id):
            await update.message.reply_text("لا يمكن حذف المالك الرئيسي.", reply_markup=admin_panel_markup())
            context.user_data.pop("removing_admin", None)
            return
        admins = load_admins()
        if admins.pop(identifier, None) is None:
            await update.message.reply_text("لم يتم العثور على هذا المشرف.", reply_markup=admin_panel_markup())
        else:
            save_admins(admins)
            await update.message.reply_text("تم حذف المشرف.", reply_markup=admin_panel_markup())
        context.user_data.pop("removing_admin", None)
        return
    if not context.user_data.get("adding_admin"):
        return
    await receive_admin_user(update, context)


async def admin_command_cancel(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return
    context.user_data.pop("adding_admin", None)
    context.user_data.pop("removing_admin", None)
    context.user_data.pop("new_admin", None)
    await update.message.reply_text("تم إلغاء العملية.", reply_markup=admin_panel_markup())


async def broadcast_start(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    if not has_permission(update, "settings"):
        if update.callback_query:
            await update.callback_query.edit_message_text("لا تملك صلاحية التواصل والإذاعة.")
        else:
            await update.effective_message.reply_text("لا تملك صلاحية التواصل والإذاعة.")
        return ConversationHandler.END
    if update.callback_query:
        await update.callback_query.answer()
        mode = update.callback_query.data.removeprefix("broadcast:")
        context.user_data["broadcast_mode"] = mode
        await update.callback_query.edit_message_text("أرسل الرسالة أو الوسائط المراد إرسالها:")
    else:
        await update.effective_message.reply_text("اختر نوع الإرسال:", reply_markup=broadcast_mode_markup())
    return BROADCAST_MESSAGE if update.callback_query else BROADCAST_MODE


async def broadcast_message(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    message = update.effective_message
    context.user_data["broadcast_chat_id"] = message.chat_id
    context.user_data["broadcast_message_id"] = message.message_id
    context.user_data["broadcast_text"] = message.text or ""
    context.user_data.setdefault("broadcast_options", {
        "pin": False,
        "silent": False,
        "protect": False,
        "preview": True,
    })
    await message.reply_text(
        "خصص خيارات الإرسال ثم اختر الجمهور:",
        reply_markup=broadcast_options_markup(context),
    )
    return BROADCAST_OPTIONS


def broadcast_recipients(audience: str) -> list[str]:
    users = load_users()
    owner_id = os.getenv("TELEGRAM_ADMIN_ID")
    now = datetime.now()
    recipients = []
    for user_id, user in users.items():
        if owner_id and user_id == owner_id:
            continue
        joined = _parse_user_date(user.get("joined", ""))
        active = _parse_user_date(user.get("last_active", ""))
        if audience == "all":
            include = True
        elif audience.startswith("new"):
            include = bool(joined and joined >= now - timedelta(days=int(audience.removeprefix("new"))))
        else:
            include = bool(active and active < now - timedelta(days=int(audience.removeprefix("inactive"))))
        if include:
            recipients.append(user_id)
    return recipients


def _parse_user_date(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


async def broadcast_options_click(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "broadcast:audience":
        await query.edit_message_text("🎯 اختر الجمهور:", reply_markup=broadcast_audience_markup())
        return BROADCAST_AUDIENCE
    if query.data == "broadcast:help":
        await query.edit_message_text(
            "📖 مساعدة الإذاعة\n\n"
            "رسالة مباشرة تنسخ المحتوى بدون مصدر.\n"
            "توجيه رسالة يحافظ على المصدر الأصلي.\n"
            "التثبيت قد يحتاج صلاحيات إضافية في المحادثة.\n"
            "اختر الجمهور بعد ضبط الخيارات.",
            reply_markup=broadcast_options_markup(context),
        )
        return BROADCAST_OPTIONS
    if query.data == "broadcast:cancel":
        return await broadcast_cancel(update, context)
    key = query.data.removeprefix("broadcastopt:")
    options = context.user_data.setdefault("broadcast_options", {})
    options[key] = not options.get(key, False)
    await query.edit_message_text("خصص خيارات الإرسال ثم اختر الجمهور:", reply_markup=broadcast_options_markup(context))
    return BROADCAST_OPTIONS


async def broadcast_audience(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()
    audience = query.data.removeprefix("audience:")
    recipients = broadcast_recipients(audience)
    options = context.user_data.get("broadcast_options", {})
    chat_id = context.user_data["broadcast_chat_id"]
    message_id = context.user_data["broadcast_message_id"]
    mode = context.user_data.get("broadcast_mode", "direct")
    sent = 0
    failed = 0
    for recipient in recipients:
        try:
            if mode == "forward":
                result = await context.bot.forward_message(
                    chat_id=recipient,
                    from_chat_id=chat_id,
                    message_id=message_id,
                    disable_notification=bool(options.get("silent")),
                    protect_content=bool(options.get("protect")),
                )
            elif context.user_data.get("broadcast_text"):
                result = await context.bot.send_message(
                    chat_id=recipient,
                    text=context.user_data["broadcast_text"],
                    disable_notification=bool(options.get("silent")),
                    protect_content=bool(options.get("protect")),
                    disable_web_page_preview=not bool(options.get("preview", True)),
                )
            else:
                result = await context.bot.copy_message(
                    chat_id=recipient,
                    from_chat_id=chat_id,
                    message_id=message_id,
                    disable_notification=bool(options.get("silent")),
                    protect_content=bool(options.get("protect")),
                )
            sent += 1
            if options.get("pin"):
                try:
                    await context.bot.pin_chat_message(
                        chat_id=recipient,
                        message_id=result.message_id if hasattr(result, "message_id") else result,
                        disable_notification=True,
                    )
                except telegram.error.TelegramError:
                    pass
        except telegram.error.TelegramError:
            failed += 1
    await query.edit_message_text(
        f"✅ انتهت الإذاعة\nتم الإرسال: {sent}\nفشل الإرسال: {failed}",
        reply_markup=admin_panel_markup(),
    )
    context.user_data.clear()
    return ConversationHandler.END


async def broadcast_cancel(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    context.user_data.clear()
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("تم إلغاء الإذاعة.", reply_markup=admin_panel_markup())
    else:
        await update.effective_message.reply_text("تم إلغاء الإذاعة.", reply_markup=admin_panel_markup())
    return ConversationHandler.END


async def auto_reply_start(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    if not has_permission(update, "settings"):
        await update.message.reply_text("لا تملك صلاحية إدارة الردود التلقائية.")
        return ConversationHandler.END
    await update.message.reply_text("أرسل الكلمة أو العبارة التي سيبحث عنها البوت:")
    return AUTO_KEYWORD


async def auto_add_callback(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()
    if not has_permission(update, "settings"):
        await query.edit_message_text("لا تملك صلاحية إدارة الردود التلقائية.")
        return ConversationHandler.END
    await query.edit_message_text("أرسل الكلمة أو العبارة التي سيبحث عنها البوت:")
    return AUTO_KEYWORD


async def auto_reply_keyword(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    keyword = update.message.text.strip()
    if not keyword:
        await update.message.reply_text("الكلمة لا يمكن أن تكون فارغة:")
        return AUTO_KEYWORD
    context.user_data["auto_keyword"] = keyword
    await update.message.reply_text("اختر نوع المطابقة:", reply_markup=auto_match_markup())
    return AUTO_MATCH


async def auto_reply_match(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["auto_match"] = query.data.removeprefix("automatch:")
    await query.edit_message_text("أرسل نص الرد التلقائي:", reply_markup=auto_response_markup())
    return AUTO_RESPONSE


async def edit_auto_reply_start(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()
    if not has_permission(update, "settings"):
        await query.edit_message_text("لا تملك صلاحية إدارة الردود التلقائية.")
        return ConversationHandler.END
    index = int(query.data.rsplit(":", 1)[-1])
    replies = load_auto_replies()
    if index >= len(replies):
        await query.edit_message_text("الرد التلقائي غير موجود.", reply_markup=auto_replies_markup())
        return ConversationHandler.END
    context.user_data["auto_edit_index"] = index
    context.user_data["auto_existing"] = replies[index]
    await query.edit_message_text("أرسل الكلمة المفتاحية الجديدة:")
    return AUTO_KEYWORD


async def back_auto_match(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("أرسل الكلمة أو العبارة التي سيبحث عنها البوت:")
    return AUTO_KEYWORD


async def back_auto_options(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("أرسل نص الرد التلقائي:", reply_markup=auto_response_markup())
    return AUTO_RESPONSE


async def cancel_auto_reply(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()
    for key in ("auto_keyword", "auto_match", "auto_response", "auto_options"):
        context.user_data.pop(key, None)
    await query.edit_message_text("تم إلغاء إنشاء الرد.", reply_markup=auto_replies_markup())
    return ConversationHandler.END


async def auto_reply_response(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    media_fields = (
        ("photo", "photo"),
        ("video", "video"),
        ("document", "document"),
        ("audio", "audio"),
        ("voice", "voice"),
    )
    media = next((getattr(update.message, field) for field, _ in media_fields
                  if getattr(update.message, field, None)), None)
    if media:
        response_type = next(kind for field, kind in media_fields if getattr(update.message, field, None))
        response_value = media[-1].file_id if response_type == "photo" else media.file_id
        context.user_data["auto_response_type"] = response_type
        context.user_data["auto_response_value"] = response_value
        context.user_data["auto_caption"] = update.message.caption or ""
    elif update.message.text and update.message.text.strip():
        context.user_data["auto_response_type"] = "text"
        context.user_data["auto_response_value"] = update.message.text.strip()
        context.user_data["auto_caption"] = ""
    else:
        await update.message.reply_text("أرسل نصًا أو وسائط للرد:")
        return AUTO_RESPONSE
    context.user_data["auto_response"] = context.user_data["auto_response_value"]
    context.user_data["auto_options"] = {
        "stop": False,
        "disable_web_page_preview": True,
        "protect_content": False,
        "silent": False,
        "spoiler": False,
        "caption_above": False,
        "buttons": [],
    }
    context.user_data["auto_button_names"] = list(load_buttons())
    await update.message.reply_text(
        "خصص خيارات الرد ثم اضغط حفظ:",
        reply_markup=auto_options_markup(context),
    )
    return AUTO_OPTIONS


def parse_auto_options(value: str, buttons: dict[str, dict[str, str]]) -> dict[str, object] | None:
    parts = value.split("|", 6)
    if len(parts) != 7 or any(part.strip() not in ("0", "1") for part in parts[:6]):
        return None
    button_names = [name.strip() for name in parts[6].split(",") if name.strip()]
    if any(name not in buttons for name in button_names):
        return None
    return {
        "stop": parts[0].strip() == "1",
        "disable_web_page_preview": parts[1].strip() == "0",
        "protect_content": parts[2].strip() == "1",
        "silent": parts[3].strip() == "1",
        "spoiler": parts[4].strip() == "1",
        "caption_above": parts[5].strip() == "1",
        "buttons": button_names,
    }


async def auto_reply_options(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    value = update.message.text.strip()
    options = parse_auto_options(value, load_buttons())
    if options is None:
        await update.message.reply_text("الإعدادات غير صحيحة. استخدم 7 قيم مفصولة بـ | كما في المثال:")
        return AUTO_OPTIONS
    context.user_data["auto_options"] = options
    return await save_auto_reply(update, context)


def automatic_button_markup(names: list[str]) -> InlineKeyboardMarkup | None:
    buttons = load_buttons()
    rows = []
    for name in names:
        button = resolve_button(buttons, name)
        if not button:
            continue
        button_type = button.get("type", "content")
        label = name
        if button_type == "url":
            item = InlineKeyboardButton(label, url=button["value"])
        elif button_type == "web_app":
            item = InlineKeyboardButton(label, web_app=WebAppInfo(url=button["value"]))
        elif button_type == "inline_query":
            item = InlineKeyboardButton(label, switch_inline_query=button["value"])
        elif button_type == "copy_text":
            item = InlineKeyboardButton(label, copy_text=CopyTextButton(button["value"]))
        else:
            item = InlineKeyboardButton(label, callback_data=f"custom:{name}")
        rows.append([item])
    return InlineKeyboardMarkup(rows) if rows else None


async def save_auto_reply(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    reply = {
        "keyword": context.user_data.pop("auto_keyword"),
        "match": context.user_data.pop("auto_match"),
        "response": context.user_data.pop("auto_response"),
        "response_type": context.user_data.pop("auto_response_type", "text"),
        "caption": context.user_data.pop("auto_caption", ""),
        **context.user_data.pop("auto_options"),
        "enabled": True,
    }
    replies = load_auto_replies()
    edit_index = context.user_data.pop("auto_edit_index", None)
    existing = context.user_data.pop("auto_existing", None)
    if edit_index is not None and isinstance(existing, dict):
        reply["enabled"] = existing.get("enabled", True)
        replies[edit_index] = reply
    else:
        replies.append(reply)
    save_auto_replies(replies)
    await update.effective_message.reply_text("تم حفظ الرد التلقائي.", reply_markup=auto_replies_markup())
    return ConversationHandler.END


async def auto_reply_callback(
    update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    action = query.data.removeprefix("auto:")
    if action == "add":
        await query.edit_message_text("أرسل /addautoreply لبدء إنشاء رد تلقائي.", reply_markup=auto_replies_markup())
    elif action == "list":
        replies = load_auto_replies()
        await query.edit_message_text(
            "الردود التلقائية (اضغط على القاعدة لتعطيلها/تفعيلها):",
            reply_markup=auto_replies_list_markup(replies),
        )
    elif action.startswith("toggle:"):
        index = int(action.removeprefix("toggle:"))
        replies = load_auto_replies()
        if index < len(replies):
            replies[index]["enabled"] = not replies[index].get("enabled", True)
            save_auto_replies(replies)
        await query.edit_message_text("الردود التلقائية:", reply_markup=auto_replies_list_markup(replies))
    elif action.startswith("delete:"):
        index = int(action.removeprefix("delete:"))
        replies = load_auto_replies()
        if index < len(replies):
            replies.pop(index)
            save_auto_replies(replies)
        await query.edit_message_text("الردود التلقائية:", reply_markup=auto_replies_list_markup(replies))
    elif action == "cancel":
        for key in (
            "auto_keyword", "auto_match", "auto_response", "auto_response_type",
            "auto_response_value", "auto_caption", "auto_options", "auto_edit_index",
            "auto_existing", "auto_button_names",
        ):
            context.user_data.pop(key, None)
        await query.edit_message_text("تم إلغاء إنشاء الرد.", reply_markup=auto_replies_markup())


async def auto_option_click(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if not has_permission(update, "settings"):
        await query.edit_message_text("لا تملك صلاحية إدارة الردود التلقائية.")
        return ConversationHandler.END
    options = context.user_data.setdefault("auto_options", {})
    action = query.data.removeprefix("autoopt:")
    if action.startswith("toggle:"):
        key = action.removeprefix("toggle:")
        options[key] = not options.get(key, False)
    elif action.startswith("button:"):
        name = action.removeprefix("button:")
        selected = options.setdefault("buttons", [])
        if name in selected:
            selected.remove(name)
        else:
            selected.append(name)
    elif action == "save":
        return await save_auto_reply(update, context)
    await query.edit_message_text("خصص خيارات الرد ثم اضغط حفظ:", reply_markup=auto_options_markup(context))
    return AUTO_OPTIONS


async def auto_reply_manager(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not has_permission(update, "settings"):
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("لا تملك صلاحية إدارة الردود التلقائية.")
        return
    await auto_reply_callback(update, context)


async def notification_click(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not has_permission(update, "settings"):
        await query.edit_message_text("لا تملك صلاحية إدارة الإشعارات.")
        return
    settings = load_notifications()
    settings["new_user"] = not settings.get("new_user", True)
    save_notifications(settings)
    enabled = settings["new_user"]
    await query.edit_message_text(
        "🔔 إعدادات الإشعارات\n\n"
        f"إشعار دخول عضو جديد: {'مفعل' if enabled else 'معطل'}",
        reply_markup=notifications_markup(),
    )


def auto_reply_matches(item: dict[str, object], text: str) -> bool:
    keyword = " ".join(str(item.get("keyword", "")).casefold().split())
    value = " ".join(text.casefold().split())
    match_type = item.get("match", "contains")
    if match_type == "exact":
        return value == keyword
    if match_type == "starts":
        return value.startswith(keyword)
    if match_type == "word":
        return re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", value) is not None
    return keyword in value


async def send_auto_reply(
    message: telegram.Message,
    item: dict[str, object],
    update: telegram.Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    options = item
    markup = automatic_button_markup(options.get("buttons", []))
    response = interpolate(
        str(item.get("response", "")),
        update,
        context.bot.username,
        context.user_data.get("points", 0),
    )
    response_type = item.get("response_type", "text")
    if response_type == "text":
        await message.reply_text(
            response,
            parse_mode=telegram.constants.ParseMode.HTML,
            disable_web_page_preview=bool(options.get("disable_web_page_preview", False)),
            disable_notification=bool(options.get("silent", False)),
            protect_content=bool(options.get("protect_content", False)),
            reply_markup=markup,
        )
        return
    media_method = {
        "photo": message.reply_photo,
        "video": message.reply_video,
        "document": message.reply_document,
        "audio": message.reply_audio,
        "voice": message.reply_voice,
    }.get(response_type)
    if media_method:
        media_options = {
            "disable_notification": bool(options.get("silent", False)),
            "protect_content": bool(options.get("protect_content", False)),
            "reply_markup": markup,
        }
        if response_type in ("photo", "video"):
            media_options["has_spoiler"] = bool(options.get("spoiler", False))
            media_options["show_caption_above_media"] = bool(options.get("caption_above", False))
        await media_method(
            response,
            caption=interpolate(
                str(item.get("caption", "")),
                update,
                context.bot.username,
                context.user_data.get("points", 0),
            ) or None,
            **media_options,
        )


async def automatic_reply(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    await record_user(update, context)
    if is_admin(update):
        return
    text = message.text.strip().lower()

    pending_password = context.user_data.get("pending_password_button")
    if pending_password:
        button = load_buttons().get(pending_password)
        if button and text == str(button_settings(button).get("password", "")).casefold():
            context.user_data.setdefault("unlocked_buttons", set()).add(pending_password)
            context.user_data.pop("pending_password_button")
            await message.reply_text(
                interpolate(
                    str(button.get("value", "")),
                    update,
                    context.bot.username,
                    context.user_data.get("points", 0),
                ),
                parse_mode=telegram.constants.ParseMode.HTML,
            )
        else:
            await message.reply_text("كلمة المرور غير صحيحة، حاول مرة أخرى أو أرسل /start.")
        return

    pending_response = context.user_data.pop("pending_response_button", None)
    if pending_response:
        await message.reply_text("تم استلام ردك، شكرًا لك.")
        return

    for item in load_auto_replies():
        if not item.get("enabled", True) or not auto_reply_matches(item, message.text):
            continue
        await send_auto_reply(message, item, update, context)
        if item.get("stop"):
            raise ApplicationHandlerStop
        return

    if any(word in text for word in ("مرحبا", "هلا", "hello", "hi")):
        response = "أهلاً وسهلاً! كيف أقدر أساعدك؟"
    elif "شكرا" in text or "thanks" in text:
        response = "العفو!"
    else:
        response = "وصلت رسالتك. اختر أحد الخيارات أو اكتب /start."

    await message.reply_text(response)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled Telegram update error", exc_info=context.error)


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
    )
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Set the TELEGRAM_BOT_TOKEN environment variable first.")

    application = Application.builder().token(token).build()
    application.add_error_handler(error_handler)
    application.add_handler(CommandHandler(("start", "start1"), start))
    application.add_handler(build_data_request_handler())
    application.add_handler(build_request_settings_handler())
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("requests", admin_requests))
    for handler in build_button_handlers():
        application.add_handler(handler)
    for handler in build_store_handlers():
        application.add_handler(handler)
    application.add_handler(ConversationHandler(
        entry_points=[
            CommandHandler("broadcast", broadcast_start),
            CallbackQueryHandler(broadcast_start, pattern=r"^broadcast:(direct|forward)$"),
        ],
        states={
            BROADCAST_MODE: [
                CallbackQueryHandler(broadcast_start, pattern=r"^broadcast:(direct|forward)$"),
            ],
            BROADCAST_MESSAGE: [
                MessageHandler(filters.ALL & ~filters.COMMAND, broadcast_message),
            ],
            BROADCAST_OPTIONS: [
                CallbackQueryHandler(broadcast_options_click, pattern=r"^broadcast(opt:|:audience|:help|:cancel)"),
            ],
            BROADCAST_AUDIENCE: [
                CallbackQueryHandler(broadcast_audience, pattern=r"^audience:"),
                CallbackQueryHandler(broadcast_options_click, pattern=r"^broadcast:back_options$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", broadcast_cancel)],
    ))
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler("_legacy_addbutton", add_button_start)],
        states={
            BUTTON_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_button_name)],
            BUTTON_TYPE: [CallbackQueryHandler(
                choose_button_type,
                pattern=r"^newtype:",
            ), CallbackQueryHandler(back_from_add_type, pattern=r"^add:back$")],
            BUTTON_VALUE: [
                CallbackQueryHandler(back_from_add_value, pattern=r"^add:types$"),
                MessageHandler(
                    (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL |
                     filters.AUDIO | filters.VOICE) & ~filters.COMMAND,
                    receive_button_value,
                ),
            ],
            BUTTON_PARENT: [CallbackQueryHandler(choose_button_parent, pattern=r"^parent:")],
        },
        fallbacks=[CommandHandler("cancel", cancel_add_button)],
    ))
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler("_legacy_buttonsettings", button_settings_start)],
        states={
            SETTINGS_BUTTON: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_settings_button)],
            SETTINGS_ACTION: [
                CallbackQueryHandler(change_button_type, pattern=r"^changetype:"),
                CallbackQueryHandler(settings_action, pattern=r"^set:"),
            ],
            SETTINGS_VALUE: [
                CallbackQueryHandler(back_from_settings_value, pattern=r"^set:back$"),
                MessageHandler(
                    (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL |
                     filters.AUDIO | filters.VOICE) & ~filters.COMMAND,
                    receive_settings_value,
                ),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_add_button)],
    ))
    application.add_handler(ConversationHandler(
        entry_points=[
            CommandHandler("addautoreply", auto_reply_start),
            CallbackQueryHandler(auto_add_callback, pattern=r"^auto:add$"),
            CallbackQueryHandler(edit_auto_reply_start, pattern=r"^auto:edit:\d+$"),
        ],
        states={
            AUTO_KEYWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, auto_reply_keyword)],
            AUTO_MATCH: [
                CallbackQueryHandler(auto_reply_match, pattern=r"^automatch:"),
                CallbackQueryHandler(cancel_auto_reply, pattern=r"^auto:cancel$"),
                CallbackQueryHandler(back_auto_match, pattern=r"^autoreply:keywordback$"),
            ],
            AUTO_RESPONSE: [
                CallbackQueryHandler(back_auto_match, pattern=r"^autoreply:back$"),
                CallbackQueryHandler(cancel_auto_reply, pattern=r"^auto:cancel$"),
                MessageHandler(
                    (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL |
                     filters.AUDIO | filters.VOICE) & ~filters.COMMAND,
                    auto_reply_response,
                ),
            ],
            AUTO_OPTIONS: [
                CallbackQueryHandler(auto_option_click, pattern=r"^autoopt:"),
                CallbackQueryHandler(back_auto_options, pattern=r"^autoreply:back$"),
                CallbackQueryHandler(cancel_auto_reply, pattern=r"^auto:cancel$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, auto_reply_options),
            ],
        },
        fallbacks=[CommandHandler("cancel", admin_command_cancel)],
    ))
    application.add_handler(CommandHandler("cancel", admin_command_cancel))
    application.add_handler(CallbackQueryHandler(notification_click, pattern=r"^notify:"))
    application.add_handler(CallbackQueryHandler(auto_reply_manager, pattern=r"^auto:"))
    application.add_handler(CallbackQueryHandler(save_admin_permissions, pattern=r"^adminperm:"))
    application.add_handler(CallbackQueryHandler(admin_panel_click, pattern=r"^admin:"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, pending_admin_message))
    application.add_handler(CallbackQueryHandler(button_click))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, automatic_reply))
    application.run_polling()


if __name__ == "__main__":
    main()

