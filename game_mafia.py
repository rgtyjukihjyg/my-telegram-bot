import asyncio, html as html_mod, random, time
from aiogram import F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import (
    bot, dp, MAFIA_GAMES, is_owner, _try_delete_command,
    _is_peer_invalid, _notify_owner_bc_dead, _is_dead_bc,
)

LOBBY_TIMEOUT = 60
LOBBY_TICK = 10
MIN_PLAYERS = 4
NIGHT_TIMEOUT = 60
VOTE_TIMEOUT = 60

ROLES = {
    "mafia": "🔫 Мафия",
    "doctor": "💊 Доктор",
    "detective": "🔍 Комиссар",
    "civilian": "👤 Мирный",
}


def _lobby_text(game):
    players = game["players"]
    lines = ["🔫 <b>Набор в Мафию</b>\n"]
    if players:
        names = ", ".join(html_mod.escape(p["name"]) for p in players)
        lines.append(f"👥 Игроки ({len(players)}): {names}")
    else:
        lines.append("👥 Пока никого.")
    if len(players) < MIN_PLAYERS:
        lines.append(f"\n⚠️ Нужно минимум {MIN_PLAYERS} чел. (сейчас {len(players)})")
    if game.get("no_timer"):
        lines.append("\n⏸ Таймер отключён. Админ: <code>/play</code>")
    else:
        tl = game.get("time_left", LOBBY_TIMEOUT)
        lines.append(f"\n⏱ Старт через <b>{tl}</b> сек.")
    lines.append("\n<i>Каждый игрок получит свою роль в ЛС.</i>")
    return "\n".join(lines)


def _lobby_kb(game):
    b = InlineKeyboardBuilder()
    b.button(text="🙋 Присоединиться", callback_data="mafia_join")
    return b.as_markup()


async def _lobby_updater(chat_id):
    while True:
        await asyncio.sleep(LOBBY_TICK)
        game = MAFIA_GAMES.get(chat_id)
        if not game or game.get("state") != "lobby": return
        if game.get("no_timer"): continue
        game["time_left"] = max(0, game.get("time_left", LOBBY_TIMEOUT) - LOBBY_TICK)
        bc_id = game.get("bc_id")
        try:
            if bc_id and not _is_dead_bc(bc_id):
                await bot.edit_message_text(chat_id=chat_id, message_id=game["lobby_msg_id"],
                                             text=_lobby_text(game), reply_markup=_lobby_kb(game),
                                             parse_mode="HTML", business_connection_id=bc_id)
            else:
                await bot.edit_message_text(chat_id=chat_id, message_id=game["lobby_msg_id"],
                                             text=_lobby_text(game), reply_markup=_lobby_kb(game),
                                             parse_mode="HTML")
        except Exception: pass
        if game["time_left"] <= 0:
            try:
                if bc_id and not _is_dead_bc(bc_id):
                    await bot.send_message(chat_id,
                        "⏱ Время вышло. Жду <code>/play</code> от владельца.",
                        parse_mode="HTML", business_connection_id=bc_id)
                else:
                    await bot.send_message(chat_id,
                        "⏱ Время вышло. Жду <code>/play</code> от владельца.",
                        parse_mode="HTML")
            except Exception: pass
            return


@dp.message(F.text.func(lambda t: t and t.strip().lower().startswith("/mafia")))
async def cmd_mafia(message: Message):
    await _try_delete_command(message)
    if message.chat.type == "private":
        await message.answer("🔫 Мафия играется только в группах. Добавь бота в чат."); return
    chat_id = message.chat.id
    if chat_id in MAFIA_GAMES and MAFIA_GAMES[chat_id].get("state") != "lobby":
        await message.answer("⚠️ Игра уже идёт."); return
    args = (message.text or "").split()
    no_timer = "notimer" in [a.lower() for a in args[1:]]
    user = message.from_user
    name = user.full_name or (f"@{user.username}" if user.username else "Игрок")
    MAFIA_GAMES[chat_id] = {
        "players": [{"id": user.id, "name": name, "alive": True, "role": None}],
        "bc_id": message.business_connection_id,
        "no_timer": no_timer, "time_left": LOBBY_TIMEOUT,
        "state": "lobby", "day": 0,
    }
    game = MAFIA_GAMES[chat_id]
    msg = await message.answer(_lobby_text(game), reply_markup=_lobby_kb(game), parse_mode="HTML")
    game["lobby_msg_id"] = msg.message_id
    if not no_timer:
        asyncio.create_task(_lobby_updater(chat_id))


@dp.callback_query(F.data == "mafia_join")
async def cb_mafia_join(cb: CallbackQuery):
    chat_id = cb.message.chat.id
    game = MAFIA_GAMES.get(chat_id)
    if not game or game.get("state") != "lobby":
        await cb.answer("Лобби закрыто", show_alert=True); return
    uid = cb.from_user.id
    if any(p["id"] == uid for p in game["players"]):
        await cb.answer("Ты уже в игре", show_alert=True); return
    if len(game["players"]) >= 10:
        await cb.answer("Максимум 10 игроков", show_alert=True); return
    u = cb.from_user; name = u.full_name or (f"@{u.username}" if u.username else "Игрок")
    game["players"].append({"id": uid, "name": name, "alive": True, "role": None})
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=game["lobby_msg_id"],
                                     text=_lobby_text(game), reply_markup=_lobby_kb(game),
                                     parse_mode="HTML")
    except Exception: pass
    await cb.answer("Зашёл!")


@dp.message(F.text == "/play")
async def cmd_play(message: Message):
    await _try_delete_command(message)
    if not is_owner(message.from_user):
        await message.answer("Только владелец может стартовать."); return
    chat_id = message.chat.id
    game = MAFIA_GAMES.get(chat_id)
    if game and game.get("state") == "lobby":
        if len(game["players"]) < MIN_PLAYERS:
            await message.answer(f"⚠️ Нужно минимум {MIN_PLAYERS} игрока."); return
        await _start_mafia(chat_id)
        return
    # если есть УНО-лобби
    from bot import UNO_GAMES
    ug = UNO_GAMES.get(chat_id)
    if ug and not ug.get("started"):
        import game_uno
        if len(ug["players"]) < 2:
            await message.answer("⚠️ Нужно ≥2 игрока."); return
        await game_uno._start_group(chat_id)
        return
    await message.answer("Нет активного лобби.")


def _assign_roles(players):
    n = len(players)
    # классика: 1/4 мафия (мин 1), 1 доктор, 1 комиссар, остальные мирные
    mafia_count = max(1, n // 4)
    roles = ["mafia"] * mafia_count + ["doctor", "detective"] + ["civilian"] * (n - mafia_count - 2)
    random.shuffle(roles)
    for p, r in zip(players, roles): p["role"] = r


async def _start_mafia(chat_id):
    game = MAFIA_GAMES.get(chat_id)
    if not game: return
    game["state"] = "starting"
    game["day"] = 1
    _assign_roles(game["players"])

    bc_id = game.get("bc_id")
    try:
        if bc_id and not _is_dead_bc(bc_id):
            await bot.edit_message_text(chat_id=chat_id, message_id=game["lobby_msg_id"],
                                         text="🔫 <b>Мафия началась!</b>", parse_mode="HTML",
                                         business_connection_id=bc_id)
        else:
            await bot.edit_message_text(chat_id=chat_id, message_id=game["lobby_msg_id"],
                                         text="🔫 <b>Мафия началась!</b>", parse_mode="HTML")
    except Exception: pass

    # рассылаем роли в ЛС
    for p in game["players"]:
        try:
            await bot.send_message(p["id"],
                f"🔫 <b>Твоя роль:</b> {ROLES[p['role']]}\n\n"
                f"Игра началась! Ночью будут действия, днём — голосование.",
                parse_mode="HTML")
        except Exception:
            try:
                if bc_id and not _is_dead_bc(bc_id):
                    await bot.send_message(chat_id,
                        f"⚠️ @{p['name']} — напиши боту в ЛС.",
                        business_connection_id=bc_id)
            except Exception: pass

    # первая ночь
    await asyncio.sleep(2)
    await _start_night(chat_id)


async def _start_night(chat_id):
    game = MAFIA_GAMES.get(chat_id)
    if not game or game.get("state") in ("finished", "cancelled"): return
    game["state"] = "night"
    game["night_actions"] = {"mafia": None, "doctor": None, "detective": None}
    bc_id = game.get("bc_id")

    alive = [p for p in game["players"] if p["alive"]]
    alive_list = "\n".join(f"• {html_mod.escape(p['name'])}" for p in alive)
    text = (f"🌙 <b>НОЧЬ {game['day']}</b>\n\n"
            f"Живые:\n{alive_list}\n\n"
            f"Все спят. Мафия, доктор и комиссар — проверьте ЛС.\n"
            f"<i>Через {NIGHT_TIMEOUT} сек начнётся день.</i>")

    try:
        if bc_id and not _is_dead_bc(bc_id):
            await bot.send_message(chat_id, text, parse_mode="HTML", business_connection_id=bc_id)
        else:
            await bot.send_message(chat_id, text, parse_mode="HTML")
    except Exception: pass

    # отправляем выборы в ЛС
    for p in alive:
        if p["role"] == "mafia":
            await _send_mafia_kb(chat_id, p)
        elif p["role"] == "doctor":
            await _send_doctor_kb(chat_id, p)
        elif p["role"] == "detective":
            await _send_detective_kb(chat_id, p)

    asyncio.create_task(_night_countdown(chat_id))


async def _send_mafia_kb(chat_id, player):
    game = MAFIA_GAMES.get(chat_id)
    others = [p for p in game["players"] if p["alive"] and p["role"] != "mafia"]
    b = InlineKeyboardBuilder()
    for p in others:
        b.button(text=html_mod.escape(p["name"]), callback_data=f"mafia_kill:{p['id']}")
    b.adjust(2)
    try:
        await bot.send_message(player["id"],
            f"🔫 <b>Ты мафия.</b> Выбери жертву:",
            reply_markup=b.as_markup(), parse_mode="HTML")
    except Exception: pass


async def _send_doctor_kb(chat_id, player):
    game = MAFIA_GAMES.get(chat_id)
    alive = [p for p in game["players"] if p["alive"]]
    b = InlineKeyboardBuilder()
    for p in alive:
        b.button(text=html_mod.escape(p["name"]), callback_data=f"mafia_save:{p['id']}")
    b.adjust(2)
    try:
        await bot.send_message(player["id"],
            f"💊 <b>Ты доктор.</b> Кого спасти?",
            reply_markup=b.as_markup(), parse_mode="HTML")
    except Exception: pass


async def _send_detective_kb(chat_id, player):
    game = MAFIA_GAMES.get(chat_id)
    others = [p for p in game["players"] if p["alive"] and p["id"] != player["id"]]
    b = InlineKeyboardBuilder()
    for p in others:
        b.button(text=html_mod.escape(p["name"]), callback_data=f"mafia_check:{p['id']}")
    b.adjust(2)
    try:
        await bot.send_message(player["id"],
            f"🔍 <b>Ты комиссар.</b> Кого проверить?",
            reply_markup=b.as_markup(), parse_mode="HTML")
    except Exception: pass


async def _night_countdown(chat_id):
    for _ in range(NIGHT_TIMEOUT // 5):
        await asyncio.sleep(5)
        game = MAFIA_GAMES.get(chat_id)
        if not game or game.get("state") != "night": return
    await _end_night(chat_id)


@dp.callback_query(F.data.startswith("mafia_kill:"))
async def cb_kill(cb: CallbackQuery):
    uid = cb.from_user.id
    game = None
    for cid, g in MAFIA_GAMES.items():
        if g.get("state") == "night":
            for p in g["players"]:
                if p["id"] == uid and p["role"] == "mafia": game = (cid, g); break
    if not game: await cb.answer("Не твоя кнопка", show_alert=True); return
    cid, game = game
    target = int(cb.data.split(":", 1)[1])
    game["night_actions"]["mafia"] = target
    await cb.answer("Выбрано")
    try: await cb.message.edit_text(f"🔫 Жертва: {html_mod.escape(next(p['name'] for p in game['players'] if p['id']==target))}")
    except Exception: pass


@dp.callback_query(F.data.startswith("mafia_save:"))
async def cb_save(cb: CallbackQuery):
    uid = cb.from_user.id
    game = None
    for cid, g in MAFIA_GAMES.items():
        if g.get("state") == "night":
            for p in g["players"]:
                if p["id"] == uid and p["role"] == "doctor": game = (cid, g); break
    if not game: await cb.answer("Не твоя кнопка", show_alert=True); return
    cid, game = game
    target = int(cb.data.split(":", 1)[1])
    game["night_actions"]["doctor"] = target
    await cb.answer("Спасаешь")


@dp.callback_query(F.data.startswith("mafia_check:"))
async def cb_check(cb: CallbackQuery):
    uid = cb.from_user.id
    game = None
    for cid, g in MAFIA_GAMES.items():
        if g.get("state") == "night":
            for p in g["players"]:
                if p["id"] == uid and p["role"] == "detective": game = (cid, g); break
    if not game: await cb.answer("Не твоя кнопка", show_alert=True); return
    cid, game = game
    target = int(cb.data.split(":", 1)[1])
    target_p = next((p for p in game["players"] if p["id"] == target), None)
    if not target_p: await cb.answer("Не найден", show_alert=True); return
    is_mafia = target_p["role"] == "mafia"
    game["night_actions"]["detective"] = target
    await cb.answer(f"{'🔫 МАФИЯ!' if is_mafia else '👤 Мирный'}", show_alert=True)


async def _end_night(chat_id):
    game = MAFIA_GAMES.get(chat_id)
    if not game or game.get("state") != "night": return
    acts = game["night_actions"]
    victim_id = acts.get("mafia")
    saved_id = acts.get("doctor")

    killed_name = None
    if victim_id and victim_id != saved_id:
        victim = next((p for p in game["players"] if p["id"] == victim_id), None)
        if victim and victim["alive"]:
            victim["alive"] = False
            killed_name = victim["name"]

    # оповещение
    bc_id = game.get("bc_id")
    if killed_name:
        text = f"☀️ <b>ДЕНЬ {game['day']}</b>\n\n💀 Этой ночью погиб <b>{html_mod.escape(killed_name)}</b>."
    else:
        text = f"☀️ <b>ДЕНЬ {game['day']}</b>\n\n🌅 Все выжили, никто не погиб."

    # проверка конца
    end, msg = _check_end(game)
    if end:
        game["state"] = "finished"
        text += f"\n\n🏁 {msg}"
        try:
            if bc_id and not _is_dead_bc(bc_id):
                await bot.send_message(chat_id, text, parse_mode="HTML", business_connection_id=bc_id)
            else:
                await bot.send_message(chat_id, text, parse_mode="HTML")
        except Exception: pass
        MAFIA_GAMES.pop(chat_id, None)
        return

    try:
        if bc_id and not _is_dead_bc(bc_id):
            await bot.send_message(chat_id, text, parse_mode="HTML", business_connection_id=bc_id)
        else:
            await bot.send_message(chat_id, text, parse_mode="HTML")
    except Exception: pass

    await asyncio.sleep(2)
    await _start_vote(chat_id)


def _check_end(game):
    alive = [p for p in game["players"] if p["alive"]]
    mafia = [p for p in alive if p["role"] == "mafia"]
    civs = [p for p in alive if p["role"] != "mafia"]
    if not mafia: return True, "👤 Победа мирных! Мафия мертва."
    if len(mafia) >= len(civs): return True, "🔫 Победа мафии!"
    return False, ""


async def _start_vote(chat_id):
    game = MAFIA_GAMES.get(chat_id)
    if not game or game.get("state") == "finished": return
    game["state"] = "vote"
    game["votes"] = {}
    alive = [p for p in game["players"] if p["alive"]]
    b = InlineKeyboardBuilder()
    for p in alive:
        b.button(text=html_mod.escape(p["name"]), callback_data=f"mafia_vote:{p['id']}")
    b.adjust(2)
    bc_id = game.get("bc_id")
    text = (f"🗳 <b>Голосование дня {game['day']}</b>\n\n"
            f"Кого посадить? Голосуйте кнопкой ниже.\n"
            f"<i>У вас {VOTE_TIMEOUT} сек.</i>")
    try:
        if bc_id and not _is_dead_bc(bc_id):
            await bot.send_message(chat_id, text, reply_markup=b.as_markup(),
                                    parse_mode="HTML", business_connection_id=bc_id)
        else:
            await bot.send_message(chat_id, text, reply_markup=b.as_markup(), parse_mode="HTML")
    except Exception: pass
    asyncio.create_task(_vote_countdown(chat_id))


async def _vote_countdown(chat_id):
    for _ in range(VOTE_TIMEOUT // 5):
        await asyncio.sleep(5)
        game = MAFIA_GAMES.get(chat_id)
        if not game or game.get("state") != "vote": return
    await _end_vote(chat_id)


@dp.callback_query(F.data.startswith("mafia_vote:"))
async def cb_vote(cb: CallbackQuery):
    chat_id = cb.message.chat.id
    game = MAFIA_GAMES.get(chat_id)
    if not game or game.get("state") != "vote": await cb.answer("Не голосование", show_alert=True); return
    uid = cb.from_user.id
    voter = next((p for p in game["players"] if p["id"] == uid and p["alive"]), None)
    if not voter: await cb.answer("Ты не игрок", show_alert=True); return
    target = int(cb.data.split(":", 1)[1])
    game["votes"][uid] = target
    await cb.answer("Голос принят")


async def _end_vote(chat_id):
    game = MAFIA_GAMES.get(chat_id)
    if not game or game.get("state") != "vote": return
    votes = game.get("votes", {})
    bc_id = game.get("bc_id")
    if not votes:
        text = "🗳 Никто не голосовал. День пропущен."
    else:
        counter = {}
        for t in votes.values(): counter[t] = counter.get(t, 0) + 1
        max_v = max(counter.values())
        top = [k for k, v in counter.items() if v == max_v]
        if len(top) > 1:
            text = "🗳 Ничья в голосовании. Никто не посажен."
        else:
            voted_id = top[0]
            voted_p = next((p for p in game["players"] if p["id"] == voted_id), None)
            if voted_p:
                voted_p["alive"] = False
                role = ROLES[voted_p["role"]]
                text = f"⚖️ <b>{html_mod.escape(voted_p['name'])}</b> посажен.\nОн был: {role}"
            else:
                text = "🗳 Ошибка голосования."

    end, msg = _check_end(game)
    if end:
        text += f"\n\n🏁 {msg}"
        try:
            if bc_id and not _is_dead_bc(bc_id):
                await bot.send_message(chat_id, text, parse_mode="HTML", business_connection_id=bc_id)
            else:
                await bot.send_message(chat_id, text, parse_mode="HTML")
        except Exception: pass
        MAFIA_GAMES.pop(chat_id, None)
        return

    try:
        if bc_id and not _is_dead_bc(bc_id):
            await bot.send_message(chat_id, text, parse_mode="HTML", business_connection_id=bc_id)
        else:
            await bot.send_message(chat_id, text, parse_mode="HTML")
    except Exception: pass
    await asyncio.sleep(2)
    game["day"] += 1
    await _start_night(chat_id)
