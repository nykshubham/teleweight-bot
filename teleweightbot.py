import os
import json
import datetime
import asyncio
import traceback
from aiohttp import web
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ConversationHandler, ContextTypes, filters
)

# --- CONFIG ---
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or "6525065683:AAHbSg6-PhRA3obWwzRjfw-en1AclYpiq7g"
if not TOKEN:
    raise ValueError("❌ TELEGRAM_BOT_TOKEN is not set. Set it in Render environment variables.")

PLAN_FILE = "plan.json"
WEIGHT_LOG_FILE = "weights.json"
MAX_WEEKLY_LOSS = 2.5  # kg/week

# --- FIXED USER DATA ---
USER_AGE = 25
USER_HEIGHT = 171
USER_ACTIVITY = 1.2
USER_GENDER = "male"

# Conversation states
CURRENT_WEIGHT, TARGET_WEIGHT, WEEKS = range(3)

# ------------------ ASYNC FILE HELPERS ------------------

async def read_json(filename, default=None):
    """Async JSON read to avoid blocking"""
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _read_json_sync, filename, default)
    except Exception as e:
        print(f"Error reading {filename}: {e}")
        return default

def _read_json_sync(filename, default):
    if not os.path.exists(filename):
        return default
    with open(filename, "r") as f:
        return json.load(f)

async def write_json(filename, data):
    """Async JSON write to avoid blocking"""
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _write_json_sync, filename, data)
    except Exception as e:
        print(f"Error writing {filename}: {e}")

def _write_json_sync(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f)

# ------------------ PLAN FLOW ------------------

async def plan_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("What's your current weight (kg)?")
    return CURRENT_WEIGHT

async def plan_current(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['current_weight'] = float(update.message.text)
        await update.message.reply_text("What's your target weight (kg)?")
        return TARGET_WEIGHT
    except ValueError:
        await update.message.reply_text("Please enter a valid number.")
        return CURRENT_WEIGHT

async def plan_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['target_weight'] = float(update.message.text)
        await update.message.reply_text("In how many weeks do you want to achieve it?")
        return WEEKS
    except ValueError:
        await update.message.reply_text("Please enter a valid number.")
        return TARGET_WEIGHT

async def plan_weeks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['weeks'] = float(update.message.text)
    except ValueError:
        await update.message.reply_text("Please enter a valid number.")
        return WEEKS

    context.user_data['age'] = USER_AGE
    context.user_data['height'] = USER_HEIGHT
    context.user_data['activity_level'] = USER_ACTIVITY
    context.user_data['gender'] = USER_GENDER
    context.user_data['start_date'] = datetime.date.today().isoformat()

    current_weight = context.user_data['current_weight']
    target_weight = context.user_data['target_weight']
    weeks = context.user_data['weeks']

    total_loss_needed = current_weight - target_weight
    weekly_loss_needed = abs(total_loss_needed) / weeks

    if weekly_loss_needed > MAX_WEEKLY_LOSS:
        min_weeks_needed = abs(total_loss_needed) / MAX_WEEKLY_LOSS
        await update.message.reply_text(
            f"❌ This plan is impossible.\n"
            f"You're trying to change {weekly_loss_needed:.2f} kg/week.\n"
            f"📅 Minimum safe time: {min_weeks_needed:.1f} weeks.\n"
            f"Please restart planning with /plan and set a realistic timeline."
        )
        return ConversationHandler.END

    await write_json(PLAN_FILE, context.user_data)
    await write_json(WEIGHT_LOG_FILE, [current_weight])

    goal_type = "gain" if target_weight > current_weight else "lose"

    await update.message.reply_text(
        f"✅ Plan saved!\n🎯 Target: {target_weight} kg\n⏱️ Timeline: {weeks} weeks\n📆 Starting today!\n📈 Goal: {goal_type} weight"
    )
    return ConversationHandler.END

# ------------------ WEIGHT LOGGING ------------------

async def log_weight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        weight = float(update.message.text)
    except ValueError:
        return

    plan = await read_json(PLAN_FILE)
    if not plan:
        await update.message.reply_text("You need to set a plan first using /plan")
        return

    target_weight = plan['target_weight']
    weeks = plan['weeks']
    start_date = datetime.date.fromisoformat(plan['start_date'])
    days_passed = (datetime.date.today() - start_date).days
    days_total = int(weeks * 7)
    days_left = max(days_total - days_passed, 0)

    goal_type = "gain" if target_weight > plan['current_weight'] else "loss"

    weights = await read_json(WEIGHT_LOG_FILE, [])
    previous_weight = weights[-1] if weights else weight
    weights.append(weight)
    await write_json(WEIGHT_LOG_FILE, weights)

    if goal_type == "loss":
        remaining = max(weight - target_weight, 0)
        progress_condition = weight <= target_weight
        milestone = previous_weight - weight
        progress_msg = f"📊 Remaining to lose: {remaining:.2f} kg\n"
        milestone_achieved = milestone >= 0.05
        milestone_msg = f"🎉 Nice work! You lost {milestone:.2f} kg since last time!\n" if milestone_achieved else ""
    else:
        remaining = max(target_weight - weight, 0)
        progress_condition = weight >= target_weight
        milestone = weight - previous_weight
        progress_msg = f"📊 Remaining to gain: {remaining:.2f} kg\n"
        milestone_achieved = milestone >= 0.05
        milestone_msg = f"🎉 Awesome! You gained {milestone:.2f} kg since last time!\n" if milestone_achieved else ""

    msg = (
        f"📉 Weight logged: {weight:.2f} kg\n"
        f"🎯 Target: {target_weight:.2f} kg\n"
        f"📆 Days left: {days_left}\n"
        f"{progress_msg}"
    )

    if milestone_achieved:
        msg += milestone_msg

    if progress_condition:
        msg += (
            "🏆🥳 AMAZING! You've reached your target weight!\n"
            "🎯 Time to set a new goal with /plan 💪"
        )
        # Async file deletion
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: os.path.exists(WEIGHT_LOG_FILE) and os.remove(WEIGHT_LOG_FILE))

    await update.message.reply_text(msg)

# ------------------ MAIN (Webhook Server) ------------------

async def main():
    application = ApplicationBuilder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("plan", plan_start)],
        states={
            CURRENT_WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, plan_current)],
            TARGET_WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, plan_target)],
            WEEKS: [MessageHandler(filters.TEXT & ~filters.COMMAND, plan_weeks)],
        },
        fallbacks=[]
    )

    application.add_handler(conv)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, log_weight))

    await application.initialize()
    await application.start()

    # --- Create web server ---
    web_app = web.Application()

    async def webhook(request):
        """
        CRITICAL: Respond to Telegram immediately, then process update asynchronously
        """
        try:
            data = await request.json()
            print("📩 Incoming update")
            
            if not data:
                return web.Response(text="ok")

            # Parse the update
            update = Update.de_json(data, application.bot)
            
            # Respond immediately to Telegram (avoid timeout)
            response = web.Response(text="ok")
            
            # Process update asynchronously in background
            asyncio.create_task(safe_process_update(application, update))
            
            return response

        except Exception as e:
            print("❌ Webhook error:", e)
            print(traceback.format_exc())
            # Still return 200 to avoid Telegram retries
            return web.Response(text="ok")

    async def safe_process_update(app, update):
        """Process update with timeout protection"""
        try:
            await asyncio.wait_for(
                app.process_update(update),
                timeout=50.0  # Internal timeout (less than Telegram's 60s)
            )
        except asyncio.TimeoutError:
            print("⏱️ Update processing timed out")
        except Exception as e:
            print(f"❌ Error processing update: {e}")
            print(traceback.format_exc())

    async def healthcheck(request):
        return web.Response(text="✅ Bot is alive")

    web_app.router.add_post("/webhook", webhook)
    web_app.router.add_get("/", healthcheck)

    webhook_url = "https://teleweight-bot.onrender.com/webhook"
    print(f"🔗 Setting webhook to: {webhook_url}")
    await application.bot.set_webhook(url=webhook_url)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8080)))
    await site.start()

    print("🌐 Webhook server is live and listening...")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
