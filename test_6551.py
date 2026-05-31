"""
6551.io API 全面测试脚本 v2
测试三个模块：OpenNews / OpenTwitter / Daily News
运行：python3 test_6551.py
"""

import asyncio, os, re, textwrap
from datetime import datetime
import httpx
from dotenv import load_dotenv

load_dotenv()

NL_TOKEN = os.environ.get("NEWSLIQUID_TOKEN", "")
BASE     = "https://ai.6551.io"
HEADERS  = {"Authorization": f"Bearer {NL_TOKEN}", "Content-Type": "application/json"}

TEST_COIN    = "HYPE"
TEST_KEYWORD = "Hyperliquid"
TEST_TWITTER = "HyperliquidX"

W = 70

def sep(title=""):
    if title:
        pad = (W - len(title) - 2) // 2
        print(f"\n{'─'*pad} {title} {'─'*pad}")
    else:
        print("─" * W)

def ok(label):       print(f"  ✅  {label}")
def fail(label, e):  print(f"  ❌  {label}：{e}")

def strip_html(text):
    text = re.sub(r'<[^>]+>', '', text)
    return text.strip()

def fmt_ts(ts):
    try:
        s = str(ts).split("+")[0].split("Z")[0].replace("T"," ")
        dt = datetime.fromisoformat(s)
        return dt.strftime("%m/%d %H:%M")
    except:
        return str(ts)[:16]

def show_item(item, idx=None):
    prefix = f"  [{idx}] " if idx is not None else "  "
    ts     = fmt_ts(item.get("ts",""))
    text   = strip_html(item.get("text",""))[:75]
    src    = item.get("newsType","")
    eng    = item.get("engineType","")
    ai     = item.get("aiRating") or {}
    score  = ai.get("score","-")
    sig    = ai.get("signal","-")
    grade  = ai.get("grade","-")
    coins  = [c["symbol"] for c in item.get("coins",[]) if "-" not in c["symbol"]][:3]
    summary= strip_html(ai.get("summary") or "")[:55]

    print(f"{prefix}{ts}  [{src}/{eng}]")
    print(f"       {textwrap.shorten(text,70)}")
    if score != "-":
        icon = {"long":"▲","short":"▼"}.get(sig,"─")
        print(f"       评分 {score}/100  {icon} {sig}  等级 {grade}  {' '.join(coins)}")
    if summary:
        print(f"       摘要：{summary}")

def show_tweet(t, idx=None):
    prefix = f"  [{idx}] " if idx is not None else "  "
    text   = strip_html(t.get("text",""))[:75]
    url    = t.get("url","")
    like   = t.get("like_count", t.get("favoriteCount",0))
    rt     = t.get("retweet_count", t.get("retweetCount",0))
    at     = fmt_ts(t.get("created_at", t.get("createdAt","")))
    print(f"{prefix}{at}")
    print(f"       {textwrap.shorten(text,70)}")
    print(f"       ❤ {like}  🔁 {rt}  {url[:50] if url else ''}")

async def post(endpoint, body):
    transport = httpx.AsyncHTTPTransport(proxy=None)
    async with httpx.AsyncClient(timeout=20, transport=transport) as c:
        r = await c.post(f"{BASE}{endpoint}", headers=HEADERS, json=body)
        r.raise_for_status()
        return r.json()

async def get(endpoint, params=None):
    transport = httpx.AsyncHTTPTransport(proxy=None)
    async with httpx.AsyncClient(timeout=20, transport=transport) as c:
        r = await c.get(f"{BASE}{endpoint}", headers=HEADERS, params=params)
        r.raise_for_status()
        return r.json()

def extract_items(d):
    raw = d.get("data", [])
    return raw if isinstance(raw, list) else raw.get("list", [])

# ══════════════════════════════════════════════════════════════
# 模块一：OpenNews
# ══════════════════════════════════════════════════════════════
async def test_opennews():
    sep("1 · OpenNews — 新闻搜索")

    tests = [
        ("1-A 最新新闻（全源）",        {"limit": 3}),
        (f"1-B {TEST_COIN} 相关新闻",   {"coins": [TEST_COIN], "limit": 3}),
        ("1-C 高分新闻（score≥70）",    {"score": 70, "limit": 3}),
        ("1-D 上币公告",                {"engineTypes": {"listing": []}, "limit": 3}),
        ("1-E 链上鲸鱼",                {"engineTypes": {"onchain": []}, "limit": 3}),
        ("1-F 市场异动",                {"engineTypes": {"market": []}, "limit": 3}),
        ("1-G AI 预测信号",             {"engineTypes": {"prediction": []}, "limit": 3}),
        ("1-H ▲ long 信号",             {"signal": "long", "limit": 3}),
        ("1-I ▼ short 信号",            {"signal": "short", "limit": 3}),
    ]

    for label, body in tests:
        try:
            d     = await post("/open/news_search", body)
            items = extract_items(d)
            print(f"\n  {label}，返回 {len(items)} 条")
            for i, it in enumerate(items): show_item(it, i+1)
            ok(label)
        except Exception as e:
            fail(label, e)

# ══════════════════════════════════════════════════════════════
# 模块二：OpenTwitter
# ══════════════════════════════════════════════════════════════
async def test_opentwitter():
    sep("2 · OpenTwitter — Twitter/X 数据")

    # 2-A 用户资料（正确接口：/open/twitter_user_info）
    try:
        d = await post("/open/twitter_user_info", {"username": TEST_TWITTER})
        u = d.get("data", {})
        print(f"\n  2-A  @{TEST_TWITTER} 用户资料")
        print(f"       名称：{u.get('name','?')}  粉丝：{u.get('followersCount',u.get('followers_count','?')):,}" if isinstance(u.get('followersCount',u.get('followers_count',0)), int) else f"       名称：{u.get('name','?')}")
        print(f"       简介：{strip_html(u.get('description',''))[:60]}")
        ok("用户资料")
    except Exception as e:
        fail("用户资料", e)

    # 2-B 最新推文
    try:
        d      = await post("/open/twitter_user_tweets", {"username": TEST_TWITTER, "limit": 5})
        dd     = d.get("data", {})
        tweets = dd.get("tweets", dd) if isinstance(dd, dict) else dd
        print(f"\n  2-B  @{TEST_TWITTER} 最新推文，返回 {len(tweets)} 条")
        for i, t in enumerate(tweets[:3]): show_tweet(t, i+1)
        ok("最新推文")
    except Exception as e:
        fail("最新推文", e)

    # 2-C 关键词搜索（正确参数：keywords）
    try:
        d      = await post("/open/twitter_search", {"keywords": TEST_KEYWORD, "maxResults": 5})
        dd     = d.get("data", {})
        tweets = dd.get("tweets", dd) if isinstance(dd, dict) else dd
        print(f"\n  2-C  关键词「{TEST_KEYWORD}」推文，返回 {len(tweets)} 条")
        for i, t in enumerate(tweets[:3]): show_tweet(t, i+1)
        ok("关键词搜索")
    except Exception as e:
        fail("关键词搜索", e)

    # 2-D Hashtag 搜索
    try:
        d      = await post("/open/twitter_search", {"hashtag": "Hyperliquid", "maxResults": 5})
        dd     = d.get("data", {})
        tweets = dd.get("tweets", dd) if isinstance(dd, dict) else dd
        print(f"\n  2-D  Hashtag #Hyperliquid，返回 {len(tweets)} 条")
        for i, t in enumerate(tweets[:3]): show_tweet(t, i+1)
        ok("Hashtag 搜索")
    except Exception as e:
        fail("Hashtag 搜索", e)

    # 2-E 粉丝动态
    try:
        d      = await post("/open/twitter_follower_events", {"username": TEST_TWITTER, "isFollow": True, "maxResults": 5})
        dd     = d.get("data", {})
        events = dd if isinstance(dd, list) else dd.get("list", [])
        print(f"\n  2-E  @{TEST_TWITTER} 新增粉丝，返回 {len(events)} 条")
        for ev in events[:3]:
            print(f"       {ev.get('eventType','?')}  @{ev.get('twAccount','?')}  {fmt_ts(ev.get('createdAt',''))}")
        ok("粉丝动态")
    except Exception as e:
        fail("粉丝动态", e)

    # 2-F 删推记录
    try:
        d      = await post("/open/twitter_deleted_tweets", {"username": TEST_TWITTER, "maxResults": 5})
        dd     = d.get("data", {})
        tweets = dd.get("tweets", dd) if isinstance(dd, dict) else dd
        print(f"\n  2-F  @{TEST_TWITTER} 删推记录，返回 {len(tweets)} 条")
        for i, t in enumerate(tweets[:2]): show_tweet(t, i+1)
        ok("删推记录")
    except Exception as e:
        fail("删推记录", e)

# ══════════════════════════════════════════════════════════════
# 模块三：Daily News（免费接口，无需 Token 过滤）
# ══════════════════════════════════════════════════════════════
async def test_daily_news():
    sep("3 · Daily News — 每日热点")

    # 3-A 分类列表
    try:
        transport = httpx.AsyncHTTPTransport(proxy=None)
        async with httpx.AsyncClient(timeout=20, transport=transport) as c:
            r = await c.get(f"{BASE}/open/free_categories", headers=HEADERS)
            r.raise_for_status()
            d = r.json()
        cats = d if isinstance(d, list) else d.get("data", [])
        print(f"\n  3-A  新闻分类，共 {len(cats)} 个")
        for cat in cats[:6]:
            subs = [s["key"] for s in cat.get("subcategories", [])][:4]
            print(f"       {cat.get('key','?'):15} → {', '.join(subs)}")
        ok("分类列表")

        # 3-B 取第一个分类的热点
        if cats:
            cat_key = cats[0]["key"]
            try:
                transport2 = httpx.AsyncHTTPTransport(proxy=None)
                async with httpx.AsyncClient(timeout=20, transport=transport2) as c2:
                    r2 = await c2.get(f"{BASE}/open/free_hot", headers=HEADERS,
                                      params={"category": cat_key})
                    r2.raise_for_status()
                    d2 = r2.json()
                dd         = d2 if isinstance(d2, dict) else d2.get("data", {})
                news_items  = (dd.get("news") or {}).get("items", [])
                tweet_items = (dd.get("tweets") or {}).get("items", [])
                print(f"\n  3-B  「{cat_key}」热点：新闻 {len(news_items)} 条 / 推文 {len(tweet_items)} 条")
                for i, n in enumerate(news_items[:2]):
                    title = strip_html(n.get("title", n.get("text","")))[:68]
                    print(f"       [{i+1}] {title}")
                    print(f"            评分 {n.get('score','-')}  信号 {n.get('signal','-')}")
                for i, t in enumerate(tweet_items[:2]):
                    print(f"       [推{i+1}] {strip_html(t.get('content',''))[:65]}")
                    print(f"            @{t.get('handle','')}  ❤ {t.get('metrics',{}).get('likes',0)}")
                ok(f"「{cat_key}」热点")
            except Exception as e:
                fail(f"「{cat_key}」热点", e)
    except Exception as e:
        fail("分类列表", e)

# ══════════════════════════════════════════════════════════════
async def main():
    print("=" * W)
    print("  6551.io NewsLiquid API 全面测试 v2")
    print(f"  时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  测试币种：{TEST_COIN}  测试账号：@{TEST_TWITTER}")
    print("=" * W)
    if not NL_TOKEN:
        print("\n❌ 未找到 NEWSLIQUID_TOKEN，请先在 .env 文件中配置")
        return
    await test_opennews()
    await test_opentwitter()
    await test_daily_news()
    sep()
    print("  测试完成！")
    print("=" * W)

if __name__ == "__main__":
    asyncio.run(main())
