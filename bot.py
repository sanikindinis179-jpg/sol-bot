import asyncio
import aiohttp
import json
import time
from datetime import datetime

# ─── CONFIG ───────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = "8354864680:AAFXcfXMRZimnPrJbFgo2VQ8rV0U5DpiJXM"
HELIUS_API_KEY = "8be1bb0a-c2f9-42e1-ae80-fa1c4fd2f11d"
HELIUS_RPC     = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
HELIUS_API     = f"https://api.helius.xyz/v0"
TG_API         = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Кошельки для отслеживания
WALLETS = [
    "D8n8Dy6DWC9691mR4NroSA9TdxXBxDV6Rr639RapanS4",
    "36A6mEN5rYJdVTb6fMqVvG6ez8g2mTYdr1omWcQ1kDKG",
    "3BLjRcxWGtR7WRshJ3hL25U3RjWr5Ud98wMcczQqk4Ei",
]

# Фильтры сигналов
FILTERS = {
    "min_sol_amount":   0.05,    # минимальная сумма покупки в SOL
    "min_mc":           1000,    # минимальный MC в USD
    "max_mc":           500000,  # максимальный MC в USD
    "mc_growth_alert":  50,      # алерт при росте MC на X%
    "sell_threshold":   1,       # алерт если хотя бы 1 кошелёк продаёт
}

# ─── STATE ────────────────────────────────────────────────────────────────────
chat_ids        = set()          # кто запустил бота
seen_signatures = set()          # уже обработанные транзакции
token_mc_cache  = {}             # кеш MC для отслеживания роста
wallet_holdings = {}             # что держат кошельки {wallet: {mint: amount}}

# ─── TELEGRAM ────────────────────────────────────────────────────────────────
async def tg_send(session, chat_id, text):
    url = f"{TG_API}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    try:
        async with session.post(url, json=payload) as r:
            return await r.json()
    except Exception as e:
        print(f"[TG ERROR] {e}")

async def tg_broadcast(session, text):
    for cid in list(chat_ids):
        await tg_send(session, cid, text)

async def tg_poll(session):
    """Получаем новые сообщения (long polling)"""
    offset = 0
    while True:
        try:
            url = f"{TG_API}/getUpdates"
            params = {"timeout": 30, "offset": offset}
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=35)) as r:
                data = await r.json()
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                cid = msg.get("chat", {}).get("id")
                text = msg.get("text", "")
                if cid and text == "/start":
                    chat_ids.add(cid)
                    await tg_send(session, cid, 
                        "✅ <b>Бот запущен!</b>\n\n"
                        f"👛 Отслеживаю <b>{len(WALLETS)}</b> кошельков\n"
                        "📡 Сигналы: First Buy, Sell, Рост MC\n\n"
                        "Команды:\n"
                        "/status — статус бота\n"
                        "/wallets — список кошельков\n"
                        "/stop — остановить сигналы"
                    )
                elif cid and text == "/status":
                    await tg_send(session, cid,
                        f"📊 <b>Статус бота</b>\n\n"
                        f"👛 Кошельков: {len(WALLETS)}\n"
                        f"🔍 Обработано транзакций: {len(seen_signatures)}\n"
                        f"⏱ Время: {datetime.now().strftime('%H:%M:%S')}"
                    )
                elif cid and text == "/wallets":
                    wlist = "\n".join([f"• <code>{w[:8]}...{w[-4:]}</code>" for w in WALLETS])
                    await tg_send(session, cid, f"👛 <b>Отслеживаемые кошельки:</b>\n\n{wlist}")
                elif cid and text == "/stop":
                    chat_ids.discard(cid)
                    await tg_send(session, cid, "🔕 Сигналы остановлены. /start чтобы возобновить.")
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[POLL ERROR] {e}")
            await asyncio.sleep(5)

# ─── HELIUS API ───────────────────────────────────────────────────────────────
async def get_wallet_transactions(session, wallet, limit=10):
    """Получаем последние транзакции кошелька"""
    url = f"{HELIUS_API}/addresses/{wallet}/transactions"
    params = {"api-key": HELIUS_API_KEY, "limit": limit, "type": "SWAP"}
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status == 200:
                return await r.json()
    except Exception as e:
        print(f"[HELIUS TX ERROR] {e}")
    return []

async def get_token_metadata(session, mint):
    """Получаем метаданные токена"""
    url = f"{HELIUS_API}/token-metadata"
    params = {"api-key": HELIUS_API_KEY}
    payload = {"mintAccounts": [mint], "includeOffChain": True, "disableCache": False}
    try:
        async with session.post(url, params=params, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status == 200:
                data = await r.json()
                if data:
                    return data[0]
    except Exception as e:
        print(f"[META ERROR] {e}")
    return None

async def get_token_price(session, mint):
    """Получаем цену токена через Jupiter"""
    try:
        url = f"https://price.jup.ag/v6/price?ids={mint}"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 200:
                data = await r.json()
                price_data = data.get("data", {}).get(mint, {})
                return price_data.get("price", 0)
    except:
        pass
    return 0

# ─── SIGNAL LOGIC ─────────────────────────────────────────────────────────────
def parse_swap(tx, wallet):
    """Парсим своп транзакцию"""
    try:
        events = tx.get("events", {})
        swap = events.get("swap", {})
        if not swap:
            return None

        token_inputs  = swap.get("tokenInputs", [])
        token_outputs = swap.get("tokenOutputs", [])
        native_input  = swap.get("nativeInput", {})
        native_output = swap.get("nativeOutput", {})

        # Покупка: SOL → токен
        if native_input and token_outputs:
            sol_amount = native_input.get("amount", 0) / 1e9
            if sol_amount < FILTERS["min_sol_amount"]:
                return None
            mint = token_outputs[0].get("mint", "")
            token_amount = token_outputs[0].get("rawTokenAmount", {}).get("tokenAmount", 0)
            return {
                "type": "BUY",
                "wallet": wallet,
                "sol_amount": sol_amount,
                "mint": mint,
                "token_amount": float(token_amount) if token_amount else 0,
                "signature": tx.get("signature", ""),
                "timestamp": tx.get("timestamp", 0),
            }

        # Продажа: токен → SOL
        if token_inputs and native_output:
            sol_amount = native_output.get("amount", 0) / 1e9
            mint = token_inputs[0].get("mint", "")
            token_amount = token_inputs[0].get("rawTokenAmount", {}).get("tokenAmount", 0)
            return {
                "type": "SELL",
                "wallet": wallet,
                "sol_amount": sol_amount,
                "mint": mint,
                "token_amount": float(token_amount) if token_amount else 0,
                "signature": tx.get("signature", ""),
                "timestamp": tx.get("timestamp", 0),
            }
    except Exception as e:
        print(f"[PARSE ERROR] {e}")
    return None

def score_token(sol_amount, holders=0, age_minutes=0):
    """Простой скоринг токена 1-10"""
    score = 5.0
    if sol_amount >= 0.5:  score += 2
    elif sol_amount >= 0.2: score += 1
    if holders > 100: score += 1
    elif holders < 20: score -= 1
    if age_minutes < 30:  score += 1
    elif age_minutes > 240: score -= 1
    return round(min(10, max(1, score)), 1)

async def format_buy_signal(session, swap, meta):
    """Форматируем сигнал покупки"""
    w = swap["wallet"]
    short_wallet = f"{w[:6]}...{w[-4:]}"
    
    name   = "Unknown"
    symbol = "???"
    if meta:
        on_chain = meta.get("onChainMetadata", {}).get("metadata", {}).get("data", {})
        name   = on_chain.get("name", "Unknown")
        symbol = on_chain.get("symbol", "???")

    price  = await get_token_price(session, swap["mint"])
    mc_str = f"${price * 1e9:,.0f}" if price > 0 else "N/A"
    rating = score_token(swap["sol_amount"])
    stars  = "⭐" * int(rating / 2)

    short_mint = f"{swap['mint'][:6]}...{swap['mint'][-4:]}"
    sol_str    = f"{swap['sol_amount']:.3f}"
    time_str   = datetime.fromtimestamp(swap["timestamp"]).strftime("%H:%M:%S") if swap["timestamp"] else "?"

    return (
        f"🟢 <b>FIRST BUY — {symbol}</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👛 Кошелёк: <code>{short_wallet}</code>\n"
        f"🪙 Токен: <b>{name}</b> (<code>{short_mint}</code>)\n"
        f"💰 Куплено: <b>{sol_str} SOL</b>\n"
        f"📊 MC: {mc_str}\n"
        f"⭐ Рейтинг: <b>{rating}/10</b> {stars}\n"
        f"🕐 Время: {time_str}\n"
        f"🔗 <a href='https://solscan.io/tx/{swap['signature']}'>Транзакция</a> | "
        f"<a href='https://dexscreener.com/solana/{swap['mint']}'>Chart</a>"
    )

async def format_sell_signal(session, swap, meta):
    """Форматируем сигнал продажи"""
    w = swap["wallet"]
    short_wallet = f"{w[:6]}...{w[-4:]}"

    name   = "Unknown"
    symbol = "???"
    if meta:
        on_chain = meta.get("onChainMetadata", {}).get("metadata", {}).get("data", {})
        name   = on_chain.get("name", "Unknown")
        symbol = on_chain.get("symbol", "???")

    sol_str  = f"{swap['sol_amount']:.3f}"
    time_str = datetime.fromtimestamp(swap["timestamp"]).strftime("%H:%M:%S") if swap["timestamp"] else "?"

    return (
        f"🔴 <b>SELL — {symbol}</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👛 Кошелёк: <code>{short_wallet}</code>\n"
        f"🪙 Токен: <b>{name}</b>\n"
        f"💸 Продано за: <b>{sol_str} SOL</b>\n"
        f"⚠️ Кошелёк выходит!\n"
        f"🕐 Время: {time_str}\n"
        f"🔗 <a href='https://solscan.io/tx/{swap['signature']}'>Транзакция</a> | "
        f"<a href='https://dexscreener.com/solana/{swap['mint']}'>Chart</a>"
    )

# ─── MAIN LOOP ────────────────────────────────────────────────────────────────
async def monitor_wallet(session, wallet):
    """Мониторим один кошелёк"""
    txs = await get_wallet_transactions(session, wallet, limit=5)
    for tx in txs:
        sig = tx.get("signature", "")
        if not sig or sig in seen_signatures:
            continue
        seen_signatures.add(sig)

        swap = parse_swap(tx, wallet)
        if not swap:
            continue

        mint = swap["mint"]
        if not mint:
            continue

        print(f"[{swap['type']}] {wallet[:8]}... | {mint[:8]}... | {swap['sol_amount']:.3f} SOL")

        # Получаем метаданные
        meta = await get_token_metadata(session, mint)

        # Проверяем первая ли это покупка данного токена кошельком
        if swap["type"] == "BUY":
            holdings = wallet_holdings.setdefault(wallet, {})
            is_first_buy = mint not in holdings
            holdings[mint] = swap["token_amount"]

            if is_first_buy and chat_ids:
                msg = await format_buy_signal(session, swap, meta)
                await tg_broadcast(session, msg)

        elif swap["type"] == "SELL":
            # Убираем из holdings
            wallet_holdings.get(wallet, {}).pop(mint, None)
            if chat_ids:
                msg = await format_sell_signal(session, swap, meta)
                await tg_broadcast(session, msg)

        await asyncio.sleep(0.3)  # rate limit

async def check_mc_growth(session):
    """Проверяем рост MC для токенов в holdings"""
    all_mints = set()
    for holdings in wallet_holdings.values():
        all_mints.update(holdings.keys())

    for mint in list(all_mints):
        price = await get_token_price(session, mint)
        if price <= 0:
            continue
        mc = price * 1_000_000_000  # supply ~1B для pump.fun

        if mint in token_mc_cache:
            old_mc = token_mc_cache[mint]
            if old_mc > 0:
                growth = ((mc - old_mc) / old_mc) * 100
                if growth >= FILTERS["mc_growth_alert"] and chat_ids:
                    short_mint = f"{mint[:6]}...{mint[-4:]}"
                    msg = (
                        f"📈 <b>РОСТ MC +{growth:.0f}%</b>\n"
                        f"━━━━━━━━━━━━━━━\n"
                        f"🪙 Токен: <code>{short_mint}</code>\n"
                        f"📊 MC: ${old_mc:,.0f} → ${mc:,.0f}\n"
                        f"🚀 Рост за цикл: <b>+{growth:.1f}%</b>\n"
                        f"🔗 <a href='https://dexscreener.com/solana/{mint}'>Chart</a>"
                    )
                    await tg_broadcast(session, msg)

        token_mc_cache[mint] = mc
        await asyncio.sleep(0.2)

async def main_loop(session):
    """Основной цикл мониторинга"""
    print(f"[BOT] Запущен. Слежу за {len(WALLETS)} кошельками...")
    cycle = 0
    while True:
        try:
            # Мониторим все кошельки
            for wallet in WALLETS:
                await monitor_wallet(session, wallet)
                await asyncio.sleep(1)

            # Каждые 5 циклов проверяем рост MC
            cycle += 1
            if cycle % 5 == 0:
                await check_mc_growth(session)

            print(f"[CYCLE {cycle}] {datetime.now().strftime('%H:%M:%S')} | "
                  f"Seen: {len(seen_signatures)} txs | "
                  f"Chats: {len(chat_ids)}")

        except Exception as e:
            print(f"[LOOP ERROR] {e}")

        await asyncio.sleep(15)  # пауза между циклами

async def main():
    connector = aiohttp.TCPConnector(limit=20)
    timeout   = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        print("=" * 50)
        print("  SOLANA SIGNAL BOT")
        print(f"  Кошельков: {len(WALLETS)}")
        print(f"  Telegram: настроен")
        print(f"  Helius: настроен")
        print("=" * 50)
        print(f"\n👉 Откройте бота в Telegram и напишите /start\n")

        # Запускаем polling и мониторинг параллельно
        await asyncio.gather(
            tg_poll(session),
            main_loop(session),
        )

if __name__ == "__main__":
    asyncio.run(main())

