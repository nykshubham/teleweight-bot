import os
import json
import datetime
import asyncio
from aiohttp import web
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ConversationHandler, ContextTypes, filters
)

# --- CONFIG ---
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PLAN_FILE = "plan.json"
WEIGHT_LOG_FILE = "weights.json"
MAX_WEEKLY_LOSS = 2.5

# --- YOUR FIXED DATA ---
USER_AGE = 25
USER_HEIGHT = 171
USER_ACTIVITY = 1.2
USER_GENDER = "male"

# Conversation states
CURRENT_WEIGHT, TARGET_WEIGHT, WEEKS = range(3)

# ------------------ PLAN FLOW ------------------

async def plan_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("What's your current weight (kg)?")
    return CURRENT_WEIGHT

async def plan_current(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['current_weight'] = float(update.message.text)
    await update.message.reply_text("What's your target weight (kg)?")
    return TARGET_WEIGHT

async def plan_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['target_weight'] = float(update.message.text)
    await update.message.reply_text("In how many weeks do you want to achieve it?")
    return WEEKS

async def plan_weeks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['weeks'] = float(update.message.text)
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

    with open(PLAN_FILE, "w") as f:
        json.dump(context.user_data, f)

    with open(WEIGHT_LOG_FILE, "w") as wf:
        json.dump([current_weight], wf)

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

    if not os.path.exists(PLAN_FILE):
        await update.message.reply_text("You need to set a plan first using /plan")
        return

    with open(PLAN_FILE, "r") as f:
        plan = json.load(f)

    target_weight = plan['target_weight']
    weeks = plan['weeks']
    start_date = datetime.date.fromisoformat(plan['start_date'])
    days_passed = (datetime.date.today() - start_date).days
    days_total = int(weeks * 7)
    days_left = max(days_total - days_passed, 0)

    # Determine direction (gain or loss)
    goal_type = "gain" if target_weight > plan['current_weight'] else "loss"

    # Store weight log
    weights = []
    if os.path.exists(WEIGHT_LOG_FILE):
        with open(WEIGHT_LOG_FILE, "r") as f:
            weights = json.load(f)

    previous_weight = weights[-1] if weights else weight
    weights.append(weight)
    with open(WEIGHT_LOG_FILE, "w") as f:
        json.dump(weights, f)

    # --- Direction-aware progress ---
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

    # 🏆 Celebration if goal reached (loss or gain)
    if progress_condition:
        msg += (
            "🏆🥳 AMAZING! You've reached your target weight!\n"
            "🎯 Time to set a new goal with /plan 💪"
        )
        if os.path.exists(WEIGHT_LOG_FILE):
            os.remove(WEIGHT_LOG_FILE)

    await update.message.reply_text(msg)

# ------------------ MAIN (Webhook Server) ------------------

async def main():
    application = ApplicationBuilder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("plan", plan_start)],
        states={
            CURRENT_WEIGHT: [MessageHandler(filters.TEXT, plan_current)],
            TARGET_WEIGHT: [MessageHandler(filters.TEXT, plan_target)],
            WEEKS: [MessageHandler(filters.TEXT, plan_weeks)],
        },
        fallbacks=[]
    )

    application.add_handler(conv)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, log_weight))

    # --- Webhook server setup ---
    async def webhook(request):
        try:
            data = await request.json()
            update = Update.de_json(data, application.bot)
            await application.process_update(update)
            return web.Response(text="ok")
        except Exception as e:
            print("❌ Webhook error:", e)
            return web.Response(status=500, text=str(e))

    # ✅ Create the web app here
    web_app = web.Application()
    web_app.router.add_post("/webhook", webhook)

    # ✅ Use your actual Render URL here
    webhook_url = "https://teleweight-bot.onrender.com/webhook"
    await application.bot.set_webhook(url=webhook_url)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8080)))
    await site.start()

    print("🌐 Webhook server is live on Render...")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
