"""
APK Builder Bot — Professional Edition
Stack: aiogram 3.x (bot) + Telethon (userbot for >50 MB delivery)
"""

import asyncio
import logging
import random
import string
import os
import sys
from datetime import datetime
from io import BytesIO
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, FSInputFile, BufferedInputFile,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from PIL import Image, ImageDraw, ImageFont
import aiosqlite

from config import Config
from userbot import UserbotManager
from converter import convert_to_apk, cleanup_build

# ─────────────────────── Logging ───────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("apk_bot")

# ─────────────────────── Core objects ──────────────────
bot = Bot(token=Config.BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)
userbot_manager = UserbotManager()

# ═══════════════════════ FSM STATES ════════════════════
class BuildStates(StatesGroup):
    waiting_name   = State()
    waiting_icon   = State()

class AdminStates(StatesGroup):
    waiting_broadcast     = State()
    waiting_force_add     = State()
    waiting_force_remove  = State()
    waiting_profile_photo = State()
    waiting_profile_text  = State()
    waiting_userbot_phone = State()
    waiting_userbot_otp   = State()
    waiting_userbot_2fa   = State()

# ═══════════════════════ DATABASE ══════════════════════
async def init_db():
    async with aiosqlite.connect(Config.DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id    INTEGER PRIMARY KEY,
                username   TEXT,
                full_name  TEXT,
                joined_at  TEXT,
                verified   INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS apks (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER,
                name           TEXT,
                icon_file_id   TEXT,
                source_file_id TEXT,
                source_type    TEXT,
                created_at     TEXT,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            );
            CREATE TABLE IF NOT EXISTS force_joins (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id  TEXT UNIQUE,
                title    TEXT,
                link     TEXT,
                type     TEXT DEFAULT 'channel'
            );
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        await db.commit()


async def add_user(user_id: int, username: str, full_name: str):
    async with aiosqlite.connect(Config.DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, full_name, joined_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, username or "", full_name or "", datetime.now().isoformat()),
        )
        await db.commit()


async def set_verified(user_id: int):
    async with aiosqlite.connect(Config.DB_PATH) as db:
        await db.execute("UPDATE users SET verified = 1 WHERE user_id = ?", (user_id,))
        await db.commit()


async def is_verified(user_id: int) -> bool:
    async with aiosqlite.connect(Config.DB_PATH) as db:
        async with db.execute(
            "SELECT verified FROM users WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return bool(row and row[0])


async def save_apk(
    user_id: int,
    name: str,
    icon_file_id: Optional[str],
    source_file_id: str,
    source_type: str,
):
    async with aiosqlite.connect(Config.DB_PATH) as db:
        await db.execute(
            "INSERT INTO apks (user_id, name, icon_file_id, source_file_id, source_type, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, name, icon_file_id, source_file_id, source_type, datetime.now().isoformat()),
        )
        await db.commit()


async def get_user_apks(user_id: int):
    async with aiosqlite.connect(Config.DB_PATH) as db:
        async with db.execute(
            "SELECT id, name, icon_file_id, source_file_id, created_at "
            "FROM apks WHERE user_id = ? ORDER BY id DESC LIMIT 20",
            (user_id,),
        ) as cur:
            return await cur.fetchall()


async def delete_apk(apk_id: int, user_id: int):
    async with aiosqlite.connect(Config.DB_PATH) as db:
        await db.execute(
            "DELETE FROM apks WHERE id = ? AND user_id = ?", (apk_id, user_id)
        )
        await db.commit()


async def get_apk(apk_id: int):
    async with aiosqlite.connect(Config.DB_PATH) as db:
        async with db.execute("SELECT * FROM apks WHERE id = ?", (apk_id,)) as cur:
            return await cur.fetchone()


async def get_stats():
    async with aiosqlite.connect(Config.DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as c:
            users = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM apks") as c:
            apks = (await c.fetchone())[0]
        return users, apks


async def get_all_user_ids():
    async with aiosqlite.connect(Config.DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users") as cur:
            rows = await cur.fetchall()
            return [r[0] for r in rows]


async def add_force_join(chat_id: str, title: str, link: str, type_: str = "channel"):
    async with aiosqlite.connect(Config.DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO force_joins (chat_id, title, link, type) VALUES (?, ?, ?, ?)",
            (str(chat_id), title, link, type_),
        )
        await db.commit()


async def remove_force_join(chat_id: str):
    async with aiosqlite.connect(Config.DB_PATH) as db:
        await db.execute(
            "DELETE FROM force_joins WHERE chat_id = ?", (str(chat_id),)
        )
        await db.commit()


async def get_force_joins():
    async with aiosqlite.connect(Config.DB_PATH) as db:
        async with db.execute(
            "SELECT chat_id, title, link, type FROM force_joins"
        ) as cur:
            return await cur.fetchall()


async def set_setting(key: str, value: str):
    async with aiosqlite.connect(Config.DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
        )
        await db.commit()


async def get_setting(key: str) -> Optional[str]:
    async with aiosqlite.connect(Config.DB_PATH) as db:
        async with db.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


# ═══════════════════════ CAPTCHA ═══════════════════════
def generate_captcha():
    letters = random.sample(string.ascii_uppercase, k=6)
    target  = random.choice(letters)
    img     = Image.new("RGB", (460, 170), (18, 18, 28))
    draw    = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 54)
    except Exception:
        font = ImageFont.load_default()

    positions = []
    x = 28
    for letter in letters:
        y = random.randint(38, 72)
        color = (
            random.randint(170, 255),
            random.randint(170, 255),
            random.randint(170, 255),
        )
        draw.text((x, y), letter, fill=color, font=font)
        positions.append((letter, x, y))
        x += 68

    # Highlight circle
    for letter, lx, ly in positions:
        if letter == target:
            accent = random.choice(
                [(255, 70, 70), (70, 220, 70), (70, 160, 255), (255, 210, 50)]
            )
            draw.ellipse(
                [lx - 10, ly - 10, lx + 52, ly + 62],
                outline=accent,
                width=4,
            )
            break

    # Noise dots
    for _ in range(90):
        draw.point(
            (random.randint(0, 459), random.randint(0, 169)),
            fill=(random.randint(0, 60),) * 3,
        )

    bio = BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    return bio, target, letters


# ═══════════════════════ KEYBOARDS ═════════════════════
# Note: Telegram bots cannot change button background color via Bot API.
# "Colored" UX is achieved with emoji prefixes and structured layouts.

def kb_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📂  My APKs", callback_data="my_apks")],
        [
            InlineKeyboardButton(text="ℹ️  Help",    callback_data="cb_help"),
            InlineKeyboardButton(text="💬  Support", url=f"https://t.me/{Config.SUPPORT_USERNAME}"),
        ],
    ])


def kb_skip_icon():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭  Skip Icon", callback_data="skip_icon")]
    ])


def kb_apk_actions(apk_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⬇️  Download", callback_data=f"dl_{apk_id}"),
            InlineKeyboardButton(text="🗑  Delete",    callback_data=f"del_{apk_id}"),
        ],
        [InlineKeyboardButton(text="◀️  Back",       callback_data="my_apks")],
    ])


def kb_force_join(joins):
    buttons = [
        [InlineKeyboardButton(text=f"➕  Join {title}", url=link)]
        for _, title, link, _ in joins
    ]
    buttons.append(
        [InlineKeyboardButton(text="✅  I've Joined — Check", callback_data="check_force")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_admin():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📢  Broadcast",  callback_data="admin_broadcast"),
            InlineKeyboardButton(text="📊  Stats",      callback_data="admin_stats"),
        ],
        [
            InlineKeyboardButton(text="🔒  Force Join", callback_data="admin_force"),
            InlineKeyboardButton(text="🖼  Start Page",  callback_data="admin_profile"),
        ],
        [InlineKeyboardButton(text="🤖  Userbot",       callback_data="admin_userbot")],
    ])


def kb_force_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕  Add",    callback_data="force_add"),
            InlineKeyboardButton(text="➖  Remove", callback_data="force_remove"),
        ],
        [InlineKeyboardButton(text="📋  List",     callback_data="force_list")],
        [InlineKeyboardButton(text="◀️  Back",     callback_data="admin_panel")],
    ])


def kb_userbot_menu():
    connected = userbot_manager.is_connected()
    status    = "🟢  Connected" if connected else "🔴  Not Connected"
    buttons   = [
        [InlineKeyboardButton(text=f"Status: {status}", callback_data="ub_status")],
        [InlineKeyboardButton(text="➕  Add Userbot Account", callback_data="ub_add")],
    ]
    if connected:
        buttons.append(
            [InlineKeyboardButton(text="🔌  Disconnect", callback_data="ub_disconnect")]
        )
    buttons.append([InlineKeyboardButton(text="◀️  Back", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_back_admin():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️  Back", callback_data="admin_panel")]
    ])


def kb_large_file_offer(apk_id: int):
    """Shown when bot's own 50 MB limit is hit — userbot can deliver instead."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="⚡  Hit limit — send via userbot account",
                callback_data=f"ub_send_{apk_id}",
            )
        ]
    ])


# ═══════════════════════ HELPERS ═══════════════════════
async def safe_reply(message: Message, text: str, **kwargs) -> Message:
    kwargs.setdefault("parse_mode", ParseMode.HTML)
    try:
        return await message.reply(text, **kwargs)
    except Exception:
        return await message.answer(text, **kwargs)


async def check_force_join(user_id: int) -> bool:
    joins = await get_force_joins()
    if not joins:
        return True
    for chat_id, _, _, _ in joins:
        try:
            member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
                return False
        except Exception:
            return False
    return True


async def notify_owner(text: str, photo: Optional[str] = None):
    try:
        if photo:
            await bot.send_photo(
                Config.OWNER_ID, photo, caption=text, parse_mode=ParseMode.HTML
            )
        else:
            await bot.send_message(Config.OWNER_ID, text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Owner notify failed: {e}")


def is_owner(user_id: int) -> bool:
    return user_id == Config.OWNER_ID


WELCOME_TEXT = (
    "<b>🛠  APK Builder Bot</b>\n\n"
    "Send me an <b>HTML</b> or <b>ZIP</b> file and I'll wrap it into an APK.\n\n"
    "• Send <code>.html</code> or <code>.zip</code>\n"
    "• Enter the app name\n"
    "• (Optional) send an icon image\n"
    "• Your APK is ready in seconds!"
)

HELP_TEXT = (
    "<b>❓ How to use this bot</b>\n\n"
    "1️⃣  Send a <code>.html</code> or <code>.zip</code> file\n"
    "2️⃣  Enter your desired app name (max 30 chars)\n"
    "3️⃣  Send an icon image, or tap <b>Skip</b>\n"
    "4️⃣  Your APK is queued — download from <b>My APKs</b>\n\n"
    "<b>Limits</b>\n"
    "• Max file size: 50 MB (Telegram Bot API limit)\n"
    "• If the resulting APK is &gt;50 MB, tap the button below the\n"
    "  download link and we'll send it via our secondary account.\n\n"
    f"Need help? Contact @{Config.SUPPORT_USERNAME}"
)


# ═══════════════════════ /START ════════════════════════
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user = message.from_user
    await add_user(user.id, user.username, user.full_name)

    # Animated progress bar
    bar_msg = await safe_reply(message, "<b>Initialising…</b>\n\n▱▱▱▱▱▱▱▱▱▱")
    for i in range(1, 11):
        bar = "▰" * i + "▱" * (10 - i)
        try:
            await bar_msg.edit_text(f"<b>Initialising…</b>\n\n{bar}", parse_mode=ParseMode.HTML)
        except Exception:
            pass
        await asyncio.sleep(0.10)
    await asyncio.sleep(0.3)
    try:
        await bar_msg.delete()
    except Exception:
        pass

    # CAPTCHA
    bio, target, letters = generate_captcha()
    await state.update_data(captcha_target=target)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{l}", callback_data=f"cap_{l}") for l in letters[:3]],
        [InlineKeyboardButton(text=f"{l}", callback_data=f"cap_{l}") for l in letters[3:]],
    ])

    await message.answer_photo(
        BufferedInputFile(bio.read(), filename="captcha.png"),
        caption=(
            "🔐 <b>Human Verification</b>\n\n"
            "Select the <b>circled letter</b> from the image below 👇"
        ),
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
        reply_to_message_id=message.message_id,
    )


@router.callback_query(F.data.startswith("cap_"))
async def captcha_handler(callback: CallbackQuery, state: FSMContext):
    data   = await state.get_data()
    target = data.get("captcha_target")
    chosen = callback.data.split("_")[1]

    if chosen != target:
        await callback.answer("❌ Wrong letter! Try again.", show_alert=True)
        return

    await set_verified(callback.from_user.id)
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass

    # Force-join gate
    if not await check_force_join(callback.from_user.id):
        joins = await get_force_joins()
        await callback.message.answer(
            "🔒 <b>Join Required</b>\n\n"
            "Please join the channels/groups below, then tap <b>✅ I've Joined</b>.",
            reply_markup=kb_force_join(joins),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()
        return

    await _send_welcome(callback.message)
    await callback.answer("✅ Verified!")


async def _send_welcome(message: Message):
    custom_text  = await get_setting("start_text")
    custom_photo = await get_setting("start_photo")
    text = custom_text or WELCOME_TEXT
    if custom_photo:
        await message.answer_photo(
            custom_photo, caption=text,
            reply_markup=kb_main_menu(), parse_mode=ParseMode.HTML,
        )
    else:
        await message.answer(text, reply_markup=kb_main_menu(), parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "check_force")
async def check_force_cb(callback: CallbackQuery):
    if await check_force_join(callback.from_user.id):
        try:
            await callback.message.delete()
        except Exception:
            pass
        await _send_welcome(callback.message)
    else:
        await callback.answer("❌ You haven't joined all required channels yet!", show_alert=True)
    await callback.answer()


@router.callback_query(F.data == "cb_help")
async def cb_help(callback: CallbackQuery):
    await callback.message.edit_text(
        HELP_TEXT,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️  Back", callback_data="back_main")]
        ]),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


# ═══════════════════════ FILE HANDLING ═════════════════
@router.message(F.document)
async def handle_document(message: Message, state: FSMContext):
    if not await is_verified(message.from_user.id):
        await safe_reply(message, "Please run /start and complete verification first.")
        return
    if not await check_force_join(message.from_user.id):
        joins = await get_force_joins()
        await safe_reply(
            message,
            "🔒 <b>Join Required</b>\n\nJoin the required channels first.",
            reply_markup=kb_force_join(joins),
        )
        return

    doc  = message.document
    mime = (doc.mime_type or "").lower()
    fname = (doc.file_name or "").lower()

    if not (
        fname.endswith((".html", ".htm", ".zip"))
        or "html" in mime
        or "zip" in mime
    ):
        await safe_reply(message, "❌ Please send an <b>HTML</b> or <b>ZIP</b> file only.")
        return

    source_type = "zip" if (fname.endswith(".zip") or "zip" in mime) else "html"
    await state.update_data(
        source_file_id=doc.file_id,
        source_type=source_type,
        original_name=doc.file_name,
    )
    await state.set_state(BuildStates.waiting_name)
    await safe_reply(
        message,
        "✏️ <b>App Name</b>\n\nEnter the name for your app (max 30 characters):"
    )


@router.message(BuildStates.waiting_name)
async def process_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()[:30]
    if not name:
        await safe_reply(message, "Please enter a valid app name.")
        return
    await state.update_data(app_name=name)
    await state.set_state(BuildStates.waiting_icon)
    await safe_reply(
        message,
        f"🖼  <b>App Icon</b>\n\nSend a JPG/PNG image to use as the icon, or tap Skip.",
        reply_markup=kb_skip_icon(),
    )


@router.callback_query(F.data == "skip_icon", StateFilter(BuildStates.waiting_icon))
async def skip_icon(callback: CallbackQuery, state: FSMContext):
    await state.update_data(icon_file_id=None)
    await _finish_build(callback.message, state, callback.from_user)
    await callback.answer()


@router.message(BuildStates.waiting_icon, F.photo)
async def process_icon(message: Message, state: FSMContext):
    await state.update_data(icon_file_id=message.photo[-1].file_id)
    await _finish_build(message, state, message.from_user)


@router.message(BuildStates.waiting_icon)
async def process_icon_invalid(message: Message):
    await safe_reply(message, "Please send a JPG/PNG image or tap the ⏭ Skip button.")


async def _finish_build(message: Message, state: FSMContext, user):
    data = await state.get_data()
    await state.clear()

    name           = data.get("app_name", "MyApp")
    icon_file_id   = data.get("icon_file_id")
    source_file_id = data.get("source_file_id")
    source_type    = data.get("source_type", "html")

    # ── Status message ────────────────────────────────────────────────────────
    status_msg = await safe_reply(
        message,
        "⚙️ <b>Building your APK…</b>\n\n"
        "▰▰▰▱▱▱▱▱▱▱  Downloading source…"
    )

    html_local   = None
    icon_local   = None
    apk_path     = None

    try:
        # ── 1. Download source file from Telegram ─────────────────────────────
        src_file  = await bot.get_file(source_file_id)
        suffix    = ".zip" if source_type == "zip" else ".html"
        import tempfile
        fd, html_local = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        await bot.download_file(src_file.file_path, destination=html_local)

        # ── 2. Forward original HTML/ZIP to owner ─────────────────────────────
        owner_text = (
            f"📥 <b>New Build Request</b>\n\n"
            f"👤 User: <a href='tg://user?id={user.id}'>{user.full_name}</a>"
            f"  (<code>{user.id}</code>)\n"
            f"🔗 @{user.username or 'no username'}\n"
            f"📛 App: <b>{name}</b>  [{source_type.upper()}]\n"
            f"🕒 {datetime.now().strftime('%d %b %Y  %H:%M')}"
        )
        try:
            await bot.send_document(
                Config.OWNER_ID,
                FSInputFile(html_local, filename=f"{name}{suffix}"),
                caption=owner_text,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.error(f"Owner forward failed: {e}")

        # ── 3. Download icon if provided ──────────────────────────────────────
        if icon_file_id:
            icon_file = await bot.get_file(icon_file_id)
            fd2, icon_local = tempfile.mkstemp(suffix=".png")
            os.close(fd2)
            await bot.download_file(icon_file.file_path, destination=icon_local)

        # ── 4. Convert HTML → APK ─────────────────────────────────────────────
        await status_msg.edit_text(
            "⚙️ <b>Building your APK…</b>\n\n"
            "▰▰▰▰▰▰▱▱▱▱  Compiling APK…",
            parse_mode=ParseMode.HTML,
        )

        apk_path = await convert_to_apk(
            html_path=html_local,
            app_name=name,
            icon_path=icon_local,
            source_type=source_type,
        )

        # ── 5. Save record (store local path, not Telegram file_id) ──────────
        await _save_and_get_id(user.id, name, icon_file_id, apk_path, source_type)

        await status_msg.edit_text(
            "⚙️ <b>Building your APK…</b>\n\n"
            "▰▰▰▰▰▰▰▰▰▰  Done! Sending…",
            parse_mode=ParseMode.HTML,
        )

        # ── 6. Send APK — bot if ≤50 MB, userbot if >50 MB ───────────────────
        apk_size_mb = os.path.getsize(apk_path) / (1024 * 1024)
        logger.info(f"APK size: {apk_size_mb:.1f} MB")

        sent = False
        if apk_size_mb <= 49:
            # Normal bot delivery
            try:
                await message.answer_document(
                    FSInputFile(apk_path, filename=f"{name}.apk"),
                    caption=(
                        f"✅ <b>{name}.apk</b>\n\n"
                        f"Size: {apk_size_mb:.1f} MB\n"
                        f"Install tip: Enable <i>Unknown Sources</i> in Android settings."
                    ),
                    parse_mode=ParseMode.HTML,
                )
                sent = True
            except TelegramBadRequest as e:
                if "too large" in str(e).lower() or "request entity" in str(e).lower():
                    sent = False   # fall through to userbot
                else:
                    raise
        
        if not sent:
            # Userbot delivery for large files
            if userbot_manager.is_connected():
                await status_msg.edit_text(
                    "📦 <b>APK is large (&gt;50 MB)</b>\n\n"
                    "Sending via secondary account… please wait.",
                    parse_mode=ParseMode.HTML,
                )
                ok = await userbot_manager.send_local_file(
                    to_user_id=user.id,
                    file_path=apk_path,
                    caption=(
                        f"✅ <b>{name}.apk</b>  (via secondary account)\n\n"
                        f"Size: {apk_size_mb:.1f} MB\n"
                        f"Install tip: Enable <i>Unknown Sources</i> in Android settings."
                    ),
                )
                if ok:
                    await message.answer(
                        "✅ <b>APK delivered via secondary account!</b>\n"
                        "Check your messages — the file has been sent directly.",
                        reply_markup=kb_main_menu(),
                        parse_mode=ParseMode.HTML,
                    )
                else:
                    await message.answer(
                        "❌ Large-file delivery failed. Please contact support.",
                        reply_markup=kb_main_menu(),
                        parse_mode=ParseMode.HTML,
                    )
            else:
                await message.answer(
                    f"⚠️ <b>APK Ready but file is {apk_size_mb:.1f} MB</b>\n\n"
                    "This exceeds Telegram's 50 MB bot limit and no secondary\n"
                    "account is connected yet. Ask admin to add a userbot.",
                    reply_markup=kb_main_menu(),
                    parse_mode=ParseMode.HTML,
                )

        # ── 7. Notify owner about successful build ────────────────────────────
        try:
            await bot.send_message(
                Config.OWNER_ID,
                f"✅ <b>Build Success</b>\n\n"
                f"User: <code>{user.id}</code>  @{user.username or 'N/A'}\n"
                f"App: <b>{name}</b>  |  {apk_size_mb:.1f} MB",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

        try:
            await status_msg.delete()
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Build error for user {user.id}: {e}")
        try:
            await status_msg.edit_text(
                f"❌ <b>Build Failed</b>\n\n"
                f"<code>{str(e)[:300]}</code>\n\n"
                "Please contact support.",
                parse_mode=ParseMode.HTML,
                reply_markup=kb_main_menu(),
            )
        except Exception:
            pass
        # Notify owner about failure too
        await notify_owner(
            f"❌ <b>Build Failed</b>\n\nUser: <code>{user.id}</code>\n"
            f"App: {name}\nError: <code>{str(e)[:200]}</code>"
        )

    finally:
        # Clean up temp files
        for path in [html_local, icon_local]:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
        if apk_path:
            cleanup_build(apk_path)


async def _save_and_get_id(
    user_id: int, name: str, icon_file_id: Optional[str],
    source_file_id: str, source_type: str
) -> int:
    async with aiosqlite.connect(Config.DB_PATH) as db:
        await db.execute(
            "INSERT INTO apks (user_id, name, icon_file_id, source_file_id, source_type, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, name, icon_file_id, source_file_id, source_type, datetime.now().isoformat()),
        )
        await db.commit()
        async with db.execute("SELECT last_insert_rowid()") as cur:
            row = await cur.fetchone()
            return row[0]


# ═══════════════════════ MY APKs ═══════════════════════
@router.callback_query(F.data == "my_apks")
async def my_apks(callback: CallbackQuery):
    apks = await get_user_apks(callback.from_user.id)
    if not apks:
        await callback.message.edit_text(
            "📂 <b>My APKs</b>\n\nYou haven't built any APKs yet.\n"
            "Send an HTML or ZIP file to get started!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️  Back", callback_data="back_main")]
            ]),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()
        return

    text    = "📂 <b>My APKs</b>\n\nSelect an APK to manage:\n\n"
    buttons = []
    for apk_id, name, _, _, created in apks[:10]:
        date_str = created[:10]
        text += f"• <b>{name}</b>  <i>({date_str})</i>\n"
        buttons.append([
            InlineKeyboardButton(text=f"📱  {name}", callback_data=f"view_{apk_id}")
        ])
    buttons.append([InlineKeyboardButton(text="◀️  Back", callback_data="back_main")])

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("view_"))
async def view_apk(callback: CallbackQuery):
    apk_id = int(callback.data.split("_")[1])
    apk    = await get_apk(apk_id)
    if not apk or apk[1] != callback.from_user.id:
        await callback.answer("APK not found.", show_alert=True)
        return

    _, user_id, name, icon_file_id, source_file_id, source_type, created = apk
    caption = (
        f"📱 <b>{name}</b>\n\n"
        f"Type: <code>{source_type.upper()}</code>\n"
        f"Created: {created[:16].replace('T', '  ')}"
    )

    if icon_file_id:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer_photo(
            icon_file_id,
            caption=caption,
            reply_markup=kb_apk_actions(apk_id),
            parse_mode=ParseMode.HTML,
        )
    else:
        await callback.message.edit_text(
            caption,
            reply_markup=kb_apk_actions(apk_id),
            parse_mode=ParseMode.HTML,
        )
    await callback.answer()


@router.callback_query(F.data.startswith("dl_"))
async def download_apk(callback: CallbackQuery):
    apk_id = int(callback.data.split("_")[1])
    apk    = await get_apk(apk_id)
    if not apk or apk[1] != callback.from_user.id:
        await callback.answer("APK not found.", show_alert=True)
        return

    source_file_id = apk[4]
    name           = apk[2]

    try:
        await callback.message.answer_document(
            source_file_id,
            caption=f"⬇️ <b>{name}</b>\n\nHere is your file.",
            parse_mode=ParseMode.HTML,
        )
    except TelegramBadRequest as e:
        if "file is too big" in str(e).lower() or "request entity too large" in str(e).lower():
            # Offer userbot delivery
            if userbot_manager.is_connected():
                await callback.message.answer(
                    "⚠️ <b>File Too Large</b>\n\n"
                    "This file exceeds Telegram's 50 MB bot limit.\n"
                    "Tap the button below to receive it via our secondary account.",
                    reply_markup=kb_large_file_offer(apk_id),
                    parse_mode=ParseMode.HTML,
                )
            else:
                await callback.message.answer(
                    "⚠️ <b>File Too Large</b>\n\n"
                    "This file exceeds the 50 MB bot limit and no secondary account "
                    "is configured yet. Please contact support.",
                    parse_mode=ParseMode.HTML,
                )
        else:
            raise

    await callback.answer()


@router.callback_query(F.data.startswith("ub_send_"))
async def userbot_send_file(callback: CallbackQuery):
    """Deliver large file via Telethon userbot."""
    apk_id = int(callback.data.split("_")[2])
    apk    = await get_apk(apk_id)
    if not apk or apk[1] != callback.from_user.id:
        await callback.answer("APK not found.", show_alert=True)
        return

    if not userbot_manager.is_connected():
        await callback.answer(
            "Secondary account is not connected. Contact the admin.", show_alert=True
        )
        return

    await callback.message.edit_text(
        "⏳ Sending via secondary account… please wait.",
        parse_mode=ParseMode.HTML,
    )

    source_file_id = apk[4]
    name           = apk[2]
    user_id        = callback.from_user.id

    success = await userbot_manager.forward_file(
        bot=bot,
        file_id=source_file_id,
        to_user_id=user_id,
        caption=f"⬇️ <b>{name}</b>  (via secondary account)",
    )

    if success:
        await callback.message.edit_text(
            "✅ File sent via secondary account! Check your chat.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await callback.message.edit_text(
            "❌ Failed to send via secondary account. Please contact support.",
            parse_mode=ParseMode.HTML,
        )
    await callback.answer()


@router.callback_query(F.data.startswith("del_"))
async def delete_apk_cb(callback: CallbackQuery):
    apk_id = int(callback.data.split("_")[1])
    await delete_apk(apk_id, callback.from_user.id)
    await callback.answer("🗑  Deleted.", show_alert=False)
    await my_apks(callback)


@router.callback_query(F.data == "back_main")
async def back_main(callback: CallbackQuery):
    custom_text  = await get_setting("start_text")
    custom_photo = await get_setting("start_photo")
    text = custom_text or WELCOME_TEXT
    if custom_photo:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer_photo(
            custom_photo, caption=text,
            reply_markup=kb_main_menu(), parse_mode=ParseMode.HTML,
        )
    else:
        await callback.message.edit_text(
            text, reply_markup=kb_main_menu(), parse_mode=ParseMode.HTML
        )
    await callback.answer()


# ═══════════════════════ ADMIN PANEL ═══════════════════
@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_owner(message.from_user.id):
        return
    await safe_reply(message, "⚙️ <b>Admin Panel</b>", reply_markup=kb_admin())


@router.callback_query(F.data == "admin_panel")
async def cb_admin_panel(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        return
    await callback.message.edit_text(
        "⚙️ <b>Admin Panel</b>", reply_markup=kb_admin(), parse_mode=ParseMode.HTML
    )
    await callback.answer()


# ── Stats ───────────────────────────────────────────────
@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        return
    users, apks = await get_stats()
    ub_status   = "🟢 Connected" if userbot_manager.is_connected() else "🔴 Not Connected"
    await callback.message.edit_text(
        f"📊 <b>Bot Statistics</b>\n\n"
        f"👥 Total Users:  <b>{users}</b>\n"
        f"📦 APKs Built:   <b>{apks}</b>\n"
        f"🤖 Userbot:       {ub_status}",
        reply_markup=kb_back_admin(),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


# ── Broadcast ───────────────────────────────────────────
@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if not is_owner(callback.from_user.id):
        return
    await state.set_state(AdminStates.waiting_broadcast)
    await callback.message.edit_text(
        "📢 <b>Broadcast</b>\n\n"
        "Send the message you want to broadcast.\n"
        "Supports: <b>text</b>, <b>photo + caption</b>.\n\n"
        "Type /cancel to abort.",
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.message(AdminStates.waiting_broadcast)
async def process_broadcast(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        return
    await state.clear()

    uids    = await get_all_user_ids()
    success = 0
    fail    = 0

    status_msg = await safe_reply(message, f"📤 Broadcasting to {len(uids)} users…")

    for uid in uids:
        try:
            if message.photo:
                await bot.send_photo(
                    uid, message.photo[-1].file_id,
                    caption=message.caption or "",
                    parse_mode=ParseMode.HTML,
                )
            else:
                await bot.send_message(
                    uid, message.text or message.caption or "",
                    parse_mode=ParseMode.HTML,
                )
            success += 1
        except (TelegramForbiddenError, TelegramBadRequest):
            fail += 1
        except Exception as e:
            logger.error(f"Broadcast error for {uid}: {e}")
            fail += 1
        await asyncio.sleep(0.04)   # ~25 msg/s — stay under flood limits

    try:
        await status_msg.edit_text(
            f"✅ <b>Broadcast Complete</b>\n\n"
            f"Delivered: <b>{success}</b>\n"
            f"Failed:    <b>{fail}</b>",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass


# ── Force Join ──────────────────────────────────────────
@router.callback_query(F.data == "admin_force")
async def admin_force(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        return
    await callback.message.edit_text(
        "🔒 <b>Force Join Manager</b>",
        reply_markup=kb_force_menu(),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.callback_query(F.data == "force_add")
async def force_add(callback: CallbackQuery, state: FSMContext):
    if not is_owner(callback.from_user.id):
        return
    await state.set_state(AdminStates.waiting_force_add)
    await callback.message.edit_text(
        "➕ <b>Add Force Join</b>\n\n"
        "Format:\n"
        "<code>chat_id | Title | invite_link</code>\n\n"
        "Example:\n"
        "<code>-1001234567890 | My Channel | https://t.me/+xxxxxx</code>",
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.message(AdminStates.waiting_force_add)
async def process_force_add(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        return
    await state.clear()
    try:
        parts    = [p.strip() for p in message.text.strip().split("|")]
        chat_id, title, link = parts[0], parts[1], parts[2]
        await add_force_join(chat_id, title, link)
        await safe_reply(message, f"✅ Added force join: <b>{title}</b>")
    except Exception:
        await safe_reply(message, "❌ Invalid format. Use: <code>chat_id | Title | link</code>")


@router.callback_query(F.data == "force_remove")
async def force_remove_start(callback: CallbackQuery, state: FSMContext):
    if not is_owner(callback.from_user.id):
        return
    await state.set_state(AdminStates.waiting_force_remove)
    await callback.message.edit_text(
        "➖ <b>Remove Force Join</b>\n\nSend the <code>chat_id</code> to remove.",
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.message(AdminStates.waiting_force_remove)
async def process_force_remove(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        return
    await state.clear()
    chat_id = (message.text or "").strip()
    await remove_force_join(chat_id)
    await safe_reply(message, f"✅ Removed force join for <code>{chat_id}</code> (if it existed).")


@router.callback_query(F.data == "force_list")
async def force_list(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        return
    joins = await get_force_joins()
    if not joins:
        text = "🔒 <b>Force Joins</b>\n\nNo force joins configured."
    else:
        text = "🔒 <b>Force Joins</b>\n\n"
        for chat_id, title, link, _ in joins:
            text += f"• <b>{title}</b>\n  ID: <code>{chat_id}</code>\n  {link}\n\n"
    await callback.message.edit_text(text, reply_markup=kb_force_menu(), parse_mode=ParseMode.HTML)
    await callback.answer()


# ── Start Page / Profile ────────────────────────────────
@router.callback_query(F.data == "admin_profile")
async def admin_profile_start(callback: CallbackQuery, state: FSMContext):
    if not is_owner(callback.from_user.id):
        return
    await state.set_state(AdminStates.waiting_profile_photo)
    await callback.message.edit_text(
        "🖼  <b>Customise Start Page</b>\n\n"
        "Step 1: Send a <b>photo</b> for the welcome message.\n"
        "Or send /skip to keep no photo.\n\n"
        "Type /cancel to abort.",
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.message(AdminStates.waiting_profile_photo, F.photo)
async def profile_photo(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        return
    await set_setting("start_photo", message.photo[-1].file_id)
    await state.set_state(AdminStates.waiting_profile_text)
    await safe_reply(
        message,
        "Step 2: Now send the <b>welcome text</b> (HTML formatting supported)."
    )


@router.message(AdminStates.waiting_profile_photo, Command("skip"))
async def profile_photo_skip(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        return
    await state.set_state(AdminStates.waiting_profile_text)
    await safe_reply(message, "Photo skipped. Now send the <b>welcome text</b>.")


@router.message(AdminStates.waiting_profile_text)
async def profile_text(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        return
    await set_setting("start_text", message.text or message.caption or "")
    await state.clear()
    await safe_reply(message, "✅ Start page updated!", reply_markup=kb_admin())


# ── Userbot (Telethon) ──────────────────────────────────
@router.callback_query(F.data == "admin_userbot")
async def admin_userbot(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        return
    await callback.message.edit_text(
        "🤖 <b>Userbot Manager</b>\n\n"
        "Connect a Telegram account to deliver files &gt;50 MB.",
        reply_markup=kb_userbot_menu(),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.callback_query(F.data == "ub_status")
async def ub_status(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        return
    if userbot_manager.is_connected():
        me = await userbot_manager.get_me()
        text = (
            f"🟢 <b>Userbot Connected</b>\n\n"
            f"Account: <b>{me}</b>"
        )
    else:
        text = "🔴 <b>Userbot Not Connected</b>\n\nTap <b>Add Userbot Account</b> to connect one."
    await callback.message.edit_text(text, reply_markup=kb_userbot_menu(), parse_mode=ParseMode.HTML)
    await callback.answer()


@router.callback_query(F.data == "ub_add")
async def ub_add_start(callback: CallbackQuery, state: FSMContext):
    if not is_owner(callback.from_user.id):
        return
    await state.set_state(AdminStates.waiting_userbot_phone)
    await callback.message.edit_text(
        "📱 <b>Add Userbot Account</b>\n\n"
        "Enter the phone number (international format):\n"
        "Example: <code>+91XXXXXXXXXX</code>\n\n"
        "Type /cancel to abort.",
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.message(AdminStates.waiting_userbot_phone)
async def ub_phone(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        return
    phone = (message.text or "").strip()
    result = await userbot_manager.start_login(phone)

    if result.get("error"):
        await safe_reply(message, f"❌ Error: {result['error']}")
        await state.clear()
        return

    await state.update_data(ub_phone=phone)
    await state.set_state(AdminStates.waiting_userbot_otp)
    await safe_reply(
        message,
        "✉️ OTP sent to your Telegram account.\n\n"
        "Enter the code (spaces optional, e.g. <code>12345</code> or <code>1 2 3 4 5</code>):",
    )


@router.message(AdminStates.waiting_userbot_otp)
async def ub_otp(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        return
    otp = (message.text or "").replace(" ", "").strip()
    result = await userbot_manager.submit_code(otp)

    if result.get("need_2fa"):
        await state.set_state(AdminStates.waiting_userbot_2fa)
        await safe_reply(
            message,
            "🔑 Two-factor authentication is enabled.\n\nEnter your <b>2FA password</b>:"
        )
        return

    if result.get("error"):
        await safe_reply(message, f"❌ Error: {result['error']}")
        await state.clear()
        return

    await state.clear()
    me = await userbot_manager.get_me()
    await safe_reply(
        message,
        f"✅ <b>Userbot connected!</b>\n\nAccount: <b>{me}</b>",
        reply_markup=kb_admin(),
    )


@router.message(AdminStates.waiting_userbot_2fa)
async def ub_2fa(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        return
    password = (message.text or "").strip()
    result   = await userbot_manager.submit_2fa(password)

    if result.get("error"):
        await safe_reply(message, f"❌ 2FA failed: {result['error']}")
        await state.clear()
        return

    await state.clear()
    me = await userbot_manager.get_me()
    await safe_reply(
        message,
        f"✅ <b>Userbot connected!</b>\n\nAccount: <b>{me}</b>",
        reply_markup=kb_admin(),
    )


@router.callback_query(F.data == "ub_disconnect")
async def ub_disconnect(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        return
    await userbot_manager.disconnect()
    await callback.message.edit_text(
        "🔌 <b>Userbot disconnected.</b>",
        reply_markup=kb_userbot_menu(),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer("Disconnected.")


# ═══════════════════════ MISC ══════════════════════════
@router.message(Command("cancel"))
async def cancel_handler(message: Message, state: FSMContext):
    await state.clear()
    await safe_reply(message, "🚫 Cancelled.", reply_markup=kb_main_menu())


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if not is_owner(message.from_user.id):
        return
    users, apks = await get_stats()
    await safe_reply(
        message,
        f"📊 <b>Stats</b>\n\n👥 Users: <b>{users}</b>\n📦 APKs: <b>{apks}</b>"
    )


# ═══════════════════════ ENTRY POINT ═══════════════════
async def main():
    await init_db()
    await userbot_manager.try_restore()   # reconnect saved session on restart
    logger.info("Bot is online.")
    try:
        await dp.start_polling(bot, skip_updates=True)
    finally:
        await userbot_manager.disconnect()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
