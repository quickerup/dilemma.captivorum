import os
import logging
import asyncio
import random
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode

# --- CONFIG ---
load_dotenv()
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GIF_CHANNEL_ID = os.getenv('GIF_CHANNEL_ID')
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

ENTRY_FEE = 0.5

GIF_MESSAGE_IDS = {
    "both_split":  [3, 2],
    "you_stole":   [5, 7],
    "you_got_got": [4, 6],
    "both_steal":  [8, 9, 10],
}

PHASE_SIGNALING = "signaling"
PHASE_DECIDING  = "deciding"
PHASE_DOUBLING  = "doubling"

SIGNAL_LABELS = {
    "peace":  "🕊️  Peace",
    "threat": "⚡  Threaten",
    "silent": "🤫  Stay Silent",
}
SIGNAL_EMOJI = {
    "peace":  "🕊️",
    "threat": "⚡",
    "silent": "🤫",
}

class GameEngine:
    def __init__(self):
        self.waiting_player = None
        self.active_games   = {}
        self.user_to_game   = {}

    def get_game(self, user_id):
        gid = self.user_to_game.get(user_id)
        return (gid, self.active_games.get(gid)) if gid else (None, None)

    def matchmake(self, user_id, name, chat_id):
        if user_id in self.user_to_game:
            return "already_in_game"
        if self.waiting_player and self.waiting_player['id'] == user_id:
            return "in_queue"
        if not self.waiting_player:
            self.waiting_player = {'id': user_id, 'name': name, 'chat_id': chat_id}
            return "queued"
        p1 = self.waiting_player
        p2 = {'id': user_id, 'name': name, 'chat_id': chat_id}
        self.waiting_player = None
        game_id = f"{p1['id']}_{p2['id']}"
        self.active_games[game_id] = {
            'players':      {p1['id']: p1, p2['id']: p2},
            'phase':        PHASE_SIGNALING,
            'signals':      {},
            'choices':      {},
            'double_votes': {},
            'round':        1,
            'multiplier':   1.0,
        }
        self.user_to_game[p1['id']] = game_id
        self.user_to_game[p2['id']] = game_id
        return {"status": "created", "game_id": game_id}

    def reset_round(self, game_id):
        game = self.active_games[game_id]
        game['phase']         = PHASE_SIGNALING
        game['signals']       = {}
        game['choices']       = {}
        game['double_votes']  = {}
        game['round']        += 1
        game['multiplier']   *= 2

    def cleanup_game(self, game_id):
        game = self.active_games.pop(game_id, None)
        if game:
            for pid in game['players']:
                self.user_to_game.pop(pid, None)
        return game

engine = GameEngine()
gif_cache = {}

async def cache_gifs(app):
    logging.info("Caching GIFs from channel...")
    for outcome, msg_ids in GIF_MESSAGE_IDS.items():
        file_ids = []
        for msg_id in msg_ids:
            try:
                msg = await app.bot.forward_message(
                    chat_id=GIF_CHANNEL_ID,
                    from_chat_id=GIF_CHANNEL_ID,
                    message_id=msg_id,
                )
                if msg.animation:
                    file_ids.append(msg.animation.file_id)
                await msg.delete()
            except Exception as e:
                logging.warning(f"Could not cache GIF msg_id={msg_id}: {e}")
        gif_cache[outcome] = file_ids
        logging.info(f"  {outcome}: {len(file_ids)} GIFs cached")
    logging.info("GIF cache ready.")

def get_gif(outcome):
    ids = gif_cache.get(outcome, [])
    return random.choice(ids) if ids else None

def signal_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🕊️  Peace",      callback_data="signal:peace")],
        [InlineKeyboardButton("⚡  Threaten",    callback_data="signal:threat")],
        [InlineKeyboardButton("🤫  Stay Silent", callback_data="signal:silent")],
    ])

def choice_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤝  SPLIT", callback_data="choice:split")],
        [InlineKeyboardButton("🔪  STEAL", callback_data="choice:steal")],
    ])

def double_keyboard(next_stake):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔥  Double Down  ({next_stake:.2f} TON)", callback_data="double:yes")],
        [InlineKeyboardButton("💰  Cash Out",                              callback_data="double:no")],
    ])

def play_again_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎮  Find New Opponent", callback_data="find_opponent")],
    ])

WELCOME = (
    "💀 <b>THE VAULT</b>\n\n"
    "Two players. One pot. No mercy.\n\n"
    f"Each round costs <b>{ENTRY_FEE} TON</b>.\n"
    "Make your choice — <b>Split</b> or <b>Steal</b>.\n\n"
    "› <b>Both Split</b> → share the pot equally\n"
    "› <b>One Steals</b> → thief takes everything\n"
    "› <b>Both Steal</b> → the house wins it all\n\n"
    "Before you decide, you send one signal.\n"
    "It means nothing. Or everything.\n\n"
    "Press <b>Find Opponent</b> when you're ready."
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎮  Find Opponent", callback_data="find_opponent")]
    ])
    await update.message.reply_text(WELCOME, parse_mode=ParseMode.HTML, reply_markup=kb)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    user    = query.from_user
    user_id = str(user.id)
    data    = query.data

    if data in ("find_opponent", "play_again"):
        result = engine.matchmake(user_id, user.first_name, query.message.chat_id)
        if result == "already_in_game":
            await query.message.reply_text("⚠️ You're already in a game.")
        elif result == "in_queue":
            await query.message.reply_text("⌛ You're already searching.")
        elif result == "queued":
            await query.message.edit_text(
                "🔍 <b>Searching for an opponent...</b>\n\nYou're in the queue. Sit tight.",
                parse_mode=ParseMode.HTML
            )
        else:
            await start_signaling_phase(result['game_id'], context, query)
        return

    if data.startswith("signal:"):
        signal_key = data.split(":")[1]
        game_id, game = engine.get_game(user_id)
        if not game or game['phase'] != PHASE_SIGNALING:
            await query.answer("Not available right now.", show_alert=True)
            return
        if user_id in game['signals']:
            await query.answer("Already sent.", show_alert=True)
            return
        game['signals'][user_id] = signal_key
        await query.message.edit_text(
            f"Signal locked: <b>{SIGNAL_EMOJI[signal_key]}</b>\n\nWaiting for your opponent...",
            parse_mode=ParseMode.HTML
        )
        if len(game['signals']) == 2:
            await advance_to_decision(game_id, context)
        return

    if data.startswith("choice:"):
        choice = data.split(":")[1]
        game_id, game = engine.get_game(user_id)
        if not game or game['phase'] != PHASE_DECIDING:
            await query.answer("Not available right now.", show_alert=True)
            return
        if user_id in game['choices']:
            await query.answer("Already locked in.", show_alert=True)
            return
        game['choices'][user_id] = choice
        await query.message.edit_text(
            "🔒 <b>Locked in.</b>\n\nWaiting for your opponent to decide...",
            parse_mode=ParseMode.HTML
        )
        if len(game['choices']) == 2:
            await resolve_game(game_id, context)
        return

    if data.startswith("double:"):
        vote = data.split(":")[1]
        game_id, game = engine.get_game(user_id)
        if not game or game['phase'] != PHASE_DOUBLING:
            await query.answer("Not available right now.", show_alert=True)
            return
        if user_id in game['double_votes']:
            await query.answer("Already voted.", show_alert=True)
            return
        game['double_votes'][user_id] = vote
        await query.message.edit_reply_markup(reply_markup=None)
        if vote == "yes":
            await query.message.reply_text(
                "🔥 <b>You're in.</b>\n\nWaiting to see if your opponent doubles down...",
                parse_mode=ParseMode.HTML
            )
        else:
            await query.message.reply_text(
                "💰 <b>Cashing out.</b>\n\nWaiting for result...",
                parse_mode=ParseMode.HTML
            )
        if len(game['double_votes']) == 2:
            await resolve_double(game_id, context)
        return

async def start_signaling_phase(game_id, context, triggering_query=None):
    game  = engine.active_games[game_id]
    p_ids = list(game['players'].keys())
    rnd   = game['round']
    stake = ENTRY_FEE * game['multiplier']

    for i, pid in enumerate(p_ids):
        opponent = game['players'][p_ids[1 - i]]
        text = (
            f"⚡ <b>MATCH FOUND</b>  —  Round {rnd}\n"
            f"Opponent: <b>{opponent['name']}</b>\n"
            f"Stakes: <b>{stake:.2f} TON</b>\n\n"
            "─────────────────\n\n"
            "<b>PHASE 1 — SIGNAL</b>\n\n"
            "Send your opponent a message before the real choice.\n"
            "They'll see it. Make of it what you will."
        )
        if triggering_query and str(triggering_query.from_user.id) == pid:
            await triggering_query.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=signal_keyboard())
        else:
            await context.bot.send_message(
                chat_id=game['players'][pid]['chat_id'],
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=signal_keyboard(),
            )

async def advance_to_decision(game_id, context):
    game  = engine.active_games[game_id]
    game['phase'] = PHASE_DECIDING
    p_ids = list(game['players'].keys())
    await asyncio.sleep(0.6)
    for i, pid in enumerate(p_ids):
        opp_id         = p_ids[1 - i]
        opp_signal_key = game['signals'].get(opp_id, "silent")
        opp_label      = SIGNAL_LABELS[opp_signal_key]
        stake          = ENTRY_FEE * game['multiplier']
        await context.bot.send_message(
            chat_id=game['players'][pid]['chat_id'],
            text=(
                "─────────────────\n"
                "<b>PHASE 2 — THE DECISION</b>\n\n"
                f"Your opponent signaled: <b>{opp_label}</b>\n\n"
                f"Pot: <b>{stake:.2f} TON</b>\n\n"
                "Now. <b>Split or Steal?</b>\n"
                "Your opponent is deciding right now."
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=choice_keyboard(),
        )

async def resolve_game(game_id, context):
    game  = engine.active_games.get(game_id)
    if not game:
        return
    p_ids = list(game['players'].keys())
    both_split = all(game['choices'][pid] == "split" for pid in p_ids)
    await asyncio.sleep(1.2)
    for i, pid in enumerate(p_ids):
        opp_id     = p_ids[1 - i]
        my_choice  = game['choices'][pid]
        opp_choice = game['choices'][opp_id]
        opp_name   = game['players'][opp_id]['name']
        my_signal  = game['signals'].get(pid, "silent")
        opp_signal = game['signals'].get(opp_id, "silent")
        stake      = ENTRY_FEE * game['multiplier']
        gif_key, caption, _ = outcome_details(my_choice, opp_choice, opp_name, my_signal, opp_signal, stake)
        gif_id = get_gif(gif_key)
        kb = double_keyboard(stake * 2) if both_split else play_again_keyboard()
        if gif_id:
            await context.bot.send_animation(
                chat_id=game['players'][pid]['chat_id'],
                animation=gif_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
        else:
            await context.bot.send_message(
                chat_id=game['players'][pid]['chat_id'],
                text=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
    if both_split:
        game['phase'] = PHASE_DOUBLING
    else:
        engine.cleanup_game(game_id)

async def resolve_double(game_id, context):
    game = engine.active_games.get(game_id)
    if not game:
        return
    p_ids  = list(game['players'].keys())
    votes  = game['double_votes']
    all_in = all(v == "yes" for v in votes.values())
    await asyncio.sleep(0.8)
    if all_in:
        engine.reset_round(game_id)
        new_stake = ENTRY_FEE * game['multiplier']
        for pid in p_ids:
            await context.bot.send_message(
                chat_id=game['players'][pid]['chat_id'],
                text=(
                    f"🔥🔥 <b>BOTH IN. Round {game['round']}.</b>\n\n"
                    f"Stakes: <b>{new_stake:.2f} TON</b>\n\n"
                    "The pot just doubled. Don't blow it."
                ),
                parse_mode=ParseMode.HTML,
            )
            await asyncio.sleep(0.4)
        await start_signaling_phase(game_id, context, triggering_query=None)
    else:
        engine.cleanup_game(game_id)
        no_voters = [pid for pid, v in votes.items() if v == "no"]
        for pid in p_ids:
            if pid in no_voters:
                msg = "💰 <b>You cashed out.</b>\n\nYour points are locked. Smart... or scared?"
            else:
                opp_name = game['players'][[p for p in p_ids if p != pid][0]]['name']
                msg = f"😤 <b>{opp_name} cashed out.</b>\n\nThey walked. Your streak is over."
            await context.bot.send_message(
                chat_id=game['players'][pid]['chat_id'],
                text=msg,
                parse_mode=ParseMode.HTML,
                reply_markup=play_again_keyboard(),
            )

def outcome_details(my_choice, opp_choice, opp_name, my_signal, opp_signal, stake):
    my_e  = "🤝" if my_choice == "split" else "🔪"
    opp_e = "🤝" if opp_choice == "split" else "🔪"
    my_s  = SIGNAL_EMOJI[my_signal]
    opp_s = SIGNAL_EMOJI[opp_signal]
    sig   = f"You {my_s} → {my_e}     They {opp_s} → {opp_e}"

    if my_choice == "split" and opp_choice == "split":
        return "both_split", (
            "━━━━━━━━━━━━━━━━━━━\n"
            "🏆 <b>YOU BOTH SPLIT</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            f"{sig}\n\n"
            f"Pot shared. <b>+{stake:.2f} TON each.</b>\n\n"
            "<i>Rare. Beautiful. Suspicious.</i>\n\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "Want to press your luck?"
        ), True

    elif my_choice == "steal" and opp_choice == "steal":
        return "both_steal", (
            "━━━━━━━━━━━━━━━━━━━\n"
            "💀 <b>BOTH STOLE</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            f"{sig}\n\n"
            f"The house eats. <b>+0 TON. Nobody wins.</b>\n\n"
            "<i>Greed consumed you both.</i>\n"
            "━━━━━━━━━━━━━━━━━━━"
        ), False

    elif my_choice == "steal" and opp_choice == "split":
        return "you_stole", (
            "━━━━━━━━━━━━━━━━━━━\n"
            "😈 <b>YOU STOLE</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            f"{sig}\n\n"
            f"<b>{opp_name}</b> trusted you. You took everything.\n"
            f"<b>+{stake * 2:.2f} TON</b>\n\n"
            "<i>Cold.</i>\n"
            "━━━━━━━━━━━━━━━━━━━"
        ), False

    else:
        return "you_got_got", (
            "━���━━━━━━━━━━━━━━━━━\n"
            "🩸 <b>YOU WERE ROBBED</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            f"{sig}\n\n"
            f"<b>{opp_name}</b> stole while you split.\n"
            "<b>+0 TON. You lose everything.</b>\n\n"
            "<i>You trusted. They didn't.</i>\n"
            "━━━━━━━━━━━━━━━━━━━"
        ), False

def main():
    app = (
        Application.builder()
        .token(TOKEN)
        .post_init(cache_gifs)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.run_polling()

if __name__ == "__main__":
    main()
