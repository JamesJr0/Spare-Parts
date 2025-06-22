import os
import logging
from flask import Flask, request
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
# Note the change here: Updater is not used for webhook-based bots in newer versions
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from functools import wraps

# Import the database service
import database

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
HEROKU_APP_NAME = os.environ.get("HEROKU_APP_NAME")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID"))

# --- LOGGING ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- FLASK APP FOR HEROKU (for webhook receiving) ---
app = Flask(__name__)

# --- TELEGRAM BOT APPLICATION SETUP ---
# In v20+, we build the application first
application = Application.builder().token(BOT_TOKEN).build()


# --- ADMIN DECORATOR ---
# A decorator to restrict access to admin-only commands
def admin_only(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id != ADMIN_USER_ID:
            await update.message.reply_text("Sorry, this is an admin-only command.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

# --- USER COMMANDS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /start command."""
    welcome_text = (
        "Welcome to the Mobile Parts Compatibility Bot!\n\n"
        "To get started, just send me a phone model name (e.g., 'Vivo Y20').\n\n"
        "If you are the admin, use /admin to manage the database."
    )
    await update.message.reply_text(welcome_text)

# --- ADMIN COMMANDS ---

@admin_only
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the main admin panel."""
    keyboard = [
        [InlineKeyboardButton("➕ Add/Update Part Compatibility", callback_data='admin_add_part')],
        [InlineKeyboardButton("📋 List All Phone Models", callback_data='admin_list_all')],
        [InlineKeyboardButton("❌ Delete a Phone Model", callback_data='admin_delete_start')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Welcome, Admin! What would you like to do?", reply_markup=reply_markup)

# --- MESSAGE HANDLERS & CONVERSATION LOGIC ---

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles all text messages to search for phones or process admin input."""
    user_state = context.user_data.get('state')

    if user_state == 'awaiting_model_for_add':
        await process_add_model_name(update, context)
    elif user_state == 'awaiting_compat_list':
        await process_compat_list(update, context)
    elif user_state == 'awaiting_model_for_delete':
        await process_delete_model_name(update, context)
    else:
        await search_phone_model(update, context)

async def search_phone_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Searches for a phone model in the database."""
    model_name = update.message.text.strip()
    phone = database.find_phone(model_name)

    if phone:
        context.user_data['searched_phone_id'] = phone['_id']
        keyboard = [
            [InlineKeyboardButton("📱 Find Compatible Display", callback_data='find_display')],
            [InlineKeyboardButton("🛡️ Find Compatible Screen Guard", callback_data='find_glass')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(f"I found `{phone['_id']}`. Which part are you looking for?", reply_markup=reply_markup, parse_mode='MarkdownV2')
    else:
        await update.message.reply_text(f"Sorry, I couldn't find '{model_name}' in my database.")

# --- CALLBACK QUERY (BUTTON PRESS) HANDLER ---

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles all button presses from inline keyboards."""
    query = update.callback_query
    await query.answer()

    # Route admin callbacks
    if query.data == 'admin_add_part':
        await ask_for_model_to_add(query, context)
    elif query.data == 'admin_list_all':
        await list_all_models(query, context)
    elif query.data == 'admin_delete_start':
        await ask_for_model_to_delete(query, context)
    elif query.data.startswith('link_'):
        part_type = query.data.split('_')[1]
        await ask_for_compat_list(query, context, part_type)
    
    # Route user callbacks
    elif query.data == 'find_display' or query.data == 'find_glass':
        await find_compatible_parts(query, context)

async def find_compatible_parts(query: Update, context: ContextTypes.DEFAULT_TYPE):
    """Finds and displays compatible parts for a searched phone."""
    phone_id = context.user_data.get('searched_phone_id')
    part_type = 'display' if query.data == 'find_display' else 'glass'

    if not phone_id:
        await query.edit_message_text("Error: I've lost track of which phone you searched for. Please search again.")
        return

    compatible_models = database.get_compatible_models(phone_id, part_type)

    if compatible_models:
        part_name = "Display" if part_type == 'display' else "Screen Guard"
        header = f"The *{part_name}* for `{phone_id}` is also compatible with:\n"
        message = header + "\n".join([f"• `{model.replace('-', '‑')}`" for model in compatible_models])
    else:
        message = f"Sorry, I don't have compatibility information for the `{part_type}` of `{phone_id}` yet."

    await query.edit_message_text(message, parse_mode='MarkdownV2')


# --- ADMIN WORKFLOW FUNCTIONS ---

async def ask_for_model_to_add(query: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asks admin for the model name to add/update."""
    context.user_data['state'] = 'awaiting_model_for_add'
    await query.edit_message_text("Please send me the full model name you want to add or update (e.g., `Oppo F21 Pro`).")

async def process_add_model_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes the model name sent by the admin."""
    model_name = update.message.text.strip()
    context.user_data['new_model_name'] = model_name
    context.user_data['state'] = None

    keyboard = [
        [InlineKeyboardButton("📱 Link Display", callback_data='link_display')],
        [InlineKeyboardButton("🛡️ Link Screen Guard", callback_data='link_glass')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"Okay, I've registered `{model_name}`. Which part do you want to link?", reply_markup=reply_markup, parse_mode='MarkdownV2')

async def ask_for_compat_list(query: Update, context: ContextTypes.DEFAULT_TYPE, part_type: str):
    """Asks for the list of other compatible models."""
    context.user_data['state'] = 'awaiting_compat_list'
    context.user_data['part_type'] = part_type
    part_name = "Display" if part_type == 'display' else "Screen Guard"
    await query.edit_message_text(f"Please send a comma-separated list of all other models that share the same *{part_name}*.\n\n(e.g., `Model A, Model B, Model C`)")

async def process_compat_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes the comma-separated list and updates the database."""
    try:
        main_model = context.user_data['new_model_name']
        part_type = context.user_data['part_type']
        
        other_models_raw = update.message.text.strip().split(',')
        other_models = [model.strip() for model in other_models_raw if model.strip()]
        
        all_models = [main_model] + other_models
        
        database.link_parts(all_models, part_type)

        await update.message.reply_text(f"✅ Success! Compatibility for the `{part_type}` has been set for:\n\n" + "• " + "\n• ".join(all_models))

    except Exception as e:
        logger.error(f"Error processing compatibility list: {e}")
        await update.message.reply_text("An error occurred. Please try again.")
    finally:
        for key in ['state', 'new_model_name', 'part_type']:
            if key in context.user_data:
                del context.user_data[key]

async def list_all_models(query: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fetches and lists all unique models from the database."""
    all_phones = database.get_all_phones()
    if not all_phones:
        await query.edit_message_text("The database is currently empty.")
        return
    
    message = "*All models in the database:*\n\n"
    message += "\n".join([f"• `{phone.replace('-', '‑')}`" for phone in sorted(all_phones)])

    if len(message) > 4096:
        message = message[:4090] + "\n..."
    
    await query.edit_message_text(message, parse_mode='MarkdownV2')
    
async def ask_for_model_to_delete(query: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asks admin for the model name to delete."""
    context.user_data['state'] = 'awaiting_model_for_delete'
    await query.edit_message_text("Please send me the exact model name you want to delete from the database.")
    
async def process_delete_model_name(update: Update, context: ContextTypes.DEFAULT_T):
    """Deletes a model from the database."""
    model_name = update.message.text.strip()
    context.user_data['state'] = None

    try:
        success = database.delete_phone(model_name)
        if success:
            await update.message.reply_text(f"✅ Successfully deleted `{model_name}` from the database.", parse_mode='MarkdownV2')
        else:
            await update.message.reply_text(f"Could not find `{model_name}` to delete.", parse_mode='MarkdownV2')
    except Exception as e:
        logger.error(f"Error deleting model: {e}")
        await update.message.reply_text("An error occurred during deletion.")


# --- MAIN SETUP AND WEBHOOK LOGIC ---
# Define handlers
start_handler = CommandHandler('start', start)
admin_handler = CommandHandler('admin', admin_panel)
message_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
callback_handler = CallbackQueryHandler(button_handler)

# Add handlers to the application
application.add_handler(start_handler)
application.add_handler(admin_handler)
application.add_handler(message_handler)
application.add_handler(callback_handler)


# Flask route to receive webhooks from Telegram
@app.route(f'/{BOT_TOKEN}', methods=['POST'])
async def respond():
    """Endpoint for Telegram webhook."""
    update = Update.de_json(request.get_json(force=True), application.bot)
    await application.process_update(update)
    return "ok"

# Flask route to set the webhook
@app.route('/setWebhook', methods=['GET', 'POST'])
def set_webhook():
    webhook_url = f"https://{HEROKU_APP_NAME}.herokuapp.com/{BOT_TOKEN}"
    # This function must be awaited
    import asyncio
    async def setup():
        await application.bot.set_webhook(url=webhook_url)
    
    asyncio.run(setup())
    return f"Webhook set to {webhook_url}"


# This part is optional but useful for local testing
if __name__ == '__main__':
    # This sets the webhook when the script starts.
    # In a production Heroku environment, you might want to run this once manually.
    webhook_url = f"https://{HEROKU_APP_NAME}.herokuapp.com/{BOT_TOKEN}"
    # This must be awaited
    import asyncio
    async def setup():
        await application.bot.set_webhook(url=webhook_url)
    
    asyncio.run(setup())
    
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

