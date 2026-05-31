import asyncio, json, logging, os, re
import httpx
import requests
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telegram.constants import ParseMode

load_dotenv()
logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TG_TOKEN   = os.environ["TG_TOKEN"]
NL_TOKEN   = os.environ["NEWSLIQUID_TOKEN"]
DS_KEY     = os.environ.get("DEEPSEEK_API_KEY", "")
NL_BASE    = "https://ai.6551.io"
NL_HEADERS = {"Authorization": f"Bearer {NL_TOKEN}", "Content-Type": "application/json"}
LIMIT      = 3

# 账号监控：存储 {chat_id: {username: asyncio.Task}}
watch_tasks: dict[int, dict[str, asyncio.Task]] = {}

# ── DefiLlama ────────────────────────────────────────────────
DEFILLAMA_API = (
    "https://api.llama.fi/overview/fees"
    "?dataType=dailyRevenue"
    "&excludeTotalDataChart=true"
    "&excludeTotalDataChartBreakdown=true"
)
DEFILLAMA_HEADERS = {
    "Accept": "*/*",
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://defillama.com/",
    "Origin": "https://defillama.com",
}

def fetch_top7() -> list[dict]:
    resp = requests.get(DEFILLAMA_API, headers=DEFILLAMA_HEADERS, timeout=60)
    resp.raise_for_status()
    protocols = resp.json().get("protocols", [])
    valid = [p for p in protocols if p.get("total24h") is not None]
    return sorted(valid, key=lambda x: x["total24h"], reverse=True)[:7]

def build_revenue_message(top7: list[dict]) -> str:
    icons = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣"]
    today = datetime.now()
    lines = [
        "",
        "🏆  加密协议 24h 收入榜 TOP 7",
        f"📅  {today.year}年{today.month}月{today.day}日",
        "――――――――――――――――――",
    ]
    for i, p in enumerate(top7):
        name = p.get("name", "Unknown")
        if "Hyperliquid" in name:
            name = "Hyperliquid"
        rev = p.get("total24h", 0)
        lines.append(f"{icons[i]}  {name.ljust(20)}~${rev:,.0f}")
    lines += ["――――――――――――――――――", "数据来源：DefiLlama"]
    return "\n".join(lines)

# ── NewsLiquid 工具函数 ───────────────────────────────────────
async def nl_post(endpoint: str, body: dict) -> dict:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{NL_BASE}{endpoint}", headers=NL_HEADERS, json=body)
        r.raise_for_status()
        return r.json()

async def nl_get(endpoint: str, params: dict = None) -> dict:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{NL_BASE}{endpoint}", headers=NL_HEADERS, params=params)
        r.raise_for_status()
        return r.json()

def clean(text: str) -> str:
    text = re.sub(r'<br\s*/?>', ' ', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'[\U00010000-\U0010ffff]', '', text, flags=re.UNICODE)
    text = re.sub(r'[\u2600-\u27BF]', '', text)
    return text.strip()

async def translate(texts: list[str]) -> list[str]:
    if not DS_KEY or not texts:
        return texts
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts))
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {DS_KEY}", "Content-Type": "application/json"},
                json={"model": "deepseek-chat", "max_tokens": 1000,
                      "messages": [{"role": "user", "content":
                          "将以下编号英文金融/加密新闻翻译成中文，保持编号，每行一条，不加解释：\n" + numbered}]}
            )
            r.raise_for_status()
            result = r.json()["choices"][0]["message"]["content"].strip()
        chunks = re.split(r'\n(?=\d+\.)', result.strip())
        lines = [re.sub(r"^\d+\.\s*", "", c.strip()).replace("\n", " ") for c in chunks if c.strip()]
        if len(lines) == len(texts):
            return lines
    except Exception as e:
        logging.warning(f"翻译失败: {e}")
    return texts

def fmt_news(item: dict, title: str = "") -> str:
    raw_ts = str(item.get("ts", ""))
    try:
        dt = datetime.fromisoformat(raw_ts.split("+")[0].split("Z")[0])
        ts_full = dt.strftime("%m/%d %H:%M")
    except Exception:
        ts_full = raw_ts[5:16]
    ai     = item.get("aiRating", {}) or {}
    score  = ai.get("score")
    source = item.get("newsType", "")
    coins  = [c["symbol"] for c in item.get("coins", [])
              if "-" not in c["symbol"] and not c["symbol"].startswith("XYZ")][:3]
    link   = item.get("link", "")
    t      = title if title else clean(item.get("text", ""))
    if len(t) > 60:
        t = re.sub(r'([。；]) *', r'\1\n', t).strip()
    t = t.rstrip("。.")
    coin_str  = "  ".join(coins) if coins else ""
    link_str  = f'<a href="{link}">阅读原文</a>' if link else ""
    row1_parts = [f"<i>{source}</i>"]
    if coin_str:
        row1_parts.append(coin_str)
    row1 = "  ·  ".join(row1_parts)
    row2_parts = [ts_full]
    if link_str:
        row2_parts.append(link_str)
    row2 = "  ·  ".join(row2_parts)
    return f"<b>{t}</b>\n\n{row1}\n{row2}"

async def fetch_and_format(body: dict, header: str = "") -> str:
    body["limit"] = 6
    data  = await nl_post("/open/news_search", body)
    raw   = data.get("data", [])
    items = raw if isinstance(raw, list) else raw.get("list", [])
    if not items:
        return header + "暂无新闻"
    seen, deduped = set(), []
    for it in items:
        key = clean(it.get("text", ""))[:20]
        if key not in seen:
            seen.add(key)
            deduped.append(it)
    deduped.sort(key=lambda x: x.get("ts", ""), reverse=True)
    items = deduped[:LIMIT]
    titles     = [clean(it.get("text", "")) for it in items]
    translated = await translate(titles)
    lines      = [fmt_news(it, translated[i]) for i, it in enumerate(items)]
    return header + "\n\n──────────\n\n".join(lines)

# ── 命令处理 ──────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 我是 DeFi 播报 Bot！\n\n"
        "/revenue 或 /top7 — 24h 协议收入榜 TOP 7\n"
        "/news [币种] — 最新新闻（如 /news HYPE）\n"
        "/hot [币种] [分数] — 重要新闻（如 /hot HYPE）\n"
        "/bloomberg — Bloomberg 机构新闻\n"
        "/reuters — Reuters 机构新闻\n"
        "/daily — 每日加密热点日报\n"
        "/watch @用户名 — 监控 Twitter 账号\n"
        "/unwatch @用户名 — 取消监控\n"
        "/watching — 查看监控列表\n"
        "/help — 帮助"
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 可用命令：\n\n"
        "/revenue — 24h 协议收入榜 TOP 7\n"
        "/top7    — 同上（别名）\n"
        "/news    — 最新新闻\n"
        "/news HYPE — HYPE 相关新闻\n"
        "/hot     — 高评分重要新闻（默认50分）\n"
        "/hot HYPE 80 — 指定币种和分数线\n"
        "/bloomberg — 只看 Bloomberg 新闻\n"
        "/reuters   — 只看 Reuters 新闻\n"
        "/daily   — 每日加密热点日报\n"
        "/watch @用户名 — 开始监控 Twitter 账号\n"
        "/unwatch @用户名 — 取消监控\n"
        "/watching — 查看当前监控列表\n"
        "/start   — 欢迎信息\n"
        "/help    — 显示本帮助"
    )

async def cmd_revenue(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    waiting = await update.message.reply_text("⏳ 正在获取最新数据，请稍候…")
    try:
        top7 = fetch_top7()
        await waiting.delete()
        await update.message.reply_text(build_revenue_message(top7))
    except Exception as e:
        await waiting.edit_text(f"❌ 获取数据失败：{e}")

async def cmd_news(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    coins = [a.upper() for a in (ctx.args or [])]
    body: dict = {"limit": LIMIT}
    if coins:
        body["coins"] = coins
    coin_label = f"（{'  '.join(coins)}）" if coins else ""
    waiting = await update.message.reply_text("⏳ 正在获取新闻…")
    try:
        text = await fetch_and_format(body, f"📰  最新动态{coin_label}\n\n")
        await waiting.delete()
        await update.message.reply_text(text, parse_mode=ParseMode.HTML,
                                        disable_web_page_preview=True)
    except Exception as e:
        await waiting.edit_text(f"❌ {e}")

async def cmd_hot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    score = 50
    coins = []
    for a in (ctx.args or []):
        if a.isdigit():
            score = int(a)
        else:
            coins.append(a.upper())
    body: dict = {"limit": LIMIT, "score": score}
    if coins:
        body["coins"] = coins
    coin_label = f"（{'  '.join(coins)}）" if coins else ""
    waiting = await update.message.reply_text("⏳ 正在获取新闻…")
    try:
        text = await fetch_and_format(body, f"🔥  评分 {score}+ 重要新闻{coin_label}\n\n")
        await waiting.delete()
        await update.message.reply_text(text, parse_mode=ParseMode.HTML,
                                        disable_web_page_preview=True)
    except Exception as e:
        await waiting.edit_text(f"❌ {e}")

# ── 机构媒体筛选 ──────────────────────────────────────────────
async def cmd_media(update: Update, ctx: ContextTypes.DEFAULT_TYPE, source_name: str, icon: str):
    waiting = await update.message.reply_text(f"⏳ 正在获取 {source_name} 新闻…")
    try:
        body = {
            "engineTypes": {"news": [source_name]},
            "limit": LIMIT,
        }
        text = await fetch_and_format(body, f"{icon}  {source_name} 最新报道\n\n")
        await waiting.delete()
        await update.message.reply_text(text, parse_mode=ParseMode.HTML,
                                        disable_web_page_preview=True)
    except Exception as e:
        await waiting.edit_text(f"❌ {e}")

async def cmd_bloomberg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_media(update, ctx, "Bloomberg", "📊")

async def cmd_reuters(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_media(update, ctx, "Reuters", "📡")

# ── 每日加密日报 ──────────────────────────────────────────────
async def cmd_daily(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    coins = [a.upper() for a in (ctx.args or [])]
    coin_label = f"（{'  '.join(coins)}）" if coins else ""
    waiting = await update.message.reply_text("⏳ 正在生成日报…")
    today = datetime.now()
    date_str = f"{today.year}年{today.month}月{today.day}日"
    try:
        body: dict = {"score": 50, "limit": 6}
        if coins:
            body["coins"] = coins
        data  = await nl_post("/open/news_search", body)
        raw   = data.get("data", [])
        items = raw if isinstance(raw, list) else raw.get("list", [])
        seen, deduped = set(), []
        for it in items:
            key = clean(it.get("text", ""))[:20]
            if key not in seen:
                seen.add(key)
                deduped.append(it)
        deduped.sort(key=lambda x: x.get("ts", ""), reverse=True)
        items = deduped[:5]
        titles     = [clean(it.get("text", "")) for it in items]
        translated = await translate(titles)
        lines = [f"☀️  加密日报{coin_label}  {date_str}", "――――――――――――――――――", ""]
        for i, it in enumerate(items):
            ai       = it.get("aiRating", {}) or {}
            score    = ai.get("score", "")
            signal   = ai.get("signal", "")
            source   = it.get("newsType", "")
            link     = it.get("link", "")
            coins_t  = [c["symbol"] for c in it.get("coins", [])
                        if "-" not in c["symbol"] and not c["symbol"].startswith("XYZ")][:3]
            sig_icon  = {"long": "▲", "short": "▼"}.get(signal, "")
            score_str = f" [{score}分 {sig_icon}]" if score else ""
            coin_str  = "  ".join(coins_t) if coins_t else ""
            link_str  = f'  <a href="{link}">原文</a>' if link else ""
            title     = translated[i].rstrip("。.")
            meta      = f"<i>{source}</i>"
            if coin_str:
                meta += f"  ·  {coin_str}"
            lines.append(f"• <b>{title}</b>{score_str}{link_str}")
            lines.append(f"  {meta}")
            lines.append("")
        lines.append("――――――――――――――――――")
        lines.append("数据来源：6551.io · DeepSeek 翻译")
        await waiting.delete()
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML,
                                        disable_web_page_preview=True)
    except Exception as e:
        await waiting.edit_text(f"❌ {e}")

# ── Twitter 账号监控 ──────────────────────────────────────────
async def _watch_loop(chat_id: int, username: str, app, interval: int = 120):
    """每 interval 秒轮询一次，有新推文就推送"""
    last_id = None
    # 先取最新5条推文的 ID 集合作为基准，全部跳过不推送
    seen_ids: set = set()
    try:
        d  = await nl_post("/open/twitter_user_tweets", {"username": username, "limit": 5})
        dd = d.get("data", {})
        tweets = dd.get("tweets", dd) if isinstance(dd, dict) else dd
        for t in tweets:
            tid = t.get("id") or t.get("id_str")
            if tid:
                seen_ids.add(str(tid))
        if tweets:
            last_id = tweets[0].get("id") or tweets[0].get("id_str")
    except Exception:
        pass

    while True:
        await asyncio.sleep(interval)
        try:
            d  = await nl_post("/open/twitter_user_tweets", {"username": username, "limit": 5})
            dd = d.get("data", {})
            tweets = dd.get("tweets", dd) if isinstance(dd, dict) else dd
            if not tweets:
                continue

            new_tweets = []
            for t in tweets:
                tid = str(t.get("id") or t.get("id_str") or "")
                if tid in seen_ids:
                    break
                new_tweets.append(t)

            if new_tweets:
                last_id = new_tweets[0].get("id") or new_tweets[0].get("id_str")
                for t in new_tweets:
                    tid = str(t.get("id") or t.get("id_str") or "")
                    if tid:
                        seen_ids.add(tid)
                for t in reversed(new_tweets):
                    raw_text = clean(t.get("text", ""))
                    translated_list = await translate([raw_text])
                    zh_text  = translated_list[0]
                    url      = t.get("url", "")
                    like     = t.get("like_count", t.get("favoriteCount", 0))
                    rt       = t.get("retweet_count", t.get("retweetCount", 0))
                    url_str  = f'<a href="{url}">查看原推 ↗</a>' if url else ""
                    # 中英对照：原文 + 译文，只有内容不同才显示双语
                    if zh_text.strip() != raw_text.strip():
                        body_text = f"{raw_text}\n\n──\n{zh_text}"
                    else:
                        body_text = raw_text
                    link_line = f"\n{url_str}" if url_str else ""
                    msg = (
                        f"🔔  <b>@{username}</b> 发推了\n\n"
                        f"{body_text}"
                        f"{link_line}"
                    )
                    await app.bot.send_message(chat_id, msg,
                                               parse_mode=ParseMode.HTML,
                                               disable_web_page_preview=True)
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.warning(f"watch_loop @{username} 出错: {e}")

async def cmd_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args or []
    if not args:
        await update.message.reply_text("用法：/watch @用户名\n例如：/watch @HyperliquidX")
        return

    username = args[0].lstrip("@")
    chat_id  = update.effective_chat.id

    if chat_id not in watch_tasks:
        watch_tasks[chat_id] = {}

    if username in watch_tasks[chat_id]:
        await update.message.reply_text(f"已经在监控 @{username} 了")
        return

    task = asyncio.create_task(_watch_loop(chat_id, username, ctx.application))
    watch_tasks[chat_id][username] = task
    await update.message.reply_text(
        f"✅ 开始监控 @{username}\n每 2 分钟检查一次，有新推文会自动推送。\n\n"
        f"取消监控：/unwatch {username}"
    )
    logger.info("开始监控 @%s | chat=%s", username, chat_id)

async def cmd_unwatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args or []
    if not args:
        await update.message.reply_text("用法：/unwatch @用户名")
        return

    username = args[0].lstrip("@")
    chat_id  = update.effective_chat.id

    if chat_id in watch_tasks and username in watch_tasks[chat_id]:
        watch_tasks[chat_id].pop(username).cancel()
        await update.message.reply_text(f"✅ 已取消监控 @{username}")
    else:
        await update.message.reply_text(f"没有在监控 @{username}")

async def cmd_watching(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    accounts = list((watch_tasks.get(chat_id) or {}).keys())
    if accounts:
        names = "\n".join(f"• @{a}" for a in accounts)
        await update.message.reply_text(f"👁  当前监控账号：\n\n{names}")
    else:
        await update.message.reply_text("目前没有监控任何账号。\n\n发送 /watch @用户名 开始监控。")

# ── 入口 ─────────────────────────────────────────────────────
def main():
    app = ApplicationBuilder().token(TG_TOKEN).build()
    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("help",      cmd_help))
    app.add_handler(CommandHandler("revenue",   cmd_revenue))
    app.add_handler(CommandHandler("top7",      cmd_revenue))
    app.add_handler(CommandHandler("news",      cmd_news))
    app.add_handler(CommandHandler("hot",       cmd_hot))
    app.add_handler(CommandHandler("bloomberg", cmd_bloomberg))
    app.add_handler(CommandHandler("reuters",   cmd_reuters))
    app.add_handler(CommandHandler("daily",     cmd_daily))
    app.add_handler(CommandHandler("watch",     cmd_watch))
    app.add_handler(CommandHandler("unwatch",   cmd_unwatch))
    app.add_handler(CommandHandler("watching",  cmd_watching))
    logger.info("Bot 启动，polling 中…")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
