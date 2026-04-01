"""
=============================================================
  EV Lab — Bambu Lab Printer Management Bot
  Features:
    • Student ID login / registration
    • Admin panel (register, delete, transfer accounts)
    • Printer selection (H2D, A1, P2S)
    • Filament & colour selection
    • Hours + minutes duration input
    • Repeating alarm every 30 s until bed is cleared
    • Queue system (locks printer while in use)
    • "Bed cleared" confirmation to auto-dequeue
    • /summary — live snapshot of all printers & queues
=============================================================
"""

import sqlite3
import asyncio
import logging
from datetime import datetime, timedelta

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ─── Configuration ────────────────────────────────────────────────────────────

BOT_TOKEN   = "8436149771:AAEPl9DoNCV43zKX7w2sUCg9xBmfEanEQKA"          # ← Replace with your token
ADMIN_IDS   = [697702193]                    # ← Replace with your Telegram ID(s)

PRINTERS    = ["H2D", "A1", "P2S"]          # Adjust if you add a 4th printer

FILAMENTS   = ["PLA", "PETG", "ABS", "TPU", "ASA", "PA", "PC"]
COLOURS     = ["White", "Black", "Red", "Blue", "Green", "Yellow", "Orange",
               "Grey", "Transparent", "Custom"]

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Database ─────────────────────────────────────────────────────────────────

conn   = sqlite3.connect("ev_printers.db", check_same_thread=False)
cursor = conn.cursor()

cursor.executescript("""
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE,
    full_name   TEXT,
    student_id  TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS print_jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    printer       TEXT    NOT NULL,
    telegram_id   INTEGER NOT NULL,
    student_id    TEXT    NOT NULL,
    full_name     TEXT    NOT NULL,
    filament      TEXT    NOT NULL,
    colour        TEXT    NOT NULL,
    duration_min  INTEGER NOT NULL,
    status        TEXT    DEFAULT 'queued',
    started_at    TEXT,
    created_at    TEXT    DEFAULT (datetime('now'))
);
""")
conn.commit()
logger.info("Database ready.")

# ─── Helpers ──────────────────────────────────────────────────────────────────

def db_get_user(telegram_id: int):
    cursor.execute("SELECT full_name, student_id FROM users WHERE telegram_id=?", (telegram_id,))
    return cursor.fetchone()

def db_get_user_by_sid(student_id: str):
    cursor.execute("SELECT telegram_id, full_name FROM users WHERE student_id=?", (student_id,))
    return cursor.fetchone()

def db_active_job(printer: str):
    """Return the active (printing) job for a printer, or None."""
    cursor.execute(
        "SELECT id, telegram_id, full_name, duration_min, started_at FROM print_jobs "
        "WHERE printer=? AND status='printing' ORDER BY id LIMIT 1",
        (printer,)
    )
    return cursor.fetchone()

def db_next_queued(printer: str):
    """Return the next queued job for a printer."""
    cursor.execute(
        "SELECT id, telegram_id, full_name, filament, colour, duration_min "
        "FROM print_jobs WHERE printer=? AND status='queued' ORDER BY id LIMIT 1",
        (printer,)
    )
    return cursor.fetchone()

def db_queue_count(printer: str):
    cursor.execute(
        "SELECT COUNT(*) FROM print_jobs WHERE printer=? AND status='queued'",
        (printer,)
    )
    return cursor.fetchone()[0]

def printer_keyboard():
    buttons = [[InlineKeyboardButton(p, callback_data=f"printer:{p}")] for p in PRINTERS]
    return InlineKeyboardMarkup(buttons)

def filament_keyboard():
    rows = []
    row  = []
    for i, f in enumerate(FILAMENTS):
        row.append(InlineKeyboardButton(f, callback_data=f"filament:{f}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)

def colour_keyboard():
    rows = []
    row  = []
    for i, c in enumerate(COLOURS):
        row.append(InlineKeyboardButton(c, callback_data=f"colour:{c}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)

def is_admin(telegram_id: int) -> bool:
    return telegram_id in ADMIN_IDS

# ─── Conversation states ───────────────────────────────────────────────────────

# Registration
REG_NAME, REG_SID = range(2)

# Print job wizard
JOB_PRINTER, JOB_FILAMENT, JOB_COLOUR, JOB_HOURS, JOB_MINUTES = range(4, 9)

# Admin actions
ADM_ACTION, ADM_SID_DELETE, ADM_SID_TRANSFER, ADM_NEW_SID = range(9, 13)

# ─── /start ───────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = db_get_user(update.effective_user.id)
    if user:
        name, sid = user
        await update.message.reply_text(
            f"👋 Welcome back, *{name}* (ID: `{sid}`)!\n\n"
            "Commands:\n"
            "  /print — Start or queue a print job\n"
            "  /status — Check all printer statuses\n"
            "  /summary — Full snapshot of who's printing what\n"
            "  /myjobs — View your active / queued jobs\n"
            "  /admin — Admin panel (authorised users only)",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "👋 Welcome to the *EV Lab Printer Bot*!\n\n"
            "You need to register before printing.\n"
            "Use /register to create your account.",
            parse_mode="Markdown",
        )

# ─── Registration ─────────────────────────────────────────────────────────────

async def cmd_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if db_get_user(update.effective_user.id):
        await update.message.reply_text("✅ You're already registered! Use /print to start.")
        return ConversationHandler.END
    await update.message.reply_text("📝 Let's get you registered.\n\nPlease enter your *full name*:", parse_mode="Markdown")
    return REG_NAME

async def reg_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["reg_name"] = update.message.text.strip()
    await update.message.reply_text("Now enter your *Student ID*:", parse_mode="Markdown")
    return REG_SID

async def reg_sid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sid  = update.message.text.strip()
    name = context.user_data["reg_name"]
    # Check if student ID already used
    if db_get_user_by_sid(sid):
        await update.message.reply_text("❌ That Student ID is already registered. Contact an admin.")
        return ConversationHandler.END
    try:
        cursor.execute(
            "INSERT INTO users (telegram_id, full_name, student_id) VALUES (?,?,?)",
            (update.effective_user.id, name, sid),
        )
        conn.commit()
        await update.message.reply_text(
            f"✅ Registered!\n👤 Name: *{name}*\n🎓 Student ID: `{sid}`\n\n"
            "Use /print to queue a print job.",
            parse_mode="Markdown",
        )
    except sqlite3.IntegrityError:
        await update.message.reply_text("❌ Registration failed (duplicate). Contact an admin.")
    return ConversationHandler.END

# ─── /status ──────────────────────────────────────────────────────────────────

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["🖨️ *Printer Status*\n"]
    for p in PRINTERS:
        active = db_active_job(p)
        queued = db_queue_count(p)
        if active:
            job_id, tid, name, dur, started = active
            if started:
                start_dt  = datetime.fromisoformat(started)
                end_dt    = start_dt + timedelta(minutes=dur)
                rem_secs  = max(0, int((end_dt - datetime.now()).total_seconds()))
                rh, rm    = divmod(rem_secs // 60, 60)
                rem_str   = f"{rh}h {rm}m" if rh else f"{rm}m"
                lines.append(
                    f"*{p}* 🔴 Printing\n"
                    f"  👤 {name}\n"
                    f"  ⏱ ~{rem_str} remaining\n"
                    f"  📋 Queue: {queued} waiting"
                )
            else:
                lines.append(f"*{p}* 🔴 Printing (no timer)\n  📋 Queue: {queued} waiting")
        else:
            lines.append(f"*{p}* 🟢 Available\n  📋 Queue: {queued} waiting")
    await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")

# ─── Print Job Wizard ─────────────────────────────────────────────────────────

async def cmd_print(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = db_get_user(update.effective_user.id)
    if not user:
        await update.message.reply_text("❌ You're not registered yet. Use /register first.")
        return ConversationHandler.END
    context.user_data["job"] = {"name": user[0], "sid": user[1]}
    await update.message.reply_text("🖨️ *Step 1/4* — Select a printer:", parse_mode="Markdown", reply_markup=printer_keyboard())
    return JOB_PRINTER

async def job_printer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    printer = query.data.split(":")[1]
    context.user_data["job"]["printer"] = printer
    active = db_active_job(printer)
    queued = db_queue_count(printer)
    status_line = (
        f"🔴 *{printer}* is currently busy. You will be queued (position {queued + 1})."
        if active else
        f"🟢 *{printer}* is available!"
    )
    await query.edit_message_text(
        f"{status_line}\n\n🧵 *Step 2/4* — Select filament type:",
        parse_mode="Markdown",
        reply_markup=filament_keyboard(),
    )
    return JOB_FILAMENT

async def job_filament(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["job"]["filament"] = query.data.split(":")[1]
    await query.edit_message_text(
        "🎨 *Step 3/4* — Select filament colour:",
        parse_mode="Markdown",
        reply_markup=colour_keyboard(),
    )
    return JOB_COLOUR

async def job_colour(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["job"]["colour"] = query.data.split(":")[1]
    await query.edit_message_text(
        "⏱ *Step 4a/4* — How many *hours* will the print take?\n\nReply with a number (e.g. `2`). Enter `0` if under an hour.",
        parse_mode="Markdown",
    )
    return JOB_HOURS

async def job_hours(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ Please enter a whole number of hours (e.g. `2` or `0`).", parse_mode="Markdown")
        return JOB_HOURS
    context.user_data["job"]["hours"] = int(text)
    await update.message.reply_text(
        "⏱ *Step 4b/4* — How many additional *minutes*?\n\nReply with a number between `0` and `59`.",
        parse_mode="Markdown",
    )
    return JOB_MINUTES

async def job_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) > 59:
        await update.message.reply_text("❌ Please enter minutes between `0` and `59`.", parse_mode="Markdown")
        return JOB_MINUTES

    job              = context.user_data["job"]
    total_minutes    = job["hours"] * 60 + int(text)

    if total_minutes < 1:
        await update.message.reply_text("❌ Total duration must be at least 1 minute. Let's try again — how many hours?", parse_mode="Markdown")
        context.user_data["job"].pop("hours", None)
        return JOB_HOURS

    job["duration"]  = total_minutes
    printer          = job["printer"]
    active           = db_active_job(printer)

    if active:
        status     = "queued"
        started_at = None
    else:
        status     = "printing"
        started_at = datetime.now().isoformat()

    cursor.execute(
        "INSERT INTO print_jobs (printer, telegram_id, student_id, full_name, filament, colour, duration_min, status, started_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (printer, update.effective_user.id, job["sid"], job["name"],
         job["filament"], job["colour"], job["duration"], status, started_at),
    )
    conn.commit()
    job_id = cursor.lastrowid

    h, m      = divmod(job["duration"], 60)
    dur_str   = f"{h}h {m}m" if h else f"{m}m"

    if status == "printing":
        end_time = (datetime.now() + timedelta(minutes=job["duration"])).strftime("%H:%M")
        await update.message.reply_text(
            f"✅ *Print started!*\n\n"
            f"🖨️ Printer: *{printer}*\n"
            f"🧵 Filament: {job['filament']} — {job['colour']}\n"
            f"⏱ Duration: {dur_str} (done ~{end_time})\n\n"
            "I'll alarm you repeatedly when time is up until you clear the bed 🔔",
            parse_mode="Markdown",
        )
        asyncio.create_task(
            print_alarm(context, update.effective_user.id, printer, job["duration"], job_id)
        )
    else:
        queue_pos = db_queue_count(printer)
        await update.message.reply_text(
            f"📋 *Queued!*\n\n"
            f"🖨️ Printer: *{printer}*\n"
            f"🧵 Filament: {job['filament']} — {job['colour']}\n"
            f"⏱ Duration: {dur_str}\n"
            f"📍 Queue position: *{queue_pos}*\n\n"
            "You'll be notified when it's your turn.",
            parse_mode="Markdown",
        )
    return ConversationHandler.END

# ─── Repeating alarm task ─────────────────────────────────────────────────────

ALARM_MESSAGES = [
    "⏰ *PRINT DONE — {printer}!*\n\nYour print is finished! Please collect it and clear the bed 🎉",
    "🔔 *Reminder — {printer} needs clearing!*\n\nYour print is still waiting. Please clear the bed so the next person can print.",
    "‼️ *URGENT — {printer} bed not cleared!*\n\nPlease clear the print bed ASAP — someone may be waiting in the queue!",
    "🚨 *{printer} — BED STILL OCCUPIED!*\n\nThis is reminder #{count}. Please clear the bed immediately!",
]

async def print_alarm(
    context: ContextTypes.DEFAULT_TYPE,
    telegram_id: int,
    printer: str,
    duration_min: int,
    job_id: int,
):
    """Wait for the print to finish, then ping every 30 s until bed is cleared."""
    await asyncio.sleep(duration_min * 60)

    ring_count = 0
    while True:
        # Stop ringing if job was marked done (bed cleared) or cancelled
        cursor.execute("SELECT status FROM print_jobs WHERE id=?", (job_id,))
        row = cursor.fetchone()
        if not row or row[0] != "printing":
            break

        ring_count += 1
        idx = min(ring_count - 1, len(ALARM_MESSAGES) - 1)
        msg = ALARM_MESSAGES[idx].format(printer=printer, count=ring_count)

        bed_btn = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                f"✅ Bed cleared on {printer}",
                callback_data=f"bedcleared:{job_id}:{printer}",
            )
        ]])

        try:
            await context.bot.send_message(
                chat_id=telegram_id,
                text=msg,
                parse_mode="Markdown",
                reply_markup=bed_btn,
            )
        except Exception as e:
            logger.warning(f"Alarm send failed for {telegram_id}: {e}")
            break

        # Wait 30 seconds before ringing again
        await asyncio.sleep(30)

# ─── Bed cleared callback ─────────────────────────────────────────────────────

async def cb_bed_cleared(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Thanks! Checking queue…")
    _, job_id, printer = query.data.split(":")
    job_id = int(job_id)

    # Mark current job as done
    cursor.execute("UPDATE print_jobs SET status='done' WHERE id=?", (job_id,))
    conn.commit()

    await query.edit_message_text(
        f"✅ *{printer}* bed cleared. Thank you!\nChecking queue…",
        parse_mode="Markdown",
    )

    # Promote next queued job
    next_job = db_next_queued(printer)
    if next_job:
        nid, ntid, nname, nfil, ncol, ndur = next_job
        started_at = datetime.now().isoformat()
        cursor.execute(
            "UPDATE print_jobs SET status='printing', started_at=? WHERE id=?",
            (started_at, nid),
        )
        conn.commit()
        end_time = (datetime.now() + timedelta(minutes=ndur)).strftime("%H:%M")
        try:
            await context.bot.send_message(
                chat_id=ntid,
                text=(
                    f"🟢 *Your turn on {printer}!*\n\n"
                    f"🧵 {nfil} — {ncol}\n"
                    f"⏱ {ndur} min (done ~{end_time})\n\n"
                    "Your print has started! I'll notify you when done 🔔"
                ),
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning(f"Could not notify {ntid}: {e}")
        asyncio.create_task(
            print_alarm(context, ntid, printer, ndur, nid)
        )
        await context.bot.send_message(
            chat_id=update.effective_user.id,
            text=f"📢 Next job on *{printer}* has started for *{nname}*.",
            parse_mode="Markdown",
        )
    else:
        await context.bot.send_message(
            chat_id=update.effective_user.id,
            text=f"🟢 *{printer}* is now free — no one in queue.",
            parse_mode="Markdown",
        )

# ─── /summary ─────────────────────────────────────────────────────────────────

async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["📊 *Lab Printer Summary*\n", f"🕐 As of {datetime.now().strftime('%d %b %Y, %H:%M')}\n"]

    any_activity = False

    for p in PRINTERS:
        active = db_active_job(p)
        cursor.execute(
            "SELECT full_name, student_id, filament, colour, duration_min, started_at "
            "FROM print_jobs WHERE printer=? AND status='queued' ORDER BY id",
            (p,)
        )
        queued_jobs = cursor.fetchall()

        section = [f"━━━━━━━━━━━━━━━\n🖨️ *{p}*"]

        if active:
            any_activity = True
            job_id, tid, name, dur, started = active
            # Fetch filament/colour for active job
            cursor.execute(
                "SELECT filament, colour, student_id FROM print_jobs WHERE id=?", (job_id,)
            )
            detail = cursor.fetchone()
            fil, col, sid = detail if detail else ("?", "?", "?")
            h, m = divmod(dur, 60)
            dur_str = f"{h}h {m}m" if h else f"{m}m"

            if started:
                end_dt   = datetime.fromisoformat(started) + timedelta(minutes=dur)
                rem_secs = max(0, int((end_dt - datetime.now()).total_seconds()))
                rh, rm   = divmod(rem_secs // 60, 60)
                rem_str  = f"{rh}h {rm}m" if rh else f"{rm}m"
                end_fmt  = end_dt.strftime("%H:%M")
                section.append(
                    f"  🔴 *Printing*\n"
                    f"  👤 {name} (`{sid}`)\n"
                    f"  🧵 {fil} — {col}\n"
                    f"  ⏱ {dur_str} total | ~{rem_str} left | done ~{end_fmt}"
                )
            else:
                section.append(
                    f"  🔴 *Printing*\n"
                    f"  👤 {name} (`{sid}`)\n"
                    f"  🧵 {fil} — {col} | {dur_str}"
                )
        else:
            section.append("  🟢 *Available*")

        if queued_jobs:
            any_activity = True
            section.append(f"\n  📋 *Queue ({len(queued_jobs)} waiting):*")
            for pos, (qname, qsid, qfil, qcol, qdur, _) in enumerate(queued_jobs, 1):
                qh, qm   = divmod(qdur, 60)
                qdur_str = f"{qh}h {qm}m" if qh else f"{qm}m"
                section.append(f"  {pos}. {qname} (`{qsid}`) — {qfil}/{qcol} [{qdur_str}]")

        lines.append("\n".join(section))

    if not any_activity:
        lines.append("✨ All printers are free and queues are empty!")

    await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")

# ─── /myjobs ──────────────────────────────────────────────────────────────────

async def cmd_myjobs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tid = update.effective_user.id
    cursor.execute(
        "SELECT printer, filament, colour, duration_min, status, started_at "
        "FROM print_jobs WHERE telegram_id=? AND status IN ('printing','queued') ORDER BY id",
        (tid,),
    )
    jobs = cursor.fetchall()
    if not jobs:
        await update.message.reply_text("You have no active or queued jobs.")
        return
    lines = ["📋 *Your active jobs:*\n"]
    for p, f, c, dur, st, sa in jobs:
        h, m = divmod(dur, 60)
        dur_str = f"{h}h {m}m" if h else f"{m}m"
        if st == "printing" and sa:
            end_dt    = datetime.fromisoformat(sa) + timedelta(minutes=dur)
            rem_secs  = max(0, int((end_dt - datetime.now()).total_seconds()))
            rh, rm    = divmod(rem_secs // 60, 60)
            rem_str   = f"{rh}h {rm}m" if rh else f"{rm}m"
            lines.append(f"*{p}* 🔴 Printing\n  {f} / {c} | ~{rem_str} remaining")
        else:
            qpos = db_queue_count(p)
            lines.append(f"*{p}* 📋 Queued (pos ~{qpos})\n  {f} / {c} | {dur_str}")
    await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")

# ─── Admin panel ──────────────────────────────────────────────────────────────

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ You don't have admin access.")
        return ConversationHandler.END
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 List all users",    callback_data="adm:list")],
        [InlineKeyboardButton("🗑 Delete account",    callback_data="adm:delete")],
        [InlineKeyboardButton("🔁 Transfer ownership", callback_data="adm:transfer")],
        [InlineKeyboardButton("🖨 Clear printer queue", callback_data="adm:clearq")],
        [InlineKeyboardButton("❌ Cancel",             callback_data="adm:cancel")],
    ])
    await update.message.reply_text("🔑 *Admin Panel*", parse_mode="Markdown", reply_markup=keyboard)
    return ADM_ACTION

async def adm_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data.split(":")[1]

    if action == "cancel":
        await query.edit_message_text("Admin panel closed.")
        return ConversationHandler.END

    if action == "list":
        cursor.execute("SELECT full_name, student_id, telegram_id FROM users ORDER BY id")
        users = cursor.fetchall()
        if not users:
            await query.edit_message_text("No registered users.")
            return ConversationHandler.END
        lines = ["👥 *Registered Users:*\n"]
        for name, sid, tid in users:
            lines.append(f"• *{name}* | SID: `{sid}` | TID: `{tid}`")
        await query.edit_message_text("\n".join(lines), parse_mode="Markdown")
        return ConversationHandler.END

    if action == "delete":
        context.user_data["adm_action"] = "delete"
        await query.edit_message_text("Enter the *Student ID* to delete:", parse_mode="Markdown")
        return ADM_SID_DELETE

    if action == "transfer":
        context.user_data["adm_action"] = "transfer"
        await query.edit_message_text(
            "Enter the *Student ID* of the account whose ownership you want to transfer:",
            parse_mode="Markdown",
        )
        return ADM_SID_TRANSFER

    if action == "clearq":
        printer_btns = [[InlineKeyboardButton(p, callback_data=f"adm_clearq:{p}")] for p in PRINTERS]
        await query.edit_message_text(
            "Which printer queue to clear?",
            reply_markup=InlineKeyboardMarkup(printer_btns),
        )
        return ADM_ACTION

async def adm_clearq_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    printer = query.data.split(":")[1]
    cursor.execute(
        "UPDATE print_jobs SET status='cancelled' WHERE printer=? AND status='queued'",
        (printer,),
    )
    conn.commit()
    await query.edit_message_text(f"✅ Queue cleared for *{printer}*.", parse_mode="Markdown")
    return ConversationHandler.END

async def adm_sid_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sid = update.message.text.strip()
    row = db_get_user_by_sid(sid)
    if not row:
        await update.message.reply_text(f"❌ No user found with Student ID `{sid}`.", parse_mode="Markdown")
        return ConversationHandler.END
    tid, name = row
    cursor.execute("DELETE FROM users WHERE student_id=?", (sid,))
    conn.commit()
    await update.message.reply_text(
        f"✅ Account deleted: *{name}* (SID: `{sid}`).", parse_mode="Markdown"
    )
    return ConversationHandler.END

async def adm_sid_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sid = update.message.text.strip()
    row = db_get_user_by_sid(sid)
    if not row:
        await update.message.reply_text(f"❌ No user found with Student ID `{sid}`.", parse_mode="Markdown")
        return ConversationHandler.END
    context.user_data["transfer_sid"] = sid
    await update.message.reply_text(
        f"Found account. Now enter the *new Student ID* to assign this account to:",
        parse_mode="Markdown",
    )
    return ADM_NEW_SID

async def adm_new_sid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_sid = update.message.text.strip()
    old_sid = context.user_data["transfer_sid"]
    if db_get_user_by_sid(new_sid):
        await update.message.reply_text(f"❌ Student ID `{new_sid}` is already in use.", parse_mode="Markdown")
        return ConversationHandler.END
    cursor.execute("UPDATE users SET student_id=? WHERE student_id=?", (new_sid, old_sid))
    conn.commit()
    await update.message.reply_text(
        f"✅ Ownership transferred: `{old_sid}` → `{new_sid}`.", parse_mode="Markdown"
    )
    return ConversationHandler.END

# ─── Cancel helper ────────────────────────────────────────────────────────────

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Action cancelled.")
    return ConversationHandler.END

# ─── Fallback for unknown ─────────────────────────────────────────────────────

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Unknown command. Try /start for the menu."
    )

# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # ── Registration conversation
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("register", cmd_register)],
        states={
            REG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name)],
            REG_SID:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_sid)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    ))

    # ── Print job conversation
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("print", cmd_print)],
        states={
            JOB_PRINTER:  [CallbackQueryHandler(job_printer,  pattern=r"^printer:")],
            JOB_FILAMENT: [CallbackQueryHandler(job_filament, pattern=r"^filament:")],
            JOB_COLOUR:   [CallbackQueryHandler(job_colour,   pattern=r"^colour:")],
            JOB_HOURS:    [MessageHandler(filters.TEXT & ~filters.COMMAND, job_hours)],
            JOB_MINUTES:  [MessageHandler(filters.TEXT & ~filters.COMMAND, job_duration)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    ))

    # ── Admin conversation
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("admin", cmd_admin)],
        states={
            ADM_ACTION: [
                CallbackQueryHandler(adm_action,    pattern=r"^adm:"),
                CallbackQueryHandler(adm_clearq_cb, pattern=r"^adm_clearq:"),
            ],
            ADM_SID_DELETE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_sid_delete)],
            ADM_SID_TRANSFER: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_sid_transfer)],
            ADM_NEW_SID:      [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_new_sid)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    ))

    # ── Standalone handlers
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("status",  cmd_status))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(CommandHandler("myjobs",  cmd_myjobs))

    # ── Bed-cleared callback (outside conversation)
    app.add_handler(CallbackQueryHandler(cb_bed_cleared, pattern=r"^bedcleared:"))

    # ── Fallback
    app.add_handler(MessageHandler(filters.COMMAND, unknown))

    logger.info("Bot is running…")
    app.run_polling()

if __name__ == "__main__":
    main()