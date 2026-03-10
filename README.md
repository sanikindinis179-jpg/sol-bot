# 🤖 Solana Signal Bot

Отслеживает кошельки и шлёт сигналы в Telegram:
- 🟢 First Buy — когда кошелёк впервые покупает токен
- 🔴 Sell — когда кошелёк продаёт
- 📈 Рост MC — когда MC вырос на 50%+

---

## 🚀 Деплой на Railway (10 минут)

### 1. Загрузите код на GitHub
1. Зайдите на github.com → New repository
2. Назовите `sol-signal-bot` → Create
3. Загрузите все 3 файла: bot.py, requirements.txt, railway.toml

### 2. Задеплойте на Railway
1. Зайдите на railway.app
2. New Project → Deploy from GitHub repo
3. Выберите `sol-signal-bot`
4. Railway сам запустит бота!

### 3. Запустите бота
1. Найдите вашего бота в Telegram
2. Напишите /start
3. Готово — сигналы пойдут!

---

## ➕ Добавить кошельки
В bot.py найдите раздел WALLETS и добавьте адреса:
```python
WALLETS = [
    "D8n8Dy6DWC9691mR4NroSA9TdxXBxDV6Rr639RapanS4",
    "36A6mEN5rYJdVTb6fMqVvG6ez8g2mTYdr1omWcQ1kDKG",
    "3BLjRcxWGtR7WRshJ3hL25U3RjWr5Ud98wMcczQqk4Ei",
]
```

## ⚙️ Настройка фильтров
```python
FILTERS = {
    "min_sol_amount": 0.05,   # мин. сумма покупки
    "min_mc": 1000,           # мин. MC в USD
    "max_mc": 500000,         # макс. MC в USD
    "mc_growth_alert": 50,    # алерт при росте MC на 50%
}
```
