import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from functools import wraps

# Import the database service
import database

# --- CONFIGURATION ---
try:
    BOT_TOKEN = os.environ["BOT_TOKEN"]
    HEROKU_APP_NAME = os.environ["HEROKU_APP_NAME"]
    ADMIN_USER_ID = int(os.environ["ADMIN_USER_ID"])
except KeyError as e:
    logging.critical(f"Missing essential environment variable: {e}")
    raise RuntimeError(f"Missing essential environment variable: {e}")

# --- LOGGING ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- TELEGRAM BOT APPLICATION SETUP ---
application = Application.builder().token(BOT_TOKEN).build()


# --- ADMIN DECORATOR ---
def admin_only(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if not user or user.id != ADMIN_USER_ID:
            if update.callback_query:
                await update.callback_query.answer("Sorry, this is an admin-only command.", show_alert=True)
            elif update.message:
                await update.message.reply_text("Sorry, this is an admin-only command.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

# --- COMMAND & MESSAGE HANDLERS (No changes needed here) ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Welcome to the Mobile Parts Compatibility Bot!\n\n"
        "To get started, just send me a phone model name (e.g., 'Vivo Y20').\n\n"
        "If you are the admin, use /admin to manage the database."
    )

@admin_only
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton("➕ Add/Update Part Compatibility", callback_data='admin_add_part')],
        [InlineKeyboardButton("📋 List All Phone Models", callback_data='admin_list_all')],
        [InlineKeyboardButton("❌ Delete a Phone Model", callback_data='admin_delete_start')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Welcome, Admin! What would you like to do?", reply_markup=reply_markup)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_state = context.user_data.get('state')
    if user_state == 'awaiting_model_for_add': await process_add_model_name(update, context)
    elif user_state == 'awaiting_compat_list': await process_compat_list(update, context)
    elif user_state == 'awaiting_model_for_delete': await process_delete_model_name(update, context)
    else: await search_phone_model(update, context)

async def search_phone_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == 'admin_add_part': await ask_for_model_to_add(query, context)
    elif data == 'admin_list_all': await list_all_models(query, context)
    elif data == 'admin_delete_start': await ask_for_model_to_delete(query, context)
    elif data.startswith('link_'): await ask_for_compat_list(query, context, data.split('_')[1])
    elif data in ['find_display', 'find_glass']: await find_compatible_parts(query, context)

async def find_compatible_parts(query: Update, context: ContextTypes.DEFAULT_TYPE):
    phone_id = context.user_data.get('searched_phone_id')
    part_type = 'display' if query.data == 'find_display' else 'glass'
    if not phone_id:
        await query.edit_message_text("Error: I've lost track of the phone. Please search again.")
        return
    compatible_models = database.get_compatible_models(phone_id, part_type)
    part_name = "Display" if part_type == 'display' else "Screen Guard"
    safe_phone_id = str(phone_id).replace('-', r'\-')
    if compatible_models:
        header = f"The *{part_name}* for `{safe_phone_id}` is also compatible with:\n"
        model_list = "\n".join([f"• `{str(model).replace('-', r'-')}`" for model in compatible_models])
        message = header + model_list
    else:
        message = f"Sorry, I don't have compatibility information for the `{part_type}` of `{safe_phone_id}` yet."
    await query.edit_message_text(message, parse_mode='MarkdownV2')

@admin_only
async def ask_for_model_to_add(query: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['state'] = 'awaiting_model_for_add'
    await query.edit_message_text("Please send the full model name to add or update (e.g., `Oppo F21 Pro`).")

async def process_add_model_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    model_name = update.message.text.strip()
    context.user_data['new_model_name'] = model_name
    context.user_data['state'] = None
    keyboard = [[InlineKeyboardButton("📱 Link Display", callback_data='link_display')], [InlineKeyboardButton("🛡️ Link Screen Guard", callback_data='link_glass')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"Okay, I've registered `{model_name}`. Which part to link?", reply_markup=reply_markup, parse_mode='MarkdownV2')

@admin_only
async def ask_for_compat_list(query: Update, context: ContextTypes.DEFAULT_TYPE, part_type: str):
    context.user_data.update({'state': 'awaiting_compat_list', 'part_type': part_type})
    part_name = "Display" if part_type == 'display' else "Screen Guard"
    await query.edit_message_text(f"Please send a comma-separated list of all other models that share the same *{part_name}*.")

async def process_compat_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        main_model, part_type = context.user_data['new_model_name'], context.user_data['part_type']
        other_models = [m.strip() for m in update.message.text.strip().split(',') if m.strip()]
        database.link_parts([main_model] + other_models, part_type)
        await update.message.reply_text(f"✅ Success! Compatibility for the `{part_type}` has been set.")
    except Exception as e:
        logger.error(f"Error processing compatibility list: {e}")
        await update.message.reply_text("An error occurred. Please try again.")
    finally:
        for key in ['state', 'new_model_name', 'part_type']: context.user_data.pop(key, None)

@admin_only
async def list_all_models(query: Update, context: ContextTypes.DEFAULT_TYPE):
    all_phones = database.get_all_phones()
    if not all_phones:
        await query.edit_message_text("The database is currently empty.")
        return
    safe_phones = [str(p).replace('-', r'\-') for p in sorted(all_phones)]
    message = "*All models in the database:*\n\n" + "\n".join([f"• `{p}`" for p in safe_phones])
    await query.edit_message_text(message[:4090], parse_mode='MarkdownV2')

@admin_only
async def ask_for_model_to_delete(query: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['state'] = 'awaiting_model_for_delete'
    await query.edit_message_text("Please send the exact model name you want to delete.")
    
async def process_delete_model_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    model_name = update.message.text.strip()
    context.user_data['state'] = None
    try:
        success = database.delete_phone(model_name)
        safe_name = model_name.replace('-', r'\-')
        msg = f"✅ Successfully deleted `{safe_name}`." if success else f"Could not find `{safe_name}` to delete."
        await update.message.reply_text(msg, parse_mode='MarkdownV2')
    except Exception as e:
        logger.error(f"Error deleting model: {e}")
        await update.message.reply_text("An error occurred during deletion.")


# --- MAIN EXECUTION BLOCK ---
if __name__ == "__main__":
    # Add all handlers to the application
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('admin', admin_panel))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_handler))

    # Heroku provides the port to listen on via the 'PORT' environment variable
    PORT = int(os.environ.get('PORT', '8443'))
    
    # The webhook URL is where Telegram will send updates
    webhook_url = f"https://{HEROKU_APP_NAME}.herokuapp.com/{BOT_TOKEN}"

    # This command starts a web server, sets the webhook, and handles updates.
    # It's the all-in-one solution from the library for this kind of deployment.
    logger.info(f"Starting webhook bot on port {PORT}")
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=BOT_TOKEN,
        webhook_url=webhook_url
    )

