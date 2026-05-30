"""
Hyperliquid 日更 Bot — 群成员召唤版
支持命令：
  /revenue  — 推送 DefiLlama 24h 收入榜 TOP 7
  /top7     — 同上（别名）
  /start    — 欢迎说明
  /help     — 帮助
"""

import os
import logging
import requests
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

load_dotenv()
TG_TOKEN = os.environ["TG_TOKEN"]

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

API_DEFILLAMA = (
    "https://api.llama.fi/overview/fees"
    "?dataType=dailyRevenue"
    "&excludeTotalDataChart=true"
    "&excludeTotalDataChartBreakdown=true"
)

HEADERS = {
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    ),
    "Referer": "https://defillama.com/",
    "Origin": "https://defillama.com",
}


# ── 数据获取 ──────────────────────────────────────────────────
def fetch_top7() -> list[dict]:
    resp = requests.get(API_DEFILLAMA, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    protocols = resp.json().get("protocols", [])
    valid = [p for p in protocols if p.get("total24h") is not None]
    return sorted(valid, key=lambda x: x["total24h"], reverse=True)[:7]


def build_message(top7: list[dict]) -> str:
    icons = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣"]
    today = datetime.now()
    date_str = f"{today.year}年{today.month}月{today.day}日"

    lines = [
        "",
        "🏆  加密协议 24h 收入榜 TOP 7",
        f"📅  {date_str}",
        "――――――――――――――――――",
    ]
    for i, p in enumerate(top7):
        name = p.get("name", "Unknown")
        if "Hyperliquid" in name:
            name = "Hyperliquid"
        rev = p.get("total24h", 0)
        lines.append(f"{icons[i]}  {name.ljust(20)}~${rev:,.0f}")

    lines += [
        "――――――――――――――――――",
        "数据来源：DefiLlama",
    ]
    return "\n".join(lines)


# ── 命令处理 ──────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 我是 DeFi 收入播报 Bot！\n\n"
        "发送 /revenue 或 /top7 即可获取\n"
        "最新 DefiLlama 24h 协议收入 TOP 7 榜单。"
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 可用命令：\n\n"
        "/revenue — 获取 24h 协议收入 TOP 7\n"
        "/top7    — 同上（别名）\n"
        "/start   — 欢迎信息\n"
        "/help    — 显示本帮助"
    )


async def cmd_revenue(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # 先回一条「正在获取」避免用户等待无响应
    waiting = await update.message.reply_text("⏳ 正在获取最新数据，请稍候…")
    try:
        top7 = fetch_top7()
        msg  = build_message(top7)
        await waiting.delete()
        await update.message.reply_text(msg)
        logger.info(
            "榜单已推送 | chat_id=%s | user=%s",
            update.effective_chat.id,
            update.effective_user.username,
        )
    except Exception as e:
        await waiting.edit_text(f"❌ 获取数据失败：{e}")
        logger.exception("fetch_top7 error")


# ── 入口 ─────────────────────────────────────────────────────
def main():
    app = (
        ApplicationBuilder()
        .token(TG_TOKEN)
        .build()
    )

    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CommandHandler("revenue", cmd_revenue))
    app.add_handler(CommandHandler("top7",    cmd_revenue))   # 别名

    logger.info("Bot 启动，polling 中…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
