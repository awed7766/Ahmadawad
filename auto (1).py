import json
import os
from datetime import datetime
from html import escape
from pathlib import Path

import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
	CommandHandler,
	ConversationHandler,
	ContextTypes,
	MessageHandler,
	CallbackQueryHandler,
	filters,
)

REQUESTS_FILE = Path(__file__).with_name("data_requests.json")
REQUEST_FORMS_FILE = Path(__file__).with_name("request_forms.json")
BUTTONS_FILE = Path(__file__).with_name("buttons.json")
ADMINS_FILE = Path(__file__).with_name("admins.json")
REQUEST_BUTTON, REQUEST_FIELD_ACTION, REQUEST_FIELD_VALUE = range(3)
REQUEST_FIELD, REQUEST_ANSWER, REQUEST_FIELD_OPTIONS = range(3, 6)
DEFAULT_FIELDS = [
	{"question": "أرسل اسمك الكامل:", "type": "text", "required": True, "hidden": False},
	{"question": "أرسل رقم الهاتف أو وسيلة التواصل:", "type": "phone", "required": True, "hidden": False},
	{"question": "أرسل ملاحظتك أو اكتب (لا يوجد):", "type": "text", "required": True, "hidden": False},
]


def admin_chat_ids() -> list[str]:
	ids = set()
	primary_id = os.getenv("TELEGRAM_ADMIN_ID")
	if primary_id:
		ids.add(str(primary_id))
	admins = load_json_file(ADMINS_FILE, {})
	users = load_json_file(Path(__file__).with_name("users.json"), {})
	if isinstance(admins, dict):
		for identifier in admins:
			normalized = str(identifier).lstrip("+")
			if normalized.isdigit():
				ids.add(normalized)
			elif isinstance(users, dict):
				for user_id, user in users.items():
					if isinstance(user, dict) and str(user.get("username", "")).casefold() == str(identifier).casefold():
						ids.add(str(user_id))
						break
	return list(ids)


def load_json_file(path: Path, default):
	if not path.exists():
		return default
	try:
		data = json.loads(path.read_text(encoding="utf-8"))
		return data if isinstance(data, type(default)) else default
	except (json.JSONDecodeError, OSError):
		return default


async def notify_admins(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
	for chat_id in admin_chat_ids():
		try:
			await context.bot.send_message(
				chat_id=chat_id,
				text=text,
				parse_mode=telegram.constants.ParseMode.HTML,
			)
		except telegram.error.TelegramError:
			pass


def request_cancel_markup() -> InlineKeyboardMarkup:
	return InlineKeyboardMarkup([
		[InlineKeyboardButton("❌ إلغاء", callback_data="request:cancel")],
	])


def load_requests() -> list[dict[str, str]]:
	if not REQUESTS_FILE.exists():
		return []
	try:
		data = json.loads(REQUESTS_FILE.read_text(encoding="utf-8"))
		return data if isinstance(data, list) else []
	except (json.JSONDecodeError, OSError):
		return []


def save_requests(requests: list[dict[str, str]]) -> None:
	REQUESTS_FILE.write_text(
		json.dumps(requests, ensure_ascii=False, indent=2),
		encoding="utf-8",
	)


def load_request_forms() -> dict[str, list[dict[str, object]]]:
	if not REQUEST_FORMS_FILE.exists():
		return {}
	try:
		data = json.loads(REQUEST_FORMS_FILE.read_text(encoding="utf-8"))
		return data if isinstance(data, dict) else {}
	except (json.JSONDecodeError, OSError):
		return {}


def save_request_forms(forms: dict[str, list[dict[str, object]]]) -> None:
	REQUEST_FORMS_FILE.write_text(
		json.dumps(forms, ensure_ascii=False, indent=2),
		encoding="utf-8",
	)


def request_fields(button_name: str = "") -> list[dict[str, object]]:
	forms = load_request_forms()
	fields = forms.get(button_name) or DEFAULT_FIELDS
	return [field for field in fields if not field.get("hidden", False)]


def request_settings_markup(button_name: str) -> InlineKeyboardMarkup:
	return InlineKeyboardMarkup([
		[InlineKeyboardButton("➕ إضافة طلب", callback_data=f"requestcfg:add:{button_name}")],
		[InlineKeyboardButton("📋 عرض الحقول", callback_data=f"requestcfg:list:{button_name}")],
		[InlineKeyboardButton("⬅️ رجوع", callback_data="set:back")],
	])


def request_config_enabled(button_name: str) -> bool:
	return bool(load_request_forms().get(button_name))


def button_exists(button_name: str) -> bool:
	if not BUTTONS_FILE.exists():
		return False
	try:
		data = json.loads(BUTTONS_FILE.read_text(encoding="utf-8"))
		return isinstance(data, dict) and button_name in data
	except (json.JSONDecodeError, OSError):
		return False


def request_fields_markup(button_name: str) -> InlineKeyboardMarkup:
	forms = load_request_forms()
	fields = forms.get(button_name, DEFAULT_FIELDS)
	rows = []
	for index, field in enumerate(fields):
		status = "✅" if not field.get("hidden", False) else "❌"
		rows.append([InlineKeyboardButton(
			f"{status} {field.get('question', '')}",
			callback_data=f"requestcfg:toggle:{button_name}:{index}",
		)])
		rows.append([InlineKeyboardButton(
			"🗑 حذف",
			callback_data=f"requestcfg:delete:{button_name}:{index}",
		)])
	rows.append([InlineKeyboardButton("➕ إضافة طلب", callback_data=f"requestcfg:add:{button_name}")])
	rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data=f"requestcfg:menu:{button_name}")])
	return InlineKeyboardMarkup(rows)


def request_field_type_markup() -> InlineKeyboardMarkup:
	return InlineKeyboardMarkup([
		[InlineKeyboardButton("📝 نص", callback_data="requesttype:text")],
		[InlineKeyboardButton("🔢 رقم", callback_data="requesttype:number")],
		[InlineKeyboardButton("📧 إيميل", callback_data="requesttype:email")],
		[InlineKeyboardButton("📞 هاتف", callback_data="requesttype:phone")],
		[InlineKeyboardButton("📍 موقع", callback_data="requesttype:location")],
		[InlineKeyboardButton("🖼 وسائط", callback_data="requesttype:media")],
		[InlineKeyboardButton("🔘 اختيار", callback_data="requesttype:choice")],
		[InlineKeyboardButton("✅ أي شيء", callback_data="requesttype:any")],
		[InlineKeyboardButton("⬅️ رجوع", callback_data="requestcfg:back")],
	])


async def request_start(
	update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
	if update.callback_query:
		await update.callback_query.answer()
	user = update.effective_user
	admin_id = os.getenv("TELEGRAM_ADMIN_ID")
	admin_username = os.getenv("TELEGRAM_ADMIN_USERNAME", "mARYAMALJhane").lstrip("@").casefold()
	if (admin_id and str(user.id) == admin_id) or (user.username and user.username.casefold() == admin_username):
		await update.effective_message.reply_text("لا تحتاج إلى إرسال طلب بيانات كمدير.")
		return ConversationHandler.END
	button_name = context.user_data.get("request_button", "")
	if update.callback_query and update.callback_query.data.startswith("request:start:"):
		button_name = update.callback_query.data.removeprefix("request:start:")
		context.user_data["request_button"] = button_name
	fields = request_fields(button_name)
	if not fields:
		await update.effective_message.reply_text("لا توجد حقول مفعلة لهذا الطلب.")
		return ConversationHandler.END
	context.user_data["request_button"] = button_name
	context.user_data["request_fields"] = fields
	context.user_data["request_index"] = 0
	context.user_data["request_answers"] = []
	await update.effective_message.reply_text(
		"📋 طلب بيانات\n\n" + str(fields[0].get("question", "أرسل البيانات:")),
		reply_markup=request_cancel_markup(),
	)
	return REQUEST_ANSWER


async def request_settings_start(
	update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
	if not _has_admin(update):
		await update.effective_message.reply_text("هذا القسم متاح للمشرفين فقط.")
		return ConversationHandler.END
	if context.args:
		context.user_data["request_settings_button"] = " ".join(context.args).strip()
		button_name = context.user_data["request_settings_button"]
		await update.effective_message.reply_text(
			f"إدارة طلبات الزر: {button_name}",
			reply_markup=request_settings_markup(button_name),
		)
		return REQUEST_FIELD_ACTION
	await update.effective_message.reply_text("أرسل اسم الزر الذي تريد إعداد طلباته:")
	return REQUEST_BUTTON


def _has_admin(update: telegram.Update) -> bool:
	user = update.effective_user
	admin_id = os.getenv("TELEGRAM_ADMIN_ID")
	admin_username = os.getenv("TELEGRAM_ADMIN_USERNAME", "mARYAMALJhane").lstrip("@").casefold()
	return (admin_id and str(user.id) == admin_id) or (
		user.username and user.username.casefold() == admin_username
	)


async def request_settings_button(
	update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
	button_name = update.message.text.strip()
	if not button_name or not button_exists(button_name):
		await update.message.reply_text("الزر غير موجود. أرسل اسم زر صحيح:")
		return REQUEST_BUTTON
	context.user_data["request_settings_button"] = button_name
	await update.message.reply_text(
		f"إدارة طلبات الزر: {button_name}",
		reply_markup=request_settings_markup(button_name),
	)
	return REQUEST_FIELD_ACTION


async def request_settings_callback(
	update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
	query = update.callback_query
	await query.answer()
	if not _has_admin(update):
		await query.edit_message_text("هذا القسم متاح للمشرفين فقط.")
		return ConversationHandler.END
	parts = query.data.split(":")
	action = parts[1]
	button_name = parts[2] if len(parts) > 2 else context.user_data.get("request_settings_button", "")
	context.user_data["request_settings_button"] = button_name
	if action == "menu":
		await query.edit_message_text(
			f"إدارة طلبات الزر: {button_name}",
			reply_markup=request_settings_markup(button_name),
		)
		return REQUEST_FIELD_ACTION
	if action == "back":
		await query.edit_message_text(
			f"إدارة طلبات الزر: {button_name}",
			reply_markup=request_settings_markup(button_name),
		)
		return REQUEST_FIELD_ACTION
	if action == "list":
		await query.edit_message_text("حقول الطلب:", reply_markup=request_fields_markup(button_name))
		return REQUEST_FIELD_ACTION
	if action == "add":
		await query.edit_message_text(
			"أرسل نص السؤال، مثال: اكتب اسمك الكامل:",
			reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data=f"requestcfg:menu:{button_name}")]]),
		)
		return REQUEST_FIELD
	if action in ("toggle", "delete") and len(parts) >= 4:
		index = int(parts[3])
		forms = load_request_forms()
		fields = forms.setdefault(button_name, [dict(field) for field in DEFAULT_FIELDS])
		if index < len(fields):
			if action == "toggle":
				fields[index]["hidden"] = not fields[index].get("hidden", False)
			else:
				fields.pop(index)
			save_request_forms(forms)
		await query.edit_message_text("حقول الطلب:", reply_markup=request_fields_markup(button_name))
		return REQUEST_FIELD_ACTION
	return REQUEST_FIELD_ACTION


async def request_add_field(
	update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
	question = update.message.text.strip()
	if not question or len(question) > 200:
		await update.message.reply_text("السؤال مطلوب ولا يتجاوز 200 حرف:")
		return REQUEST_FIELD
	context.user_data["request_field_question"] = question
	await update.message.reply_text(
		"اختر نوع البيانات:", reply_markup=request_field_type_markup()
	)
	return REQUEST_FIELD_ACTION


async def request_field_type(
	update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
	query = update.callback_query
	await query.answer()
	if query.data == "requestcfg:back":
		button_name = context.user_data.get("request_settings_button", "")
		await query.edit_message_text("حقول الطلب:", reply_markup=request_fields_markup(button_name))
		return REQUEST_FIELD_ACTION
	field_type = query.data.removeprefix("requesttype:")
	button_name = context.user_data["request_settings_button"]
	if field_type == "choice":
		context.user_data["request_field_type"] = field_type
		await query.edit_message_text("أرسل الخيارات مفصولة بفاصلة، مثال: نعم,لا")
		return REQUEST_FIELD_OPTIONS
	forms = load_request_forms()
	fields = forms.setdefault(button_name, [dict(field) for field in DEFAULT_FIELDS])
	fields.append({
		"question": context.user_data.pop("request_field_question"),
		"type": field_type,
		"required": True,
		"hidden": False,
		"options": [],
	})
	save_request_forms(forms)
	await query.edit_message_text("تمت إضافة حقل الطلب.", reply_markup=request_fields_markup(button_name))
	return REQUEST_FIELD_ACTION


async def request_field_options(
	update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
	options = [item.strip() for item in update.message.text.split(",") if item.strip()]
	if not options:
		await update.message.reply_text("أرسل خيارًا واحدًا على الأقل مفصولًا بفاصلة:")
		return REQUEST_FIELD_OPTIONS
	button_name = context.user_data["request_settings_button"]
	forms = load_request_forms()
	fields = forms.setdefault(button_name, [dict(field) for field in DEFAULT_FIELDS])
	fields.append({
		"question": context.user_data.pop("request_field_question"),
		"type": "choice",
		"required": True,
		"hidden": False,
		"options": options,
	})
	context.user_data.pop("request_field_type", None)
	save_request_forms(forms)
	await update.message.reply_text("تمت إضافة حقل الاختيار.", reply_markup=request_fields_markup(button_name))
	return REQUEST_FIELD_ACTION


def request_answer_value(update: telegram.Update) -> str:
	message = update.effective_message
	if message.text:
		return message.text.strip()
	if message.location:
		return f"الموقع: {message.location.latitude}, {message.location.longitude}"
	for field in ("photo", "video", "document", "audio", "voice"):
		media = getattr(message, field, None)
		if media:
			item = media[-1] if field == "photo" else media
			return f"{field}: {item.file_id}"
	return ""


def request_answer_valid(value: str, field: dict[str, object]) -> bool:
	if not value:
		return not field.get("required", True)
	field_type = field.get("type", "any")
	if field_type == "number":
		return value.isdigit()
	if field_type == "email":
		return "@" in value and "." in value.rsplit("@", 1)[-1]
	if field_type == "phone":
		return sum(character.isdigit() for character in value) >= 6
	if field_type == "choice":
		return value in field.get("options", [])
	return True


async def request_answer(
	update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
	fields = context.user_data["request_fields"]
	index = context.user_data["request_index"]
	field = fields[index]
	value = request_answer_value(update)
	if not request_answer_valid(value, field):
		await update.effective_message.reply_text("البيانات غير صحيحة لهذا النوع، حاول مرة أخرى:")
		return REQUEST_ANSWER
	context.user_data["request_answers"].append({
		"question": field.get("question", ""), "answer": value,
	})
	index += 1
	if index < len(fields):
		context.user_data["request_index"] = index
		await update.effective_message.reply_text(
			str(fields[index].get("question", "أرسل البيانات:")),
			reply_markup=request_cancel_markup(),
		)
		return REQUEST_ANSWER
	user = update.effective_user
	request = {
		"button": context.user_data.pop("request_button", ""),
		"answers": context.user_data.pop("request_answers"),
		"user_id": str(user.id),
		"name": user.full_name,
		"username": user.username or "",
		"language": user.language_code or "غير محددة",
		"created_at": datetime.now().isoformat(timespec="seconds"),
	}
	requests = load_requests()
	requests.append(request)
	save_requests(requests)
	await notify_admins(
		context,
		"📋 طلب جديد من عميل\n\n"
		"━━━━━━━━━━━━━━━\n\n"
		f"• تم استلام معلومات جديدة من العضو: #{escape(request['button'] or 'طلب عام')}\n\n"
		f"- ايدي العضو: {request['user_id']}\n"
		f"- اسم العضو: {escape(request['name'])}\n"
		f"- معرف العضو: {escape('@' + request['username'] if request['username'] else 'بدون معرف')}\n"
		f"- اللغة: {escape(request['language'])}\n\n"
		"• البيانات التي تم ادخالها:\n\n"
		+ "\n\n".join(
			f"{index}. {escape(str(item['question']))}:\n{escape(str(item['answer']))}"
			for index, item in enumerate(request["answers"], 1)
		)
		+ f"\n\n• الوقت: {request['created_at']}\n• إجمالي الطلبات: {len(requests)}"
	)
	await update.message.reply_text("✅ تم استلام بياناتك وإرسالها للمسؤول.")
	return ConversationHandler.END


async def request_cancel(
	update: telegram.Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
	context.user_data.pop("request_name", None)
	context.user_data.pop("request_phone", None)
	context.user_data.pop("request_button", None)
	context.user_data.pop("request_fields", None)
	context.user_data.pop("request_index", None)
	context.user_data.pop("request_answers", None)
	if update.callback_query:
		await update.callback_query.answer()
		await update.callback_query.edit_message_text("تم إلغاء طلب البيانات.")
	else:
		await update.effective_message.reply_text("تم إلغاء طلب البيانات.")
	return ConversationHandler.END


def build_data_request_handler() -> ConversationHandler:
	return ConversationHandler(
		entry_points=[
			CommandHandler("requestdata", request_start),
			CallbackQueryHandler(request_start, pattern=r"^request:start$"),
			CallbackQueryHandler(request_start, pattern=r"^request:start:.+$"),
		],
		states={
			REQUEST_ANSWER: [MessageHandler(filters.ALL & ~filters.COMMAND, request_answer)],
		},
		fallbacks=[
			CommandHandler("cancel", request_cancel),
			CallbackQueryHandler(request_cancel, pattern=r"^request:cancel$"),
		],
	)


def build_request_settings_handler() -> ConversationHandler:
	return ConversationHandler(
		entry_points=[CommandHandler("requestsettings", request_settings_start)],
		states={
			REQUEST_BUTTON: [MessageHandler(filters.TEXT & ~filters.COMMAND, request_settings_button)],
			REQUEST_FIELD_ACTION: [
				CallbackQueryHandler(request_settings_callback, pattern=r"^requestcfg:"),
				CallbackQueryHandler(request_field_type, pattern=r"^requesttype:"),
			],
			REQUEST_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, request_add_field)],
			REQUEST_ANSWER: [MessageHandler(filters.ALL & ~filters.COMMAND, request_answer)],
			REQUEST_FIELD_OPTIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, request_field_options)],
		},
		fallbacks=[CommandHandler("cancel", request_cancel)],
	)
