import asyncio, html as html_mod, random, time
from aiogram import F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import (
    bot, dp, UNO_GAMES, DATA, save_data, is_owner, _try_delete_command,
    _is_peer_invalid, _notify_owner_bc_dead, _is_dead_bc,
)

UNO_COLORS = ["🔴", "🔵", "🟢", "🟡"]
COLOR_NAMES = {"🔴": "красный", "🔵": "синий", "🟢": "зелёный", "🟡": "жёлтый", "🌈": "wild"}
NUM_EMOJI = {0:"0️⃣",1:"1️⃣",2:"2️⃣",3:"3️⃣",4:"4️⃣",5:"5️⃣",6:"6️⃣",7:"7️⃣",8:"8️⃣",9:"9️⃣"}
LOBBY_TIMEOUT = 60
LOBBY_TICK = 10
DIFFICULTY_LABELS = {"easy": "🟢 Легко", "medium": "🟡 Средне", "hard": "🔴 Сложно"}


def _get_diff(uid):
    return DATA.get("user_settings", {}).get(str(uid), {}).get("uno_difficulty", "medium")


def _set_diff(uid, d):
    DATA.setdefault("user_settings", {}).setdefault(str(uid), {})["uno_difficulty"] = d
    save_data(DATA)


def _make_deck():
    deck = []
    for c in UNO_COLORS:
        deck.append({"color": c, "type": "num", "num": 0})
        for _ in range(2):
            for n in range(1, 10): deck.append({"color": c, "type": "num", "num": n})
            deck.append({"color": c, "type": "skip"})
            deck.append({"color": c, "type": "reverse"})
            deck.append({"color": c, "type": "draw2"})
    for _ in range(4):
        deck.append({"color": "🌈", "type": "wild"})
        deck.append({"color": "🌈", "type": "wild4"})
    random.shuffle(deck)
    return deck


def _card_label(card):
    if card["type"] == "num": return f"{card['color']} {NUM_EMOJI.get(card['num'], str(card['num']))}"
    if card["type"] == "skip": return f"{card['color']} 🚫"
    if card["type"] == "reverse": return f"{card['color']} 🔄"
    if card["type"] == "draw2": return f"{card['color']} +2"
    if card["type"] == "wild": return "🌈 Wild"
    if card["type"] == "wild4": return "🌈 +4"
    return "?"


def _can_play(card, top, cur_color):
    if card["color"] == "🌈": return True
    if card["color"] == cur_color: return True
    if card["type"] == "num" and top["type"] == "num" and card["num"] == top["num"]: return True
    if card["type"] != "num" and card["type"] == top["type"]: return True
    return False


def _player_status_text(game):
    lines = []
    for p in game["players"]:
        cnt = len(game["hands"].get(str(p["id"]), []))
        marker = "👈 " if game["players"][game["turn_idx"]]["id"] == p["id"] else ""
        bot_tag = " 🤖" if p.get("is_bot") else ""
        lines.append(f"{marker}{html_mod.escape(p['name'])}{bot_tag}: <b>{cnt}</b> карт")
    return "\n".join(lines)


def _group_text(game):
    top = game["top"]; cur = game.get("current_color", top["color"])
    turn_p = game["players"][game["turn_idx"]]
    return (f"🎴 <b>Уно</b>\n\n"
            f"🎯 Верхняя: {_card_label(top)}\n"
            f"🎨 Цвет: {cur} {COLOR_NAMES.get(cur, '')}\n\n"
            f"👥 Игроки:\n{_player_status_text(game)}\n\n"
            f"⏭ Ход: <b>{html_mod.escape(turn_p['name'])}</b>\n\n"
            f"👆 Своя колода — в ЛС у бота.")


def _group_kb():
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="🎴 Взять карту", callback_data="uno_group_draw"),
        InlineKeyboardButton(text="📢 Уно!", callback_data="uno_shout"),
    )
    b.row(InlineKeyboardButton(text="🏳️ Отмена", callback_data="uno_surrender"))
    return b.as_markup()


def _dm_text(game, uid):
    hand = game["hands"].get(str(uid), [])
    top = game["top"]; cur = game.get("current_color", top["color"])
    is_turn = game["players"][game["turn_idx"]]["id"] == uid
    txt = (f"🃏 <b>Твои карты ({len(hand)}):</b>\n"
           f"Верхняя: {_card_label(top)}\nЦвет: {cur} {COLOR_NAMES.get(cur, '')}\n\n")
    txt += "✅ <b>Твой ход!</b>\n" if is_turn else f"⏳ Ждём: {html_mod.escape(game['players'][game['turn_idx']]['name'])}\n"
    txt += "\n"
    for i, c in enumerate(hand):
        mark = "" if _can_play(c, top, cur) else "·"
        txt += f"{i+1}. {mark}{_card_label(c)}\n"
    return txt


def _dm_kb(game, uid):
    hand = game["hands"].get(str(uid), [])
    top = game["top"]; cur = game.get("current_color", top["color"])
    is_turn = game["players"][game["turn_idx"]]["id"] == uid
    b = InlineKeyboardBuilder(); row = []
    for i, c in enumerate(hand):
        cb = f"uno_play:{i}" if (_can_play(c, top, cur) and is_turn) else "uno_noop"
        row.append(InlineKeyboardButton(text=_card_label(c), callback_data=cb))
        if len(row) == 4: b.row(*row); row = []
    if row: b.row(*row)
    if is_turn: b.row(InlineKeyboardButton(text="🃏 Взять карту", callback_data="uno_dm_draw"))
    return b.as_markup()


async def _send_dm(uid, game):
    try:
        await bot.send_message(uid, _dm_text(game, uid), reply_markup=_dm_kb(game, uid), parse_mode="HTML")
    except Exception as e:
        print(f"[uno dm {uid}] {str(e)[:150]}")


async def _refresh_group(game, chat_id):
    bc_id = game.get("bc_id")
    text = _group_text(game); kb = _group_kb()
    msg_id = game.get("group_msg_id")
    if bc_id and not _is_dead_bc(bc_id):
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text,
                                         reply_markup=kb, parse_mode="HTML", business_connection_id=bc_id)
            return
        except Exception as e:
            if _is_peer_invalid(e): await _notify_owner_bc_dead(bc_id); return
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text,
                                     reply_markup=kb, parse_mode="HTML")
    except Exception:
        try:
            m = await bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")
            game["group_msg_id"] = m.message_id
        except Exception: pass


def _start_round(game):
    deck = _make_deck()
    game["hands"] = {str(p["id"]): [deck.pop() for _ in range(7)] for p in game["players"]}
    game["deck"] = deck
    while True:
        first = deck.pop()
        if first["type"] == "num": break
        deck.insert(0, first)
    game["top"] = first
    game["current_color"] = first["color"]
    game["turn_idx"] = 0
    game["direction"] = 1
    game["started"] = True
    game["finished"] = False


# ============ ЛОББИ ============
def _lobby_text(game):
    players = game["players"]
    lines = ["🎴 <b>Набор в Уно</b>\n"]
    if players:
        lines.append(f"👥 Игроки ({len(players)}): " + ", ".join(html_mod.escape(p["name"]) for p in players))
    else:
        lines.append("👥 Пока никого.")
    if game.get("no_timer"):
        lines.append("\n⏸ Таймер отключён. Админ: <code>/play</code>")
    else:
        lines.append(f"\n⏱ Старт через <b>{game.get('time_left', LOBBY_TIMEOUT)}</b> сек.")
    lines.append("\nМинимум 2 игрока.")
    return "\n".join(lines)


def _lobby_kb():
    b = InlineKeyboardBuilder()
    b.button(text="🙋 Присоединиться", callback_data="uno_join")
    return b.as_markup()


async def _lobby_updater(chat_id):
    while True:
        await asyncio.sleep(LOBBY_TICK)
        game = UNO_GAMES.get(chat_id)
        if not game or game.get("started"): return
        if game.get("no_timer"): continue
        game["time_left"] = max(0, game.get("time_left", LOBBY_TIMEOUT) - LOBBY_TICK)
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=game["lobby_msg_id"],
                                         text=_lobby_text(game), reply_markup=_lobby_kb(), parse_mode="HTML")
        except Exception: pass
        if game["time_left"] <= 0:
            try: await bot.send_message(chat_id, "⏱ Время вышло. Жду <code>/play</code>.", parse_mode="HTML")
            except Exception: pass
            return


@dp.message(F.text.func(lambda t: t and t.strip().lower().startswith("/uno")))
async def cmd_uno(message: Message):
    await _try_delete_command(message)
    if message.chat.type == "private":
        await _uno_ls_menu(message); return
    chat_id = message.chat.id
    if chat_id in UNO_GAMES and UNO_GAMES[chat_id].get("started"):
        await message.answer("⚠️ Игра уже идёт."); return
    args = (message.text or "").split()
    no_timer = "notimer" in [a.lower() for a in args[1:]]
    user = message.from_user
    name = user.full_name or (f"@{user.username}" if user.username else "Игрок")
    UNO_GAMES[chat_id] = {"mode": "group", "players": [{"id": user.id, "name": name}],
                          "bc_id": message.business_connection_id, "no_timer": no_timer,
                          "time_left": LOBBY_TIMEOUT, "started": False}
    game = UNO_GAMES[chat_id]
    msg = await message.answer(_lobby_text(game), reply_markup=_lobby_kb(), parse_mode="HTML")
    game["lobby_msg_id"] = msg.message_id
    if not no_timer: asyncio.create_task(_lobby_updater(chat_id))


@dp.callback_query(F.data == "uno_join")
async def cb_join(cb: CallbackQuery):
    chat_id = cb.message.chat.id
    game = UNO_GAMES.get(chat_id)
    if not game or game.get("started"): await cb.answer("Игра уже идёт", show_alert=True); return
    uid = cb.from_user.id
    if any(p["id"] == uid for p in game["players"]): await cb.answer("Ты уже в игре", show_alert=True); return
    u = cb.from_user; name = u.full_name or (f"@{u.username}" if u.username else "Игрок")
    game["players"].append({"id": uid, "name": name})
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=game["lobby_msg_id"],
                                     text=_lobby_text(game), reply_markup=_lobby_kb(), parse_mode="HTML")
    except Exception: pass
    await cb.answer("Зашёл!")


@dp.message(F.text == "/play")
async def cmd_play(message: Message):
    await _try_delete_command(message)
    if not is_owner(message.from_user): await message.answer("Только владелец."); return
    chat_id = message.chat.id
    game = UNO_GAMES.get(chat_id)
    if not game or game.get("started"): await message.answer("Нет лобби."); return
    if len(game["players"]) < 2: await message.answer("⚠️ Нужно ≥2."); return
    await _start_group(chat_id)


# ============ ЛС ============
async def _uno_ls_menu(message: Message):
    uid = message.from_user.id
    d = _get_diff(uid)
    b = InlineKeyboardBuilder()
    for k, label in DIFFICULTY_LABELS.items():
        mark = "✅ " if k == d else ""
        b.button(text=f"{mark}{label}", callback_data=f"uno_diff:{k}")
    b.row(InlineKeyboardButton(text="🚀 Начать игру", callback_data="uno_start_ls"))
    b.adjust(1)
    await message.answer(
        f"🎴 <b>Уно против бота</b>\n\nСложность: <b>{DIFFICULTY_LABELS[d]}</b>\n",
        reply_markup=b.as_markup(), parse_mode="HTML")


@dp.callback_query(F.data.startswith("uno_diff:"))
async def cb_diff(cb: CallbackQuery):
    d = cb.data.split(":", 1)[1]
    if d not in DIFFICULTY_LABELS: await cb.answer("Ошибка", show_alert=True); return
    _set_diff(cb.from_user.id, d)
    b = InlineKeyboardBuilder()
    for k, label in DIFFICULTY_LABELS.items():
        mark = "✅ " if k == d else ""
        b.button(text=f"{mark}{label}", callback_data=f"uno_diff:{k}")
    b.row(InlineKeyboardButton(text="🚀 Начать игру", callback_data="uno_start_ls"))
    b.adjust(1)
    try:
        await cb.message.edit_text(f"🎴 <b>Уно против бота</b>\n\nСложность: <b>{DIFFICULTY_LABELS[d]}</b>",
                                    reply_markup=b.as_markup(), parse_mode="HTML")
    except Exception: pass
    await cb.answer(f"{DIFFICULTY_LABELS[d]}")


@dp.callback_query(F.data == "uno_start_ls")
async def cb_start_ls(cb: CallbackQuery):
    chat_id = cb.message.chat.id
    if chat_id in UNO_GAMES: await cb.answer("Уже идёт", show_alert=True); return
    uid = cb.from_user.id
    name = cb.from_user.full_name or "Ты"
    d = _get_diff(uid)
    game = {"mode": "ls",
            "players": [{"id": uid, "name": name}, {"id": 0, "name": "🤖 Бот", "is_bot": True, "difficulty": d}],
            "hands": {}, "deck": [], "top": None, "current_color": None,
            "turn_idx": 0, "direction": 1, "started": True, "finished": False,
            "chat_id": chat_id, "bc_id": None, "player_uid": uid}
    _start_round(game)
    UNO_GAMES[chat_id] = game
    try: await cb.message.delete()
    except Exception: pass
    await _send_dm(uid, game)
    await cb.answer("Погнали!")


# ============ СТАРТ ГРУППЫ ============
async def _start_group(chat_id):
    game = UNO_GAMES.get(chat_id)
    if not game: return
    _start_round(game)
    try: await bot.edit_message_text(chat_id=chat_id, message_id=game["lobby_msg_id"],
                                      text="🎴 <b>Уно началось!</b>", parse_mode="HTML")
    except Exception: pass
    for p in game["players"]:
        if p.get("is_bot"): continue
        try: await bot.send_message(p["id"], f"🎴 Игра началась! Твоя колода:")
        except Exception:
            try: await bot.send_message(chat_id, f"⚠️ @{p['name']} — напиши боту в ЛС.")
            except Exception: pass
    for p in game["players"]:
        if p.get("is_bot"): continue
        await _send_dm(p["id"], game)
    await _refresh_group(game, chat_id)


# ============ ХОДЫ ============
async def _apply_and_next(game, chat_id, uid, card):
    n = len(game["players"])
    if card["type"] == "skip":
        game["turn_idx"] = (game["turn_idx"] + 2 * game["direction"]) % n
    elif card["type"] == "reverse":
        game["direction"] *= -1
        game["turn_idx"] = (game["turn_idx"] + game["direction"]) % n
    elif card["type"] in ("draw2", "wild4"):
        cnt = 2 if card["type"] == "draw2" else 4
        nxt = (game["turn_idx"] + game["direction"]) % n
        nxt_id = game["players"][nxt]["id"]
        for _ in range(cnt):
            if game["deck"]: game["hands"][str(nxt_id)].append(game["deck"].pop())
        game["turn_idx"] = nxt
    else:
        game["turn_idx"] = (game["turn_idx"] + game["direction"]) % n

    if not game["hands"].get(str(uid)):
        winner = next((p["name"] for p in game["players"] if p["id"] == uid), "?")
        try: await bot.send_message(chat_id, f"🏆 <b>{html_mod.escape(winner)} выиграл!</b>", parse_mode="HTML")
        except Exception: pass
        UNO_GAMES.pop(chat_id, None)
        return

    if game["mode"] == "group":
        await _refresh_group(game, chat_id)
    for p in game["players"]:
        if p.get("is_bot"): continue
        await _send_dm(p["id"], game)

    cur = game["players"][game["turn_idx"]]
    if cur.get("is_bot"):
        await asyncio.sleep(1.5)
        await _bot_move(game, chat_id)


async def _bot_move(game, chat_id):
    if game.get("finished"): return
    bp = game["players"][game["turn_idx"]]
    if not bp.get("is_bot"): return
    hand = game["hands"].get("0", [])
    top = game["top"]; cur = game["current_color"]; diff = bp.get("difficulty", "medium")
    playable = [(i, c) for i, c in enumerate(hand) if _can_play(c, top, cur)]
    if not playable:
        if game["deck"]: hand.append(game["deck"].pop())
        game["turn_idx"] = (game["turn_idx"] + game["direction"]) % len(game["players"])
        try: await bot.send_message(game["chat_id"], "🤖 Бот взял карту.")
        except Exception: pass
        for p in game["players"]:
            if p.get("is_bot"): continue
            await _send_dm(p["id"], game)
        return
    if diff == "easy":
        pick = random.choice(playable)
    elif diff == "medium":
        normals = [(i, c) for i, c in playable if c["color"] != "🌈"]
        pick = random.choice(normals if normals else playable)
    else:
        cc = {}
        for c in hand:
            if c["color"] != "🌈": cc[c["color"]] = cc.get(c["color"], 0) + 1
        score = []
        for i, c in playable:
            s = 100 if c["type"] == "wild4" else 80 if c["type"] == "draw2" else 60 if c["type"] == "skip" else 50 if c["type"] == "reverse" else 40 if c["type"] == "wild" else 10 + cc.get(c["color"], 0)
            score.append((s, i, c))
        score.sort(reverse=True)
        pick = (score[0][1], score[0][2])
    idx, card = pick
    hand.pop(idx); game["top"] = card
    if card["color"] == "🌈":
        cc = {}
        for c in hand:
            if c["color"] != "🌈": cc[c["color"]] = cc.get(c["color"], 0) + 1
        game["current_color"] = max(cc, key=cc.get) if (diff == "hard" and cc) else random.choice(UNO_COLORS)
    else: game["current_color"] = card["color"]
    try:
        sfx = f" → {game['current_color']}" if card["color"] == "🌈" else ""
        await bot.send_message(game["chat_id"], f"🤖 Бот сбросил {_card_label(card)}{sfx}")
    except Exception: pass
    await _apply_and_next(game, chat_id, 0, card)


@dp.callback_query(F.data.startswith("uno_play:"))
async def cb_play(cb: CallbackQuery):
    chat_id = cb.message.chat.id
    game = UNO_GAMES.get(chat_id)
    if not game: await cb.answer("Нет игры", show_alert=True); return
    try: idx = int(cb.data.split(":", 1)[1])
    except: await cb.answer("Ошибка", show_alert=True); return
    uid = cb.from_user.id
    if game["players"][game["turn_idx"]]["id"] != uid:
        await cb.answer("Не твой ход", show_alert=True); return
    hand = game["hands"].get(str(uid), [])
    if idx < 0 or idx >= len(hand): await cb.answer("Нет карты", show_alert=True); return
    card = hand[idx]; top = game["top"]; cur = game["current_color"]
    if not _can_play(card, top, cur): await cb.answer("Нельзя", show_alert=True); return
    hand.pop(idx); game["top"] = card
    if card["color"] == "🌈":
        b = InlineKeyboardBuilder()
        for c in UNO_COLORS: b.button(text=c, callback_data=f"uno_color:{c}")
        b.adjust(4)
        await cb.message.answer("🎨 Выбери цвет:", reply_markup=b.as_markup())
        game["pending_uid"] = uid; game["pending_card"] = card
        await cb.answer()
        return
    game["current_color"] = card["color"]
    await _apply_and_next(game, chat_id, uid, card)
    await cb.answer()


@dp.callback_query(F.data.startswith("uno_color:"))
async def cb_color(cb: CallbackQuery):
    color = cb.data.split(":", 1)[1]
    uid = cb.from_user.id
    target = next(((cid, g) for cid, g in UNO_GAMES.items() if g.get("pending_uid") == uid), None)
    if not target: await cb.answer("Неактуально", show_alert=True); return
    chat_id, game = target
    game["current_color"] = color
    card = game.pop("pending_card"); game.pop("pending_uid", None)
    try: await cb.message.edit_text(f"🎨 {color}")
    except Exception: pass
    await _apply_and_next(game, chat_id, uid, card)
    await cb.answer(color)


@dp.callback_query(F.data == "uno_dm_draw")
async def cb_dm_draw(cb: CallbackQuery):
    uid = cb.from_user.id
    target = next(((cid, g) for cid, g in UNO_GAMES.items()
                   if (g["mode"] == "ls" and g.get("player_uid") == uid) or
                      (g["mode"] == "group" and uid in [p["id"] for p in g["players"]])), None)
    if not target: await cb.answer("Нет игры", show_alert=True); return
    chat_id, game = target
    if game["players"][game["turn_idx"]]["id"] != uid: await cb.answer("Не твой ход", show_alert=True); return
    if not game["deck"]: await cb.answer("Колода пуста", show_alert=True); return
    card = game["deck"].pop(); game["hands"][str(uid)].append(card)
    game["turn_idx"] = (game["turn_idx"] + game["direction"]) % len(game["players"])
    await cb.answer(f"Взял {_card_label(card)}")
    try: await bot.send_message(chat_id, "🎴 Игрок взял карту.")
    except Exception: pass
    for p in game["players"]:
        if p.get("is_bot"): continue
        await _send_dm(p["id"], game)
    cur = game["players"][game["turn_idx"]]
    if cur.get("is_bot"):
        await asyncio.sleep(1.5); await _bot_move(game, chat_id)


@dp.callback_query(F.data == "uno_group_draw")
async def cb_group_draw(cb: CallbackQuery):
    chat_id = cb.message.chat.id
    game = UNO_GAMES.get(chat_id)
    if not game: await cb.answer("Нет игры", show_alert=True); return
    uid = cb.from_user.id
    if game["players"][game["turn_idx"]]["id"] != uid: await cb.answer("Не твой ход", show_alert=True); return
    if not game["deck"]: await cb.answer("Колода пуста", show_alert=True); return
    card = game["deck"].pop(); game["hands"][str(uid)].append(card)
    game["turn_idx"] = (game["turn_idx"] + game["direction"]) % len(game["players"])
    await cb.answer(f"Взял {_card_label(card)}")
    try: await bot.send_message(uid, f"🃏 Взял: {_card_label(card)}")
    except Exception: pass
    for p in game["players"]:
        if p.get("is_bot"): continue
        await _send_dm(p["id"], game)
    await _refresh_group(game, chat_id)


@dp.callback_query(F.data == "uno_shout")
async def cb_shout(cb: CallbackQuery):
    chat_id = cb.message.chat.id
    game = UNO_GAMES.get(chat_id)
    if not game: await cb.answer("Нет игры", show_alert=True); return
    uid = cb.from_user.id
    hand = game["hands"].get(str(uid), [])
    if len(hand) == 1:
        name = next((p["name"] for p in game["players"] if p["id"] == uid), "?")
        try: await bot.send_message(chat_id, f"📣 <b>{html_mod.escape(name)}</b> крикнул УНО!", parse_mode="HTML")
        except Exception: pass
        await cb.answer("📣")
    elif len(hand) > 1: await cb.answer("Уно только с 1 картой!", show_alert=True)
    else: await cb.answer("Ты уже выиграл", show_alert=True)


@dp.callback_query(F.data == "uno_surrender")
async def cb_surrender(cb: CallbackQuery):
    chat_id = cb.message.chat.id
    if chat_id in UNO_GAMES:
        UNO_GAMES.pop(chat_id, None)
        try: await bot.send_message(chat_id, "🏳️ Отменено.")
        except Exception: pass
    await cb.answer()


@dp.callback_query(F.data == "uno_noop")
async def cb_noop(cb: CallbackQuery): await cb.answer()
