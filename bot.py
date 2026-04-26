import os
import json
import logging
import asyncio
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
import google.generativeai as genai

# Настройки
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY")
MEMORY_FILE    = "memory.json"
TIMEZONE       = ZoneInfo("Europe/Moscow")

MAX_HISTORY_STORED  = 2000
MAX_HISTORY_CONTEXT = 80

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.0-flash")


def now_str() -> str:
    return datetime.now(TIMEZONE).isoformat()


def now_dt() -> datetime:
    return datetime.now(TIMEZONE)


def fmt_dt(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m.%Y %H:%M")
    except Exception:
        return iso


def load_memory() -> dict:
    if Path(MEMORY_FILE).exists():
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_memory(memory: dict):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)


def _default_user() -> dict:
    return {
        "notes": [],
        "tasks": [],
        "events": [],
        "chat_history": [],
        "profile": {
            "name": "",
            "interests": [],
            "facts": [],
            "style_notes": [],
        },
    }


def get_user_data(user_id: int) -> dict:
    memory = load_memory()
    uid = str(user_id)
    if uid not in memory:
        memory[uid] = _default_user()
        save_memory(memory)
    data = memory[uid]
    defaults = _default_user()
    for key, val in defaults.items():
        if key not in data:
            data[key] = val
    if not isinstance(data.get("profile"), dict):
        data["profile"] = defaults["profile"]
    for k, v in defaults["profile"].items():
        if k not in data["profile"]:
            data["profile"][k] = v
    return data


def save_user_data(user_id: int, data: dict):
    memory = load_memory()
    memory[str(user_id)] = data
    save_memory(memory)


def add_note(user_id: int, text: str) -> str:
    data = get_user_data(user_id)
    note_id = (data["notes"][-1]["id"] + 1) if data["notes"] else 1
    data["notes"].append({"id": note_id, "text": text, "created_at": now_str()})
    save_user_data(user_id, data)
    return f"✅ Заметка #{note_id} сохранена."


def get_notes_text(user_id: int) -> str:
    data = get_user_data(user_id)
    notes = data["notes"]
    if not notes:
        return "📭 Заметок нет."
    lines = ["📝 *Заметки:*\n"]
    for n in notes[-30:]:
        lines.append(f"  `#{n['id']}` [{fmt_dt(n['created_at'])}]\n  {n['text']}\n")
    return "\n".join(lines)


def add_task(user_id: int, text: str) -> str:
    data = get_user_data(user_id)
    task_id = (data["tasks"][-1]["id"] + 1) if data["tasks"] else 1
    data["tasks"].append({"id": task_id, "text": text, "done": False, "created_at": now_str()})
    save_user_data(user_id, data)
    return f"📌 Задача #{task_id} добавлена."


def complete_task(user_id: int, task_id: int) -> str:
    data = get_user_data(user_id)
    for t in data["tasks"]:
        if t["id"] == task_id:
            t["done"] = True
            t["done_at"] = now_str()
            save_user_data(user_id, data)
            return f"✅ Задача #{task_id} выполнена!"
    return f"❌ Задача #{task_id} не найдена."


def get_tasks_text(user_id: int) -> str:
    data = get_user_data(user_id)
    tasks = data["tasks"]
    if not tasks:
        return "📭 Задач нет."
    active = [t for t in tasks if not t["done"]]
    done   = [t for t in tasks if t["done"]]
    lines  = ["📋 *Задачи:*\n"]
    if active:
        lines.append("*Активные:*")
        for t in active:
            lines.append(f"  ⬜ `#{t['id']}` {t['text']}")
    if done:
        lines.append("\n*Выполненные (последние 5):*")
        for t in done[-5:]:
            lines.append(f"  ✅ `#{t['id']}` {t['text']}")
    return "\n".join(lines)


def add_event(user_id: int, title: str, event_dt: datetime, description: str = "") -> str:
    data = get_user_data(user_id)
    ev_id = (data["events"][-1]["id"] + 1) if data["events"] else 1
    data["events"].append({
        "id": ev_id,
        "title": title,
        "description": description,
        "event_at": event_dt.isoformat(),
        "reminded": False,
        "done": False,
        "created_at": now_str(),
    })
    save_user_data(user_id, data)
    return f"📅 Событие «{title}» добавлено на {fmt_dt(event_dt.isoformat())}."


def get_events_text(user_id: int) -> str:
    data = get_user_data(user_id)
    events = [e for e in data["events"] if not e.get("done")]
    if not events:
        return "📭 Событий нет."
    events.sort(key=lambda e: e["event_at"])
    lines = ["📅 *События и напоминания:*\n"]
    for e in events:
        lines.append(f"  `#{e['id']}` *{e['title']}*")
        lines.append(f"  🕒 {fmt_dt(e['event_at'])}")
        if e.get("description"):
            lines.append(f"  _{e['description']}_")
        lines.append("")
    return "\n".join(lines)


def get_upcoming_events(user_id: int, window_minutes: int = 60) -> list:
    data = get_user_data(user_id)
    result = []
    now = now_dt()
    for e in data["events"]:
        if e.get("done") or e.get("reminded"):
            continue
        try:
            ev_dt = datetime.fromisoformat(e["event_at"])
            if ev_dt.tzinfo is None:
                ev_dt = ev_dt.replace(tzinfo=TIMEZONE)
            diff = (ev_dt - now).total_seconds() / 60
            if -5 <= diff <= window_minutes:
                result.append(e)
        except Exception:
            pass
    return result


def mark_event_reminded(user_id: int, ev_id: int):
    data = get_user_data(user_id)
    for e in data["events"]:
        if e["id"] == ev_id:
            e["reminded"] = True
    save_user_data(user_id, data)


def delete_event(user_id: int, ev_id: int) -> str:
    data = get_user_data(user_id)
    for e in data["events"]:
        if e["id"] == ev_id:
            e["done"] = True
            save_user_data(user_id, data)
            return f"🗑 Событие #{ev_id} удалено."
    return f"❌ Событие #{ev_id} не найдено."


def append_history(user_id: int, role: str, text: str):
    data = get_user_data(user_id)
    data["chat_history"].append({"role": role, "text": text, "ts": now_str()})
    if len(data["chat_history"]) > MAX_HISTORY_STORED:
        data["chat_history"] = data["chat_history"][-MAX_HISTORY_STORED:]
    save_user_data(user_id, data)


async def update_profile_async(user_id: int, user_text: str):
    data = get_user_data(user_id)
    profile = data["profile"]
    try:
        prompt = f"""Анализируй сообщение пользователя и обнови его профиль.
Текущий профиль:
- Имя: {profile.get('name', 'неизвестно')}
- Интересы: {', '.join(profile.get('interests', [])) or 'нет'}
- Факты: {'; '.join(profile.get('facts', [])) or 'нет'}
- Стиль общения: {'; '.join(profile.get('style_notes', [])) or 'нет'}

Сообщение: "{user_text}"

Верни ТОЛЬКО JSON без markdown:
{{
  "name": "имя если упомянул, иначе пустая строка",
  "new_interests": ["новые интересы если есть"],
  "new_facts": ["новые факты о человеке если есть"],
  "new_style_notes": ["заметки о стиле общения если есть"]
}}"""
        resp = model.generate_content(prompt)
        raw = resp.text.strip().replace("```json", "").replace("```", "").strip()
        parsed = json.loads(raw)
        if parsed.get("name"):
            profile["name"] = parsed["name"]
        profile["interests"]   = list(set(profile["interests"]   + parsed.get("new_interests", [])))[:20]
        profile["facts"]       = list(set(profile["facts"]       + parsed.get("new_facts", [])))[:30]
        profile["style_notes"] = list(set(profile["style_notes"] + parsed.get("new_style_notes", [])))[:15]
        data["profile"] = profile
        save_user_data(user_id, data)
    except Exception as e:
        logger.debug(f"Profile update skipped: {e}")


def build_context(user_id: int, user_message: str) -> str:
    data = get_user_data(user_id)
    profile = data["profile"]

    profile_block = (
        f"Имя: {profile.get('name') or 'неизвестно'}\n"
        f"Интересы: {', '.join(profile.get('interests', [])) or 'нет'}\n"
        f"Факты: {chr(10).join('- ' + f for f in profile.get('facts', [])) or 'нет'}\n"
        f"Стиль общения: {'; '.join(profile.get('style_notes', [])) or 'не определён'}"
    )

    notes_block = "\n".join(
        f"#{n['id']} [{fmt_dt(n['created_at'])}]: {n['text']}"
        for n in data["notes"][-15:]
    ) or "нет"

    active_tasks = [t for t in data["tasks"] if not t["done"]]
    tasks_block  = "\n".join(f"#{t['id']}: {t['text']}" for t in active_tasks) or "нет"

    now = now_dt()
    upcoming = sorted(
        [e for e in data["events"] if not e.get("done")],
        key=lambda e: e["event_at"]
    )[:10]
    events_block = "\n".join(
        f"#{e['id']} {e['title']} — {fmt_dt(e['event_at'])}"
        + (f": {e['description']}" if e.get("description") else "")
        for e in upcoming
    ) or "нет"

    history = data["chat_history"][-MAX_HISTORY_CONTEXT:]
    history_block = "\n".join(
        f"[{fmt_dt(m['ts'])}] {'Пользователь' if m['role'] == 'user' else 'Алекс'}: {m['text']}"
        for m in history
    ) or "нет истории"

    return f"""Ты — умный личный секретарь по имени Алекс в Telegram.
Сейчас: {now.strftime('%d.%m.%Y %H:%M')}

ПРАВИЛА:
1. Ты помнишь ВСЮ историю — никогда не говори "я не помню".
2. Ты адаптируешься к стилю пользователя на основе профиля.
3. Ты понимаешь контекстные вопросы ("оно", "это", "там" — ты понимаешь о чём речь из истории).
4. Проактивно упоминаешь задачи и события если уместно.
5. Если пользователь упоминает новое дело — предложи сохранить.
6. Отвечай кратко и по делу. Без лишних вступлений.
7. Отвечай на том же языке что и пользователь.

════ ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ ════
{profile_block}

════ ЗАМЕТКИ (последние 15) ════
{notes_block}

════ АКТИВНЫЕ ЗАДАЧИ ════
{tasks_block}

════ ПРЕДСТОЯЩИЕ СОБЫТИЯ ════
{events_block}

════ ИСТОРИЯ ДИАЛОГА ════
{history_block}

════ ТЕКУЩЕЕ СООБЩЕНИЕ ════
Пользователь: {user_message}

Ответь с учётом всего контекста выше."""


async def parse_event_from_text(text: str) -> dict | None:
    now = now_dt()
    try:
        prompt = f"""Текущая дата и время: {now.strftime('%d.%m.%Y %H:%M')}

Определи, есть ли в тексте пользователя событие или напоминание.
Текст: "{text}"

Если есть — верни ТОЛЬКО JSON:
{{"found": true, "title": "название", "description": "описание или пустая строка", "datetime": "YYYY-MM-DDTHH:MM:00"}}

Если нет — верни ТОЛЬКО:
{{"found": false}}"""
        resp = model.generate_content(prompt)
        raw = resp.text.strip().replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        logger.debug(f"Event parse failed: {e}")
        return None


async def transcribe_voice(file_path: str) -> str:
    try:
        audio_file = genai.upload_file(file_path, mime_type="audio/ogg")
        response = model.generate_content([
            audio_file,
            "Транскрибируй это голосовое сообщение дословно. Верни только текст без пояснений."
        ])
        return response.text.strip()
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        return ""


async def reminder_loop(app: Application):
    await asyncio.sleep(15)
    while True:
        try:
            memory = load_memory()
            for uid_str, data in memory.items():
                uid = int(uid_str)
                upcoming = get_upcoming_events(uid, window_minutes=60)
                for ev in upcoming:
                    try:
                        ev_dt = datetime.fromisoformat(ev["event_at"])
                        if ev_dt.tzinfo is None:
                            ev_dt = ev_dt.replace(tzinfo=TIMEZONE)
                        diff_min = int((ev_dt - now_dt()).total_seconds() / 60)
                        if diff_min <= 0:
                            msg = f"🔔 *Время пришло!*\n\n📅 *{ev['title']}*"
                        elif diff_min <= 15:
                            msg = f"⏰ *Через {diff_min} мин:*\n\n📅 *{ev['title']}*"
                        else:
                            msg = f"📅 *Напоминание* через {diff_min} мин\n\n*{ev['title']}*"
                        if ev.get("description"):
                            msg += f"\n_{ev['description']}_"
                        await app.bot.send_message(chat_id=uid, text=msg, parse_mode="Markdown")
                        mark_event_reminded(uid, ev["id"])
                        logger.info(f"Reminder sent to {uid} for event #{ev['id']}")
                    except Exception as e:
                        logger.error(f"Reminder send error: {e}")
        except Exception as e:
            logger.error(f"Reminder loop error: {e}")
        await asyncio.sleep(60)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = get_user_data(user.id)
    if not data["profile"]["name"] and user.first_name:
        data["profile"]["name"] = user.first_name
        save_user_data(user.id, data)
    keyboard = [
        [InlineKeyboardButton("📝 Заметки",  callback_data="show_notes"),
         InlineKeyboardButton("📋 Задачи",   callback_data="show_tasks")],
        [InlineKeyboardButton("📅 События",  callback_data="show_events"),
         InlineKeyboardButton("❓ Помощь",   callback_data="help")],
    ]
    await update.message.reply_text(
        f"👋 Привет, *{user.first_name}*!\n\n"
        f"Я *Алекс* — твой личный AI-секретарь.\n\n"
        f"🧠 Помню *всю* нашу историю\n"
        f"🎤 Понимаю голосовые сообщения\n"
        f"🔔 Напоминаю о событиях автоматически\n"
        f"👤 Адаптируюсь к тебе со временем\n\n"
        f"*Команды:*\n"
        f"/note `текст` — заметка\n"
        f"/task `текст` — задача\n"
        f"/event `название; дата время; описание` — событие\n"
        f"/notes · /tasks · /events — посмотреть списки\n"
        f"/done `N` — выполнить задачу N\n"
        f"/delevent `N` — удалить событие\n"
        f"/forget — очистить всё\n\n"
        f"Или просто пиши / говори 💬🎤",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Алекс — команды:*\n\n"
        "📝 `/note текст` — заметка\n"
        "📝 `/notes` — все заметки\n\n"
        "📋 `/task текст` — задача\n"
        "📋 `/tasks` — все задачи\n"
        "✅ `/done N` — выполнить задачу\n\n"
        "📅 `/event Название; 2025-06-01 14:00; описание`\n"
        "📅 `/events` — все события\n"
        "🗑 `/delevent N` — удалить событие\n\n"
        "🎤 Голосовые — просто отправь\n"
        "🗑 `/forget` — очистить всю память",
        parse_mode="Markdown",
    )


async def cmd_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text("✏️ `/note твой текст`", parse_mode="Markdown")
        return
    await update.message.reply_text(add_note(update.effective_user.id, text))


async def cmd_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text("✏️ `/task купить молоко`", parse_mode="Markdown")
        return
    await update.message.reply_text(add_task(update.effective_user.id, text))


async def cmd_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(get_notes_text(update.effective_user.id), parse_mode="Markdown")


async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(get_tasks_text(update.effective_user.id), parse_mode="Markdown")


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Укажи номер: `/done 1`", parse_mode="Markdown")
        return
    try:
        await update.message.reply_text(complete_task(update.effective_user.id, int(context.args[0])))
    except ValueError:
        await update.message.reply_text("❌ Номер должен быть числом.")


async def cmd_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    raw = " ".join(context.args)
    if not raw:
        await update.message.reply_text(
            "📅 Формат:\n`/event Встреча; 2025-06-01 14:00; с Иваном`\n\nОписание — необязательно.",
            parse_mode="Markdown",
        )
        return
    parts = [p.strip() for p in raw.split(";")]
    if len(parts) < 2:
        await update.message.reply_text(
            "❌ Минимум: название и дата.\n`/event Встреча; 2025-06-01 14:00`",
            parse_mode="Markdown",
        )
        return
    title = parts[0]
    description = parts[2] if len(parts) > 2 else ""
    try:
        ev_dt = datetime.strptime(parts[1].strip(), "%Y-%m-%d %H:%M").replace(tzinfo=TIMEZONE)
    except ValueError:
        await update.message.reply_text(
            "❌ Формат даты: `ГГГГ-ММ-ДД ЧЧ:ММ`\nПример: `2025-06-01 14:00`",
            parse_mode="Markdown",
        )
        return
    await update.message.reply_text(add_event(user_id, title, ev_dt, description))


async def cmd_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(get_events_text(update.effective_user.id), parse_mode="Markdown")


async def cmd_delevent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Укажи номер: `/delevent 1`", parse_mode="Markdown")
        return
    try:
        await update.message.reply_text(delete_event(update.effective_user.id, int(context.args[0])))
    except ValueError:
        await update.message.reply_text("❌ Номер должен быть числом.")


async def cmd_forget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_data(update.effective_user.id, _default_user())
    await update.message.reply_text("🗑 Вся память очищена.")


async def process_message(user_id: int, text: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
    append_history(user_id, "user", text)
    asyncio.create_task(update_profile_async(user_id, text))

    event_suggestion = ""
    event_parsed = await parse_event_from_text(text)
    if event_parsed and event_parsed.get("found"):
        try:
            ev_dt = datetime.fromisoformat(event_parsed["datetime"]).replace(tzinfo=TIMEZONE)
            result = add_event(user_id, event_parsed["title"], ev_dt, event_parsed.get("description", ""))
            event_suggestion = f"\n\n🔔 *Автосохранено:* {result}"
        except Exception as e:
            logger.debug(f"Auto-event save failed: {e}")

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        prompt = build_context(user_id, text)
        response = model.generate_content(prompt)
        reply = response.text
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        reply = f"⚠️ Ошибка AI: {e}"

    append_history(user_id, "assistant", reply)
    await update.message.reply_text(reply + event_suggestion, parse_mode="Markdown")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await process_message(update.effective_user.id, update.message.text, update, context)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text("🎤 Распознаю голос...")

    voice_file = await context.bot.get_file(update.message.voice.file_id)
    local_path = f"/tmp/voice_{user_id}_{update.message.voice.file_id}.ogg"
    await voice_file.download_to_drive(local_path)

    transcribed = await transcribe_voice(local_path)

    try:
        os.remove(local_path)
    except Exception:
        pass

    if not transcribed:
        await update.message.reply_text("❌ Не удалось распознать голос. Попробуй ещё раз.")
        return

    await update.message.reply_text(f"🎤 *Распознано:* _{transcribed}_", parse_mode="Markdown")
    await process_message(user_id, transcribed, update, context)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    handlers = {
        "show_notes":  lambda: get_notes_text(uid),
        "show_tasks":  lambda: get_tasks_text(uid),
        "show_events": lambda: get_events_text(uid),
        "help": lambda: (
            "📝 /note · /notes\n"
            "📋 /task · /tasks · /done N\n"
            "📅 /event · /events · /delevent N\n"
            "🗑 /forget"
        ),
    }
    fn = handlers.get(query.data)
    if fn:
        await query.message.reply_text(fn(), parse_mode="Markdown")


async def post_init(app: Application):
    asyncio.create_task(reminder_loop(app))


def main():
    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("help",     cmd_help))
    app.add_handler(CommandHandler("note",     cmd_note))
    app.add_handler(CommandHandler("notes",    cmd_notes))
    app.add_handler(CommandHandler("task",     cmd_task))
    app.add_handler(CommandHandler("tasks",    cmd_tasks))
    app.add_handler(CommandHandler("done",     cmd_done))
    app.add_handler(CommandHandler("event",    cmd_event))
    app.add_handler(CommandHandler("events",   cmd_events))
    app.add_handler(CommandHandler("delevent", cmd_delevent))
    app.add_handler(CommandHandler("forget",   cmd_forget))
    app.add_handler(CommandHandler("clear",    cmd_forget))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("🤖 Секретарь Алекс запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
