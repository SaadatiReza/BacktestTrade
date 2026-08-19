# SP2L Backtester (قدم اول)

بک‌اند Python (FastAPI + SQLite) برای گرفتن دیتای قیمت از Twelve Data، تشخیص ستاپ SP2L و شبیه‌سازی معاملات، به‌همراه فرانت‌اند React برای نمایش نمودار (Lightweight Charts) و نتایج بک‌تست.

## چه چیزی پیاده شده

پایپ‌لاین کامل: **Context → Level → Spike → 2L → Entry (Retest/Breakout) → SL → TP → آمار معاملات**، دقیقاً همون بخش‌هایی که در سند شما به‌عنوان قواعد تأییدشده/عمومی مشخص شده بودند.

**عمداً پیاده نشده** (طبق تفکیک خود سند شما، چون تعریف دقیقشون فقط در ویدیوی اصلی هست):
- ۴ نوع Spike
- P-Gap
- مدل ورود 2X

این سه مورد به‌عنوان TODO مستند در [backend/app/strategy/sp2l.py](backend/app/strategy/sp2l.py) علامت‌گذاری شدن. وقتی جزئیاتشون رو از ویدیو استخراج کردید، بگید تا اضافه‌شون کنم — تا اون موقع بهتره حدس زده نشن که یه قانون غلط قاطی موتور بک‌تست نشه.

## اجرا

### بک‌اند
```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # و کلید Twelve Data رو داخلش بذارید
uvicorn app.main:app --reload --port 8000
```

### فرانت‌اند
```bash
cd frontend
npm install
npm run dev
```
بعد آدرس `http://localhost:5173` رو باز کنید.

## نکته درباره داده

اتصال مستقیم به TradingView (اسکرپینگ) خلاف قوانین اونهاست. اینجا از API رایگان [Twelve Data](https://twelvedata.com/) استفاده شده که XAU/USD و اکثر نمادهای فارکس/سهام رو پوشش می‌ده. برای نمایش نمودار هم از کتابخونه رسمی و متن‌باز خود TradingView یعنی [lightweight-charts](https://github.com/tradingview/lightweight-charts) استفاده شده.

## معماری برای آینده (سیگنال زنده + بررسی Winrate)

جدول‌های دیتابیس (`Candle`, `BacktestRun`, `Trade`) طوری طراحی شدن که همین ساختار برای معاملات آینده (سیگنال زنده) هم قابل استفاده باشه — فیلد `Trade.source` از الان `backtest`/`live` رو پشتیبانی می‌کنه. وقتی به مرحله سیگنال زنده رسیدیم، فقط یه پروسه‌ی زمان‌بندی‌شده (cron/worker) لازمه که همین منطق `strategy/sp2l.py` رو روی کندل‌های تازه اجرا کنه و نتیجه رو با `source="live"` ذخیره کنه؛ صفحه‌ی وب هم می‌تونه یه تب "معاملات زنده" و "آمار Winrate تجمیعی" اضافه کنه بدون تغییر در موتور استراتژی.

## تنظیمات قابل تغییر استراتژی (`SP2LConfig`)

هر پارامتر که مقدار پیش‌فرضش از منابع عمومی (نه خود ویدیو) گرفته شده، در [backend/app/schemas.py](backend/app/schemas.py) با توضیح مشخص شده — مثلاً `min_body_ratio=0.65`. این‌ها نقطه‌ی شروع بک‌تست هستن، نه قانون قطعی؛ از پنل "تنظیمات پیشرفته استراتژی" در فرانت‌اند قابل تغییرن تا با تماشای ویدیو بتونید دقیقشون کنید.
