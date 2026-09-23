import html as html_mod
from aiogram import F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import (
    bot, dp, TTT_GAMES, is_owner, _try_delete_command,
    _is_peer_invalid, _notify_owner_bc_dead, _is_dead_bc,
)

TTT_WIN_LINES = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]


def _board_text(game):
    board = game["board"]
    x_name = game["names"].get("x") or "?"
    o_name = game["names"].get("o") or "ждём игрока..."
    def cell(i):
        v = board[i]
        return "❌" if v == "X" else ("⭕" if v == "O" else "▫️")
    grid = (f"{cell(0)} {cell(1)} {cell(2)}\n{cell(3)} {cell(4)} {cell(5)}\n{cell(6)} {cell(7)} {cell(8)}")
    if game.get("finished"):
        footer = game.get("footer", "")
        votes = game.get("rematch_votes", [])
        n1 = html_mod.escape(game["names"]["x"])
        n2 = html_mod.escape(game["names"].get("o") or "?")
        vx = "✅" if game["x_id"] in votes else "⏳"
        vo = "✅" if game.get("o_id") in votes else "⏳"
        footer += f"\n\n🔄 Реванш:\n{vx} {n1}\n{vo} {n2}"
    else:
        footer = f"Ход: {'❌' if game['turn'] == 'x' else '⭕'}"
    return (f"❌⭕ <b>Крестики-нолики</b> ❌⭕\n\n❌ {html_mod.escape(x_name)}\n"
            f"⭕ {html_mod.escape(o_name)}\n\n{grid}\n\n{footer}")


def _board_kb(game):
    board = game["board"]; b = InlineKeyboardBuilder()
    if not game.get("o_id") and not game.get("finished"):
        b.button(text="🙋 Присоединиться", callback_data="ttt_join")
        return b.as_markup()
    row = []
    for i in range(9):
        if board[i] is None and not game.get("finished"):
            row.append(InlineKeyboardButton(text="⬜", callback_data=f"ttt_move:{i}"))
        else:
            row.append(InlineKeyboardButton(
                text="❌" if board[i] == "X" else ("⭕" if board[i] == "O" else "⬜"),
                callback_data="ttt_noop"))
        if len(row) == 3: b.row(*row); row = []
    if game.get("finished"):
        b.row(InlineKeyboardButton(text="🔄 Реванш", callback_data="ttt_rematch"))
    else:
        b.row(InlineKeyboardButton(text="🏳️ Сдаться", callback_data="ttt_reset"))
    return b.as_markup()


def _check_winner(board):
    for a, b, c in TTT_WIN_LINES:
        if board[a] and board[a] == board[b] == board[c]: return board[a]
    if all(c is not None for c in board): return "draw"
    return None


async def _refresh(old_message, game):
    chat_id = old_message.chat.id
    bc_id = game.get("bc_id")
    text = _board_text(game); kb = _board_kb(game)
    if bc_id:
        if _is_dead_bc(bc_id): return
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=old_message.message_id,
                                         text=text, reply_markup=kb, parse_mode="HTML",
                                         business_connection_id=bc_id)
        except Exception as e:
            if _is_peer_invalid(e): await _notify_owner_bc_dead(bc_id)
    else:
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=old_message.message_id,
                                         text=text, reply_markup=kb, parse_mode="HTML")
        except Exception: pass


@dp.message(F.text.func(lambda t: t and t.strip().lower() == ".ttt"))
async def cmd_ttt(message: Message):
    await _try_delete_command(message)
    chat_id = message.chat.id
    if chat_id in TTT_GAMES and not TTT_GAMES[chat_id].get("finished"):
        await message.answer("⚠️ Игра уже идёт."); return
    user = message.from_user
    name = user.full_name or (f"@{user.username}" if user.username else "Игрок")
    TTT_GAMES[chat_id] = {"board": [None]*9, "x_id": user.id, "o_id": None, "turn": "x",
                          "names": {"x": name, "o": None}, "finished": False, "footer": "",
                          "rematch_votes": []}
    await message.answer(_board_text(TTT_GAMES[chat_id]),
                         reply_markup=_board_kb(TTT_GAMES[chat_id]), parse_mode="HTML")


@dp.callback_query(F.data == "ttt_join")
async def cb_join(cb: CallbackQuery):
    chat_id = cb.message.chat.id; game = TTT_GAMES.get(chat_id)
    if not game: await cb.answer("Не найдена", show_alert=True); return
    if game["finished"] or game.get("o_id"): await cb.answer("Место занято", show_alert=True); return
    if cb.from_user.id == game["x_id"]: await cb.answer("Ты уже ❌", show_alert=True); return
    u = cb.from_user; name = u.full_name or (f"@{u.username}" if u.username else "Игрок")
    game["o_id"] = u.id; game["names"]["o"] = name
    await _refresh(cb.message, game); await cb.answer("Погнали!")


@dp.callback_query(F.data.startswith("ttt_move:"))
async def cb_move(cb: CallbackQuery):
    chat_id = cb.message.chat.id; game = TTT_GAMES.get(chat_id)
    if not game or game["finished"]: await cb.answer("Недоступно", show_alert=True); return
    if not game.get("o_id"): await cb.answer("Ждём игрока", show_alert=True); return
    turn = game["turn"]
    expected = game["x_id"] if turn == "x" else game["o_id"]
    if cb.from_user.id != expected: await cb.answer("Не твой ход", show_alert=True); return
    try: idx = int(cb.data.split(":", 1)[1])
    except: await cb.answer("Ошибка", show_alert=True); return
    if idx < 0 or idx > 8 or game["board"][idx] is not None:
        await cb.answer("Занято", show_alert=True); return
    game["board"][idx] = "X" if turn == "x" else "O"
    w = _check_winner(game["board"])
    if w == "X":
        game["finished"] = True
        game["footer"] = f"🏆 Победа! ❌ {html_mod.escape(game['names']['x'])}"
        game["rematch_votes"] = []
    elif w == "O":
        game["finished"] = True
        game["footer"] = f"🏆 Победа! ⭕ {html_mod.escape(game['names']['o'])}"
        game["rematch_votes"] = []
    elif w == "draw":
        game["finished"] = True; game["footer"] = "🤝 Ничья!"; game["rematch_votes"] = []
    else:
        game["turn"] = "o" if turn == "x" else "x"
    await _refresh(cb.message, game); await cb.answer()


@dp.callback_query(F.data == "ttt_reset")
async def cb_reset(cb: CallbackQuery):
    chat_id = cb.message.chat.id; game = TTT_GAMES.get(chat_id)
    if not game: await cb.answer("Не найдена", show_alert=True); return
    if cb.from_user.id not in (game["x_id"], game.get("o_id")):
        await cb.answer("Только игроки", show_alert=True); return
    game["board"] = [None]*9; game["turn"] = "x"; game["finished"] = False
    game["footer"] = ""; game["rematch_votes"] = []
    await _refresh(cb.message, game); await cb.answer("Поле очищено")


@dp.callback_query(F.data == "ttt_rematch")
async def cb_rematch(cb: CallbackQuery):
    chat_id = cb.message.chat.id; game = TTT_GAMES.get(chat_id)
    if not game or not game.get("finished"):
        await cb.answer("Недоступно", show_alert=True); return
    uid = cb.from_user.id
    if uid not in (game["x_id"], game.get("o_id")):
        await cb.answer("Только игроки", show_alert=True); return
    votes = game.setdefault("rematch_votes", [])
    if uid not in votes: votes.append(uid)
    if game["x_id"] in votes and game.get("o_id") in votes:
        first = votes[0]
        game["board"] = [None]*9; game["finished"] = False
        game["footer"] = ""; game["rematch_votes"] = []
        game["turn"] = "x" if first == game["x_id"] else "o"
        await _refresh(cb.message, game); await cb.answer("Реванш!")
    else:
        await _refresh(cb.message, game); await cb.answer("Ждём второго")


@dp.callback_query(F.data == "ttt_noop")
async def cb_noop(cb: CallbackQuery): await cb.answer()
