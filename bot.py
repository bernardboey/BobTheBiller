import datetime
import decimal
import logging
import os
import queue
import random

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode, ForceReply
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler, Updater, MessageHandler, Filters
from telegram.user import User

import persistence

DATA_REGISTER = "r"
DATA_PAYMENT_DELETE = "pd"
DATA_PAYMENT_DELETE_YES = "py"
DATA_PAYMENT_DELETE_NO = "pn"
DATA_MODIFY_PARTICIPANTS = "mp"
DATA_MODIFY_PARTICIPANTS_SELECTED = "ps"
DATA_CHANGE_PAYER = "cp"
DATA_CHANGE_PAYER_SELECTED = "pu"
DATA_SPLIT_MANUALLY = "sm"
DATA_SPLIT_EQUALLY = "se"
DATA_BILL_DELETE = "bd"
DATA_BILL_DELETE_YES = "by"
DATA_BILL_REDISPLAY = "br"

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

persistence = persistence.MongoPersistence()
updater = Updater(token=TOKEN, use_context=True, persistence=persistence)
dispatcher = updater.dispatcher

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

keyboard_register = [
    [InlineKeyboardButton("Register", callback_data=DATA_REGISTER)],
]

markup_register = InlineKeyboardMarkup(keyboard_register)


def get_delete_payment_markup(payment_id: int):
    delete_keyboard = [
        [InlineKeyboardButton("❌ Delete", callback_data=DATA_PAYMENT_DELETE + str(payment_id))],
    ]
    delete_markup = InlineKeyboardMarkup(delete_keyboard)
    return delete_markup


def get_bill_markup(bill_id: int, equal_split: bool = True):
    keyboard = [
        [InlineKeyboardButton("Add/Remove " + choose_random_emoji(), callback_data=DATA_MODIFY_PARTICIPANTS + str(bill_id))],
        [InlineKeyboardButton("Change Payer 🤑", callback_data=DATA_CHANGE_PAYER + str(bill_id))],
        [InlineKeyboardButton("Split Manually 🧮", callback_data=DATA_SPLIT_MANUALLY + str(bill_id))
         if equal_split
         else InlineKeyboardButton("Split Equally ⚖", callback_data=DATA_SPLIT_EQUALLY + str(bill_id))],
        [InlineKeyboardButton("❌ Delete", callback_data=DATA_BILL_DELETE + str(bill_id))],
    ]
    markup = InlineKeyboardMarkup(keyboard)
    return markup


MESSAGE_REGISTERING = ("<b>Register To Start Using Bob The Biller</b>\n\nHello, I'm Bob The Biller! "
                       "To start splitting bills and tracking expenses, everyone in the chat will need to "
                       "register by clicking on the button below. For help, please see /help")


def init(update: Update, context: CallbackContext):
    context.chat_data["registered"] = []
    context.chat_data["payments_id"] = 0
    context.chat_data["bills_id"] = 0
    context.chat_data["payments"] = {}
    context.chat_data["bills"] = {}
    context.chat_data["debts"] = {}
    context.chat_data["active_manual_split"] = {
        "active": False,
        "bill_id": None,
        "message_id": None,
        "remaining_participants": [],
        "current_participant": None
    }

    context.bot.send_message(chat_id=update.effective_chat.id,
                             text=MESSAGE_REGISTERING,
                             reply_markup=markup_register,
                             parse_mode=ParseMode.HTML)


def choose_random_emoji():
    return random.choice(["👫", "👭", "👬"])


def help_handler(update: Update, context: CallbackContext):
    context.bot.send_message(chat_id=update.effective_chat.id,
                             parse_mode=ParseMode.HTML,
                             text=("Hello! Here are the available commands:\n\n"
                                   "<b>/bill - Split a bill with other people in the group</b>\n"
                                   "`/bill [amount] [description]`\n"
                                   "E.g.: /bill 23 Taxi\n\n"
                                   "You can optionally add usernames at the end to indicate who to split with:\n"
                                   "E.g.: /bill 23 Taxi @username @username\n\n"
                                   "To split with everyone:\n"
                                   "E.g. : /bill 23 Taxi @all\n\n"
                                   "<b>/paid - Record a payment to/from someone else</b>\n"
                                   "`/paid [amount] [username]`\n"
                                   "E.g.: /paid 24.50 @username\n\n"
                                   "<b>/list - See list of outstanding debts</b>"))


def button_register(update: Update, context: CallbackContext):
    query = update.callback_query
    if query.from_user.id in context.chat_data["registered"]:
        query.answer(text="You've already registered")
    else:
        context.chat_data["registered"].append(query.from_user.id)
        existing_users = list(context.chat_data["debts"].keys())
        context.chat_data["debts"][query.from_user.id] = {_id: 0 for _id in existing_users}
        context.chat_data["debts"][query.from_user.id][None] = 0
        for _id in existing_users:
            context.chat_data["debts"][_id][query.from_user.id] = 0

        names = [update.effective_chat.get_member(user_id).user.full_name for user_id in
                 context.chat_data["registered"]]

        query.answer(text="You've successfully registered!")

        logging.log(logging.INFO, f"Member registered: {query.from_user.username}, {query.from_user.id}")

        # Minus 1 to account for the bot itself
        num_registered = len(context.chat_data["registered"])
        if num_registered == update.effective_chat.get_member_count() - 1:
            query.edit_message_text(text=f"{MESSAGE_REGISTERING}\n\n<u>Registered</u>:\n" + "\n".join(names)
                                         + f"\n\nTotal: {num_registered}"
                                         + f"\n\nGreat! Everyone has registered",
                                    parse_mode=ParseMode.HTML)
        else:
            query.edit_message_text(text=f"{MESSAGE_REGISTERING}\n\n<u>Registered</u>:\n" + "\n".join(names)
                                         + f"\n\nTotal: {num_registered}",
                                    reply_markup=markup_register, parse_mode=ParseMode.HTML)


def new_member(update: Update, context: CallbackContext):
    for user in update.message.new_chat_members:
        if user.id == update.effective_chat.bot.id:
            # Bot just added to chat group
            if not context.chat_data:
                init(update, context)
            logging.log(logging.INFO, f"Added to chat {update.effective_chat.id}")
        else:
            context.chat_data["registered"].append(user.id)
            existing_users = list(context.chat_data["debts"].keys())
            context.chat_data["debts"][user.id] = {_id: 0 for _id in existing_users}
            context.chat_data["debts"][user.id][None] = 0
            for _id in existing_users:
                context.chat_data["debts"][_id][user.id] = 0
            logging.log(logging.INFO, f"New member (chat_id: {update.effective_chat.id}, user_id {user.id}, "
                                      f"name: {user.full_name}, username: {user.username})")


def left_member(update: Update, context: CallbackContext):
    user = update.message.left_chat_member

    if user.id == update.effective_chat.bot.id:
        # Bot removed from chat group
        logging.log(logging.INFO, f"Removed from chat {update.effective_chat.id}")
    else:
        try:
            context.chat_data["registered"].remove(user.id)
        except ValueError:
            pass
        logging.log(logging.INFO, f"Left member (chat_id: {update.effective_chat.id}, user_id {user.id}, "
                                  f"name: {user.full_name}, username: {user.username})")


def add_bill(update: Update, context: CallbackContext):
    try:
        amt_string, name, *list_of_users = context.args
    except ValueError:
        context.bot.send_message(chat_id=update.effective_chat.id,
                                 text="Invalid format. Please type /bill [amount] [description]")
        return

    try:
        amt = float(amt_string)
    except ValueError:
        context.bot.send_message(chat_id=update.effective_chat.id, text="Invalid amount, please try again.")
        return

    if amt == 0:
        context.bot.send_message(chat_id=update.effective_chat.id, text="Invalid amount (cannot be zero).")
        return
    elif amt < 0:
        context.bot.send_message(chat_id=update.effective_chat.id, text="Invalid amount (cannot be negative).")
        return

    if -decimal.Decimal(amt_string).as_tuple().exponent > 2:
        context.bot.send_message(chat_id=update.effective_chat.id, text="Invalid amount.")
        return

    new_id = context.chat_data["bills_id"]
    context.chat_data["bills_id"] += 1
    sender = update.message.from_user

    if not list_of_users:
        # No one part of the bill
        participant_ids = {
            sender.id: amt
        }
    elif "@all" in list_of_users:
        # Everyone part of the bill
        avg = amt / len(context.chat_data["registered"])
        participant_ids = {
            user_id: avg for user_id in context.chat_data["registered"]
        }
    else:
        all_users = [update.effective_chat.get_member(user_id).user for user_id in context.chat_data["registered"]]
        users = []
        for username in list_of_users:
            for user in all_users:
                if username[1:] == user.username:
                    users.append(user)
                    break
            else:
                context.bot.send_message(chat_id=update.effective_chat.id,
                                         text=f"Unrecognised username: {username[1:]}")
                return
        if sender not in users:
            users.append(sender)
        avg = amt / len(users)
        participant_ids = {
            user.id: avg for user in users
        }

    for user_id, amt in participant_ids.items():
        if sender.id != user_id:
            context.chat_data["debts"][sender.id][user_id] -= amt
            context.chat_data["debts"][user_id][sender.id] += amt

    context.chat_data["bills"][new_id] = {
        "name": name, "amt": amt, "payer": sender.id, "participants": participant_ids,
        "datetime": update.message.date, "equal": True, "unclaimed": 0
    }
    participants: list[tuple[User, float]] = [(update.effective_chat.get_member(user_id).user, amt)
                                              for user_id, amt in participant_ids.items()]
    context.bot.send_message(chat_id=update.effective_chat.id,
                             parse_mode=ParseMode.HTML,
                             reply_markup=get_bill_markup(new_id),
                             text=get_bill_message(name, amt, sender, participants))


def button_bill_modify_participants(update: Update, context: CallbackContext, bill_id=None):
    query = update.callback_query
    if bill_id is None:
        bill_id = get_bill_id(query)
        query.answer()
    bill = context.chat_data["bills"][bill_id]
    users = sorted((update.effective_chat.get_member(user_id).user for user_id in context.chat_data["registered"]),
                   key=lambda user: user.full_name)
    keyboard = [
                   [InlineKeyboardButton(
                       f"✅ {user.full_name}" if user.id in bill["participants"] else user.full_name,
                       callback_data=DATA_MODIFY_PARTICIPANTS_SELECTED + str(bill_id) + "," + str(user.id)
                   )]
                   for user in users
               ] + [[InlineKeyboardButton("⬅", callback_data=DATA_BILL_REDISPLAY + str(bill_id))]]
    markup = InlineKeyboardMarkup(keyboard)
    query.edit_message_text(text=f"<b>Who split the bill for <i>{bill['name']}</i>?</b>",
                            reply_markup=markup,
                            parse_mode=ParseMode.HTML)


def button_bill_modify_participants_selected(update: Update, context: CallbackContext):
    query = update.callback_query
    bill_id, user_id = (int(arg) for arg in query.data[2:].split(","))
    user = update.effective_chat.get_member(user_id).user
    payer_id = context.chat_data["bills"][bill_id]["payer"]
    if user_id in context.chat_data["bills"][bill_id]["participants"]:
        # Remove from participants
        unclaimed = context.chat_data["bills"][bill_id]["participants"][user_id]
        del context.chat_data["bills"][bill_id]["participants"][user_id]
        if payer_id != user_id:
            context.chat_data["debts"][payer_id][user_id] += unclaimed
            context.chat_data["debts"][user_id][payer_id] -= unclaimed
        if context.chat_data["bills"][bill_id]["equal"]:
            redistribute_amounts(context, bill_id)
            query.answer(f"{user.full_name} removed from bill")
        else:
            context.chat_data["bills"][bill_id]["unclaimed"] += unclaimed
            context.chat_data["debts"][payer_id][None] -= unclaimed
            query.answer(f"{user.full_name} removed from bill, ${fmt_amt(unclaimed)} added to unclaimed amount")
    else:
        # Add to participants
        if context.chat_data["bills"][bill_id]["equal"]:
            context.chat_data["bills"][bill_id]["participants"][user_id] = 0
            redistribute_amounts(context, bill_id)
            query.answer(f"{user.full_name} added to bill")
        else:
            unclaimed = context.chat_data["bills"][bill_id]["unclaimed"]
            if unclaimed > 0:
                context.chat_data["bills"][bill_id]["participants"][user_id] = unclaimed
                context.chat_data["bills"][bill_id]["unclaimed"] = 0
                if payer_id != user_id:
                    context.chat_data["debts"][payer_id][user_id] -= unclaimed
                    context.chat_data["debts"][user_id][payer_id] += unclaimed
                context.chat_data["debts"][payer_id][None] += unclaimed
                query.answer(f"{user.full_name} added to bill, took on unclaimed amount of {unclaimed}")
            else:
                query.answer(f"{user.full_name} added to bill, with $0 on their tab")
    button_bill_modify_participants(update, context, bill_id)


def redistribute_amounts(context: CallbackContext, bill_id: int):
    num_participants = len(context.chat_data["bills"][bill_id]["participants"])
    if num_participants == 0:
        return
    payer_id = context.chat_data["bills"][bill_id]["payer"]
    avg = context.chat_data["bills"][bill_id]["amt"] / num_participants
    for _id in context.chat_data["bills"][bill_id]["participants"]:
        old_amt = context.chat_data["bills"][bill_id]["participants"][_id]
        context.chat_data["bills"][bill_id]["participants"][_id] = avg
        if payer_id != _id:
            context.chat_data["debts"][payer_id][_id] -= avg - old_amt
            context.chat_data["debts"][_id][payer_id] += avg - old_amt


# TODO: SLOW
def button_bill_change_payer(update: Update, context: CallbackContext):
    query = update.callback_query
    bill_id = get_bill_id(query)
    query.answer()
    users = sorted((update.effective_chat.get_member(user_id).user for user_id in context.chat_data["registered"]),
                   key=lambda user: user.full_name)
    keyboard = [
                   [InlineKeyboardButton(user.full_name,
                                         callback_data=DATA_CHANGE_PAYER_SELECTED + str(bill_id) + "," + str(user.id))]
                   for user in users if context.chat_data["bills"][bill_id]["payer"] != user.id
               ] + [[InlineKeyboardButton("⬅", callback_data=DATA_BILL_REDISPLAY + str(bill_id))]]
    markup = InlineKeyboardMarkup(keyboard)
    query.edit_message_text(text="<b>Please choose the correct payer:</b>",
                            reply_markup=markup,
                            parse_mode=ParseMode.HTML)


def button_bill_choose_payer(update: Update, context: CallbackContext):
    query = update.callback_query
    bill_id, payer_id = (int(arg) for arg in query.data[2:].split(","))
    payer = update.effective_chat.get_member(payer_id).user
    old_payer_id = context.chat_data["bills"][bill_id]["payer"]
    context.chat_data["bills"][bill_id]["payer"] = payer.id

    for user_id, amt in context.chat_data["bills"][bill_id]["participants"].items():
        if old_payer_id != user_id:
            context.chat_data["debts"][old_payer_id][user_id] += amt
            context.chat_data["debts"][user_id][old_payer_id] -= amt
        if payer.id != user_id:
            context.chat_data["debts"][payer.id][user_id] -= amt
            context.chat_data["debts"][user_id][payer.id] += amt

    name, amt, payer, participant_ids, participants, date = get_bill_details(update, context, bill_id)
    query.answer(f"Payer changed to {payer.full_name}")
    query.edit_message_text(text=get_bill_message(name, amt, payer, participants),
                            reply_markup=get_bill_markup(bill_id, context.chat_data["bills"][bill_id]["equal"]),
                            parse_mode=ParseMode.HTML)


def button_bill_split_manually(update: Update, context: CallbackContext):
    query = update.callback_query
    bill_id = get_bill_id(query)
    name, amt, payer, participant_ids, participants, date = get_bill_details(update, context, bill_id)
    if not participants:
        query.answer("Cannot split as there is no one on this bill")
    else:
        query.answer("Please follow the instructions carefully to split the bill manually.")
    context.chat_data["bills"][bill_id]["equal"] = False
    context.chat_data["active_manual_split"] = {
        "active": True,
        "bill_id": bill_id,
        "message_id": query.message.message_id,
        "remaining_participants": [user.id for user, _ in sorted(participants, key=lambda user: user[0].full_name, reverse=True)],
        "current_participant": None
    }
    user_id = context.chat_data["active_manual_split"]["remaining_participants"].pop()
    context.chat_data["active_manual_split"]["current_participant"] = user_id
    user = update.effective_chat.get_member(user_id).user
    query.edit_message_text(text=query.message.text_html, parse_mode=ParseMode.HTML)
    query.message.reply_text(reply_markup=ForceReply(selective=True, input_field_placeholder="amount"),
                             text=f"@{query.from_user.username}, how much should {user.full_name} pay for this bill?")


def split_manually(update: Update, context: CallbackContext):
    if context.chat_data["active_manual_split"]["active"]:
        prev_user_id = context.chat_data["active_manual_split"]["current_participant"]
        try:
            amt = float(update.message.text)
        except ValueError:
            update.message.reply_text(reply_markup=ForceReply(selective=True, input_field_placeholder="amount"),
                                      text=f"Invalid amount, please try again.")
            return
        bill_id = context.chat_data["active_manual_split"]["bill_id"]
        payer_id = context.chat_data["bills"][bill_id]["payer"]
        old_amt = context.chat_data["bills"][bill_id]["participants"][prev_user_id]
        context.chat_data["bills"][bill_id]["participants"][prev_user_id] = amt
        if payer_id != prev_user_id:
            context.chat_data["debts"][payer_id][prev_user_id] -= amt - old_amt
            context.chat_data["debts"][prev_user_id][payer_id] += amt - old_amt
        name, amt, payer, participant_ids, participants, date = get_bill_details(update, context, bill_id)
        context.bot.edit_message_text(chat_id=update.effective_chat.id,
                                      message_id=context.chat_data["active_manual_split"]["message_id"],
                                      parse_mode=ParseMode.HTML,
                                      reply_markup=get_bill_markup(bill_id, context.chat_data["bills"][bill_id]["equal"]) if not context.chat_data["active_manual_split"]["remaining_participants"] else None,
                                      text=get_bill_message(name, amt, payer, participants))

        if not context.chat_data["active_manual_split"]["remaining_participants"]:
            context.chat_data["active_manual_split"]["active"] = False
            update.message.reply_text(text=f"All done!")
        else:
            user_id = context.chat_data["active_manual_split"]["remaining_participants"].pop()
            context.chat_data["active_manual_split"]["current_participant"] = user_id
            user = update.effective_chat.get_member(user_id).user
            update.message.reply_text(reply_markup=ForceReply(selective=True, input_field_placeholder="amount"),
                                      text=f"@{update.message.from_user.username}, how much should {user.full_name} pay?")


def button_bill_split_equally(update: Update, context: CallbackContext):
    query = update.callback_query
    bill_id = get_bill_id(query)
    redistribute_amounts(context, bill_id)
    context.chat_data["bills"][bill_id]["equal"] = True
    name, amt, payer, participant_ids, participants, date = get_bill_details(update, context, bill_id)
    query.answer("Bill changed to split equally")
    query.edit_message_text(text=get_bill_message(name, amt, payer, participants),
                            reply_markup=get_bill_markup(bill_id, context.chat_data["bills"][bill_id]["equal"]),
                            parse_mode=ParseMode.HTML)


def button_bill_delete(update: Update, context: CallbackContext):
    query = update.callback_query
    bill_id = get_bill_id(query)
    query.answer()

    keyboard = [
        [InlineKeyboardButton("Yes", callback_data=DATA_BILL_DELETE_YES + str(bill_id)),
         InlineKeyboardButton("No", callback_data=DATA_BILL_REDISPLAY + str(bill_id))],
    ]
    markup = InlineKeyboardMarkup(keyboard)

    query.edit_message_text(text="<b>Confirm delete bill?</b>",
                            reply_markup=markup,
                            parse_mode=ParseMode.HTML)


def button_bill_delete_confirm(update: Update, context: CallbackContext):
    query = update.callback_query
    bill_id = get_bill_id(query)
    query.answer()
    name, amt, payer, participant_ids, participants, date = get_bill_details(update, context, bill_id)
    del context.chat_data["bills"][bill_id]
    query.edit_message_text(text=f"<s>{get_bill_message(name, amt, payer, participants)}</s>\n\n"
                                 f"Deleted by {query.from_user.full_name} on {query.message.date}",
                            parse_mode=ParseMode.HTML)


def button_bill_redisplay(update: Update, context: CallbackContext):
    query = update.callback_query
    bill_id = get_bill_id(query)
    query.answer()
    name, amt, payer, participant_ids, participants, date = get_bill_details(update, context, bill_id)
    query.edit_message_text(text=get_bill_message(name, amt, payer, participants),
                            reply_markup=get_bill_markup(bill_id, context.chat_data["bills"][bill_id]["equal"]),
                            parse_mode=ParseMode.HTML)


def get_bill_id(query):
    return int(query.data[2:])


def get_bill_details(update: Update, context: CallbackContext, bill_id: int):
    bill = context.chat_data["bills"][bill_id]
    payer: User = update.effective_chat.get_member(bill["payer"]).user
    name: str = bill["name"]
    amt: float = bill["amt"]
    participant_ids: dict[int, float] = bill["participants"]
    participants: list[tuple[User, float]] = [(update.effective_chat.get_member(user_id).user, amt)
                                              for user_id, amt in participant_ids.items()]
    date: datetime.datetime = bill["datetime"]
    return name, amt, payer, participant_ids, participants, date


def get_bill_message(name: str, amt: float, payer: User, participants: list[tuple[User, float]]):
    participants = sorted(participants, key=lambda user: user[0].full_name)
    participant_list = (f"• {user.full_name} (@{user.username}): ${fmt_amt(amt)}" for user, amt in participants)
    return (f"<b><u>Split Bill: {name}</u></b>\n"
            f"<b>${fmt_amt(amt)}</b>, paid by <b>{payer.full_name}</b>\n\n"
            + "\n".join(participant_list))


def paid(update: Update, context: CallbackContext):
    sender_id = update.message.from_user.id
    try:
        amt_string, username = context.args
    except ValueError:
        context.bot.send_message(chat_id=update.effective_chat.id,
                                 text="Invalid format. Please type /paid [amount] [username]")
        return

    username = username[1:]

    try:
        amt = float(amt_string)
    except ValueError:
        context.bot.send_message(chat_id=update.effective_chat.id, text="Invalid amount, please try again.")
        return

    if amt == 0:
        context.bot.send_message(chat_id=update.effective_chat.id, text="Invalid amount (cannot be zero).")
        return
    elif amt < 0:
        context.bot.send_message(chat_id=update.effective_chat.id, text="Invalid amount (cannot be negative).")
        return

    new_id = context.chat_data["payments_id"]
    context.chat_data["payments_id"] += 1

    for _id in context.chat_data["registered"]:
        if update.effective_chat.get_member(_id).user.username == username:
            user_id = _id
            break
    else:
        context.bot.send_message(chat_id=update.effective_chat.id, text="Invalid username.")
        return

    if context.chat_data["debts"][sender_id][user_id] > 0:  # Sender owes user
        payer = sender_id
        payee = user_id
    else:
        payer = user_id
        payee = sender_id

    context.chat_data["debts"][payer][payee] -= amt
    context.chat_data["debts"][payee][payer] += amt

    balance = context.chat_data["debts"][payer][payee]

    context.chat_data["payments"][new_id] = {
        "payee": payee, "payer": payer, "amt": amt, "datetime": update.message.date, "balance": balance
    }

    payer = update.effective_chat.get_member(payer).user
    payee = update.effective_chat.get_member(payee).user

    logging.log(logging.INFO, f"Sender: {update.message.from_user.username}. Payee: {payee.username}. "
                              f"Payer: {payer.username}. Amt: {amt}")

    context.bot.send_message(chat_id=update.effective_chat.id,
                             text=get_payment_message(payer, payee, amt, balance),
                             reply_markup=get_delete_payment_markup(new_id),
                             parse_mode=ParseMode.HTML)


# Delete --> Confirm yes / no
def button_payment_delete(update: Update, context: CallbackContext):
    # TODO: Only allow payer and payee to delete
    # TODO: If too long ago, cannot delete
    query = update.callback_query
    query.answer()

    payment_id, payer, payee, amt, balance, date = get_payment_details(update, context)

    keyboard = [
        [InlineKeyboardButton("Yes", callback_data=DATA_PAYMENT_DELETE_YES + str(payment_id)),
         InlineKeyboardButton("No", callback_data=DATA_PAYMENT_DELETE_NO + str(payment_id))],
    ]
    markup = InlineKeyboardMarkup(keyboard)

    logging.log(logging.INFO, f"Pressed delete button: {query.from_user.username}, {query.from_user.id}")
    query.edit_message_text(text="<b>Confirm delete?</b>",
                            reply_markup=markup,
                            parse_mode=ParseMode.HTML)


def button_payment_delete_confirm(update: Update, context: CallbackContext):
    # TODO: If too long ago, cannot delete
    query = update.callback_query
    query.answer()

    payment_id, payer, payee, amt, balance, date = get_payment_details(update, context)

    del context.chat_data["payments"][payment_id]
    context.chat_data["debts"][payer.id][payee.id] += amt
    context.chat_data["debts"][payee.id][payer.id] -= amt

    logging.log(logging.INFO, f"Pressed delete button: {query.from_user.username}, {query.from_user.id}")
    query.edit_message_text(text=f"<s>{get_payment_message(payer, payee, amt, balance)}</s>\n\n"
                                 f"Deleted by {query.from_user.full_name} on {query.message.date}",
                            parse_mode=ParseMode.HTML)


def button_payment_delete_cancel(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()

    payment_id, payer, payee, amt, balance, date = get_payment_details(update, context)

    logging.log(logging.INFO, f"Pressed delete button: {query.from_user.username}, {query.from_user.id}")
    query.edit_message_text(text=get_payment_message(payer, payee, amt, balance),
                            reply_markup=get_delete_payment_markup(payment_id),
                            parse_mode=ParseMode.HTML)


def get_payment_details(update: Update, context: CallbackContext):
    query = update.callback_query
    payment_id = int(query.data[2:])
    payment = context.chat_data["payments"][payment_id]
    payer: User = update.effective_chat.get_member(payment["payer"]).user
    payee: User = update.effective_chat.get_member(payment["payee"]).user
    amt: float = payment["amt"]
    balance: float = payment["balance"]
    date: datetime.datetime = payment["datetime"]
    return payment_id, payer, payee, amt, balance, date


def get_payment_message(payer: User, payee: User, amt: float, balance: float):
    if -0.01 < balance < 0.01:
        message_string = "You're both settled now!"
    elif balance > 0:
        message_string = f"{payer.full_name} now owes <b>${fmt_amt(balance)}</b> to {payee.full_name}"
    else:  # balance < 0
        message_string = f"{payee.full_name} now owes <b>${fmt_amt(-balance)}</b> to {payer.full_name}"
    return (f"<b><u>Transaction</u></b>\n"
            f"Sender: <b>{payer.full_name}</b> (@{payer.username})\n"
            f"Recipient: <b>{payee.full_name}</b> (@{payee.username})\n"
            f"<b>${fmt_amt(amt)}</b>\n\n"
            f"{message_string}")


def fmt_amt(amt):
    return f"{amt:.0f}" if amt.is_integer() else f"{amt:.2f}"


def list_summary(update: Update, context: CallbackContext):
    users = sorted((update.effective_chat.get_member(user_id).user for user_id in context.chat_data["registered"]),
                   key=lambda _user: _user.full_name)
    all_settled = True
    message = ["<b><u>List of Outstanding Debts</u></b>"]
    for user1 in users:
        owes = queue.PriorityQueue()
        owed = queue.PriorityQueue()
        for user2, amt in context.chat_data["debts"][user1.id].items():
            if amt >= 0.01:  # user1 owes user2
                owes.put((amt, user2))
            elif amt <= -0.01:
                owed.put((amt, user2))
        if not owes.empty():
            message.append(f"\n<b>{user1.full_name} (@{user1.username})</b>")
        else:
            message.append(f"\n<b>{user1.full_name}</b>")
        if owes.empty() and owed.empty():
            message.append(f"• You're all settled!")
        while not owes.empty():
            amt, user2 = owes.get()
            message.append(f"• You owe <b>${fmt_amt(amt)}</b> to <b>{update.effective_chat.get_member(user2).user.full_name}</b>")
            all_settled = False
        while not owed.empty():
            amt, user2 = owed.get()
            if user2:
                message.append(f"• <b>{update.effective_chat.get_member(user2).user.full_name}</b> owes <b>${fmt_amt(-amt)}</b> to you")
            else:
                message.append(f"• An unclaimed amount of <b>${fmt_amt(amt)}</b> is owed to you")
            all_settled = False
    if all_settled:
        message = message[:1] + ["\nEveryone is all settled!"]
    context.bot.send_message(chat_id=update.effective_chat.id,
                             text="\n".join(message),
                             parse_mode=ParseMode.HTML)


dispatcher.add_handler(MessageHandler(Filters.reply, split_manually))

dispatcher.add_handler(MessageHandler(Filters.status_update.new_chat_members, new_member))
dispatcher.add_handler(MessageHandler(Filters.status_update.left_chat_member, left_member))

dispatcher.add_handler(CallbackQueryHandler(button_register, pattern=f"^{DATA_REGISTER}$"))

help_handler = CommandHandler('help', help_handler, filters=Filters.update.message)
dispatcher.add_handler(help_handler)

add_bill_handler = CommandHandler('bill', add_bill, filters=Filters.update.message)
dispatcher.add_handler(add_bill_handler)
dispatcher.add_handler(CallbackQueryHandler(button_bill_delete, pattern=f"^{DATA_BILL_DELETE}"))
dispatcher.add_handler(CallbackQueryHandler(button_bill_delete_confirm, pattern=f"^{DATA_BILL_DELETE_YES}"))
dispatcher.add_handler(CallbackQueryHandler(button_bill_redisplay, pattern=f"^{DATA_BILL_REDISPLAY}"))
dispatcher.add_handler(CallbackQueryHandler(button_bill_modify_participants, pattern=f"^{DATA_MODIFY_PARTICIPANTS}"))
dispatcher.add_handler(CallbackQueryHandler(button_bill_modify_participants_selected,
                                            pattern=f"^{DATA_MODIFY_PARTICIPANTS_SELECTED}"))
dispatcher.add_handler(CallbackQueryHandler(button_bill_split_manually, pattern=f"^{DATA_SPLIT_MANUALLY}"))
dispatcher.add_handler(CallbackQueryHandler(button_bill_split_equally, pattern=f"^{DATA_SPLIT_EQUALLY}"))
dispatcher.add_handler(CallbackQueryHandler(button_bill_change_payer, pattern=f"^{DATA_CHANGE_PAYER}"))
dispatcher.add_handler(CallbackQueryHandler(button_bill_choose_payer, pattern=f"^{DATA_CHANGE_PAYER_SELECTED}"))

paid_handler = CommandHandler('paid', paid, filters=Filters.update.message)
dispatcher.add_handler(paid_handler)
dispatcher.add_handler(CallbackQueryHandler(button_payment_delete, pattern=f"^{DATA_PAYMENT_DELETE}"))
dispatcher.add_handler(CallbackQueryHandler(button_payment_delete_confirm, pattern=f"^{DATA_PAYMENT_DELETE_YES}"))
dispatcher.add_handler(CallbackQueryHandler(button_payment_delete_cancel, pattern=f"^{DATA_PAYMENT_DELETE_NO}"))

list_handler = CommandHandler('list', list_summary, filters=Filters.update.message)
dispatcher.add_handler(list_handler)

updater.start_polling()
