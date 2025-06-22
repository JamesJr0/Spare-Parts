import os
import logging
from flask import Flask, request
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext, CallbackQueryHandler
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

# --- FLASK APP FOR HEROKU ---
app = Flask(__name__)

# --- ADMIN DECORATOR ---
# A decorator to restrict access to admin-only commands
def admin_only(func):
    @wraps(func)
    def wrapped(update: Update, context: CallbackContext, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id != ADMIN_USER_ID:
            update.message.reply_text("Sorry, this is an admin-only command.")
            return
        return func(update, context, *args, **kwargs)
    return wrapped

# --- USER COMMANDS ---

def start(update: Update, context: CallbackContext) -> None:
    """Handles the /start command."""
    welcome_text = (
        "Welcome to the Mobile Parts Compatibility Bot!\n\n"
        "To get started, just send me a phone model name (e.g., 'Vivo Y20').\n\n"
        "If you are the admin, use /admin to manage the database."
    )
    update.message.reply_text(welcome_text)

# --- ADMIN COMMANDS ---

@admin_only
def admin_panel(update: Update, context: CallbackContext) -> None:
    """Displays the main admin panel."""
    keyboard = [
        [InlineKeyboardButton("➕ Add/Update Part Compatibility", callback_data='admin_add_part')],
        [InlineKeyboardButton("📋 List All Phone Models", callback_data='admin_list_all')],
        [InlineKeyboardButton("❌ Delete a Phone Model", callback_data='admin_delete_start')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("Welcome, Admin! What would you like to do?", reply_markup=reply_markup)

# --- MESSAGE HANDLERS & CONVERSATION LOGIC ---

def handle_message(update: Update, context: CallbackContext) -> None:
    """Handles all text messages to search for phones or process admin input."""
    user_state = context.user_data.get('state')

    if user_state == 'awaiting_model_for_add':
        process_add_model_name(update, context)
    elif user_state == 'awaiting_compat_list':
        process_compat_list(update, context)
    elif user_state == 'awaiting_model_for_delete':
        process_delete_model_name(update, context)
    else:
        # Default behavior: search for a phone model
        search_phone_model(update, context)

def search_phone_model(update: Update, context: CallbackContext) -> None:
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
        update.message.reply_text(f"I found `{phone['_id']}`. Which part are you looking for?", reply_markup=reply_markup, parse_mode='MarkdownV2')
    else:
        update.message.reply_text(f"Sorry, I couldn't find '{model_name}' in my database.")

# --- CALLBACK QUERY (BUTTON PRESS) HANDLER ---

def button_handler(update: Update, context: CallbackContext) -> None:
    """Handles all button presses from inline keyboards."""
    query = update.callback_query
    query.answer() # Acknowledge the button press

    # Route admin callbacks
    if query.data == 'admin_add_part':
        ask_for_model_to_add(query, context)
    elif query.data == 'admin_list_all':
        list_all_models(query, context)
    elif query.data == 'admin_delete_start':
        ask_for_model_to_delete(query, context)
    elif query.data.startswith('link_'): # e.g., link_display or link_glass
        part_type = query.data.split('_')[1]
        ask_for_compat_list(query, context, part_type)
    
    # Route user callbacks
    elif query.data == 'find_display' or query.data == 'find_glass':
        find_compatible_parts(query, context)

def find_compatible_parts(query: Update, context: CallbackContext):
    """Finds and displays compatible parts for a searched phone."""
    phone_id = context.user_data.get('searched_phone_id')
    part_type = 'display' if query.data == 'find_display' else 'glass'

    if not phone_id:
        query.edit_message_text("Error: I've lost track of which phone you searched for. Please search again.")
        return

    compatible_models = database.get_compatible_models(phone_id, part_type)

    if compatible_models:
        part_name = "Display" if part_type == 'display' else "Screen Guard"
        header = f"The *{part_name}* for `{phone_id}` is also compatible with:\n"
        message = header + "\n".join([f"• `{model.replace('-', '‑')}`" for model in compatible_models])
    else:
        message = f"Sorry, I don't have compatibility information for the `{part_type}` of `{phone_id}` yet."

    query.edit_message_text(message, parse_mode='MarkdownV2')


# --- ADMIN WORKFLOW FUNCTIONS ---

def ask_for_model_to_add(query: Update, context: CallbackContext):
    """Asks admin for the model name to add/update."""
    context.user_data['state'] = 'awaiting_model_for_add'
    query.edit_message_text("Please send me the full model name you want to add or update (e.g., `Oppo F21 Pro`).")

def process_add_model_name(update: Update, context: CallbackContext):
    """Processes the model name sent by the admin."""
    model_name = update.message.text.strip()
    context.user_data['new_model_name'] = model_name
    context.user_data['state'] = None # Clear state

    keyboard = [
        [InlineKeyboardButton("📱 Link Display", callback_data='link_display')],
        [InlineKeyboardButton("🛡️ Link Screen Guard", callback_data='link_glass')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text(f"Okay, I've registered `{model_name}`. Which part do you want to link?", reply_markup=reply_markup, parse_mode='MarkdownV2')

def ask_for_compat_list(query: Update, context: CallbackContext, part_type: str):
    """Asks for the list of other compatible models."""
    context.user_data['state'] = 'awaiting_compat_list'
    context.user_data['part_type'] = part_type
    part_name = "Display" if part_type == 'display' else "Screen Guard"
    query.edit_message_text(f"Please send a comma-separated list of all other models that share the same *{part_name}*.\n\n(e.g., `Model A, Model B, Model C`)")

def process_compat_list(update: Update, context: CallbackContext):
    """Processes the comma-separated list and updates the database."""
    try:
        main_model = context.user_data['new_model_name']
        part_type = context.user_data['part_type']
        
        other_models_raw = update.message.text.strip().split(',')
        other_models = [model.strip() for model in other_models_raw if model.strip()]
        
        all_models = [main_model] + other_models
        
        database.link_parts(all_models, part_type)

        update.message.reply_text(f"✅ Success! Compatibility for the `{part_type}` has been set for:\n\n" + "• " + "\n• ".join(all_models))

    except Exception as e:
        logger.error(f"Error processing compatibility list: {e}")
        update.message.reply_text("An error occurred. Please try again.")
    finally:
        for key in ['state', 'new_model_name', 'part_type']:
            if key in context.user_data:
                del context.user_data[key]

def list_all_models(query: Update, context: CallbackContext):
    """Fetches and lists all unique models from the database."""
    all_phones = database.get_all_phones()
    if not all_phones:
        query.edit_message_text("The database is currently empty.")
        return
    
    message = "*All models in the database:*\n\n"
    message += "\n".join([f"• `{phone.replace('-', '‑')}`" for phone in sorted(all_phones)])

    if len(message) > 4096:
        message = message[:4090] + "\n..."
    
    query.edit_message_text(message, parse_mode='MarkdownV2')
    
def ask_for_model_to_delete(query: Update, context: CallbackContext):
    """Asks admin for the model name to delete."""
    context.user_data['state'] = 'awaiting_model_for_delete'
    query.edit_message_text("Please send me the exact model name you want to delete from the database.")
    
def process_delete_model_name(update: Update, context: CallbackContext):
    """Deletes a model from the database."""
    model_name = update.message.text.strip()
    context.user_data['state'] = None

    try:
        success = database.delete_phone(model_name)
        if success:
            update.message.reply_text(f"✅ Successfully deleted `{model_name}` from the database.", parse_mode='MarkdownV2')
        else:
            update.message.reply_text(f"Could not find `{model_name}` to delete.", parse_mode='MarkdownV2')
    except Exception as e:
        logger.error(f"Error deleting model: {e}")
        update.message.reply_text("An error occurred during deletion.")


# --- MAIN SETUP ---
@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def respond():
    """Endpoint for Telegram webhook."""
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "ok"

if __name__ == '__main__':
    bot = Bot(BOT_TOKEN)
    updater = Updater(bot.token, use_context=True)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("admin", admin_panel))
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    dispatcher.add_handler(CallbackQueryHandler(button_handler))

    webhook_url = f"https://{HEROKU_APP_NAME}.herokuapp.com/{BOT_TOKEN}"
    bot.set_webhook(webhook_url)
    
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
