# بوت متجر تليجرام آلي للحسابات الرقمية

مشروع إنتاجي مبني بـ **Python 3.12 + Aiogram 3 + FastAPI + PostgreSQL + Redis**. ينفذ الشراء، خصم المحفظة، اختيار نسخة فريدة من المخزون، التسليم الخاص، شحن Binance Pay، الإحالات، سجل العمليات، ولوحة الإدارة بصورة آلية.

> مهم قانونيًا: استخدم المشروع فقط لبيع حسابات أو تراخيص تملك حق إعادة بيعها، وبعد التأكد من شروط مزود الخدمة والقوانين المحلية ومتطلبات الضرائب وKYC/AML والاسترداد. لا تستخدمه لبيع حسابات مسروقة أو مشتركة دون إذن.

## ما الذي ينفذه المشروع؟

- أقسام ومنتجات وأسعار ومخزون مشفر.
- معاملة PostgreSQL ذرية عند الشراء:
  - قفل رصيد العميل والمنتج.
  - اختيار حساب متاح بـ `FOR UPDATE SKIP LOCKED`.
  - خصم الرصيد وتعليم الحساب كمباع وإنشاء الطلب في معاملة واحدة.
  - لا يمكن بيع صف المخزون نفسه لعميلين حتى عند الضغط المتزامن.
- استعادة بيانات أي طلب سابق من زر «طلباتي» إذا تعذر وصول رسالة التسليم.
- إنشاء فاتورة Binance Pay V3، والتحقق من Webhook بتوقيع RSA وشهادة Binance، ثم استعلام تأكيدي مستقل عن حالة الطلب قبل إضافة الرصيد.
- منع تكرار إضافة الدفعة عبر قفل الفاتورة و`transaction_id` ودفتر محفظة ذي `idempotency_key` فريد.
- رابط إحالة فريد، واحتساب العضو مرة واحدة فقط بعد الاشتراك بالقناة والتحقق منه.
- إضافة 1 دولار تلقائيًا عند كل 20 إحالة ناجحة (القيم قابلة للتعديل).
- قناة Live Feed مع تغويش الاسم، مثل `Moh****d`.
- صندوق Outbox يعيد محاولة رسائل التسليم وإشعارات المستخدم والقناة تلقائيًا عند أخطاء الشبكة المؤقتة.
- لوحة إدارة لإضافة الأقسام والمنتجات والمخزون النصي وتعديل السعر وعرض الإحصائيات.

لا يمكن لأي برنامج ضمان توافر «100%» في حال تعطل Telegram أو Binance أو الخادم، لكن التدفق نفسه مؤتمت بالكامل، ومعاملاته غير قابلة للتكرار، والإشعارات تعاد تلقائيًا.

## هيكل المشروع

```text
app/
├── main.py                  # FastAPI وWebhooks ودورة تشغيل البوت
├── bot.py                   # Bot/Dispatcher وRedis FSM
├── config.py                # متغيرات البيئة والتحقق منها
├── models.py                # نماذج PostgreSQL
├── binance_pay.py           # توقيع API وRSA Webhook وCreate/Query Order
├── security.py              # تشفير المخزون وتغويش الأسماء والأموال
├── keyboards.py
├── presentation.py
├── routers/
│   ├── user.py              # المتجر، المحفظة، الإحالة، الطلبات
│   └── admin.py             # لوحة الإدارة
└── services/
    ├── store.py             # معاملة الشراء الذرية
    ├── payments.py          # الفواتير وإضافة الرصيد غير المتكررة
    ├── referrals.py         # التحقق والمكافآت
    ├── admin.py
    └── outbox.py            # إعادة محاولة التسليم والإشعارات
alembic/                     # ترحيلات قاعدة البيانات
tests/                       # اختبارات التشفير والتغويش وتوقيع Binance
docker-compose.yml           # App + PostgreSQL + Redis + Caddy HTTPS
railway.toml                 # البناء والترحيلات وفحص الصحة على Railway
```

## 1. المتطلبات

- خادم VPS يدعم Docker وDocker Compose.
- نطاق Domain يشير بسجل `A` أو `AAAA` إلى الخادم. يتولى Caddy إصدار HTTPS تلقائيًا.
- بوت Telegram من `@BotFather`.
- حساب **Binance Pay Merchant** مقبول وبيانات Merchant API. مفتاح API العادي للتداول ليس بديلًا عن Binance Pay Merchant API.
- قناتان أو قناة واحدة للاشتراك الإلزامي وLive Feed.

## 2. إعداد Telegram

1. افتح `@BotFather` ثم `/newbot` وأنشئ البوت.
2. انسخ التوكن إلى `BOT_TOKEN` في ملف `.env`.
3. أضف البوت مشرفًا في قناة السجل مع صلاحية نشر الرسائل.
4. أضفه مشرفًا في قناة الاشتراك الإلزامي. هذا مهم لأن `getChatMember` لا يكون موثوقًا للتحقق من مستخدمين آخرين إلا عندما يكون البوت مشرفًا.
5. ضع المعرّف الرقمي للقناة في `FEED_CHANNEL_ID` و`REQUIRED_CHANNEL_ID`، عادة بصيغة `-100...`، وضع رابطها في `REQUIRED_CHANNEL_URL`.
6. ضع Telegram ID الخاص بالمدير في `ADMIN_IDS`. يمكن إضافة أكثر من مدير بفاصلة.

لا تضع التوكن أو المعرّفات الحساسة داخل ملفات Python. جميعها معدّة عبر `.env` حتى لا تتسرب عند رفع الكود.

## 3. إعداد Binance Pay Merchant API

1. سجّل أو فعّل حساب Binance Pay Merchant وأكمل متطلبات التاجر المتاحة لبلدك ونشاطك.
2. من لوحة **Binance Merchant Admin Portal** افتح إعدادات المطور/API وأنشئ API Identity Key وSecret Key.
3. ضع القيم في:

   ```dotenv
   BINANCE_API_KEY=...
   BINANCE_SECRET_KEY=...
   ```

4. اضبط عنوان الإشعار في لوحة Binance، إن كان الخيار ظاهرًا، إلى:

   ```text
   https://bot.example.com/webhooks/binance-pay
   ```

   المشروع يرسل العنوان نفسه داخل كل طلب V3، ولهذا يتجاوز عنوان اللوحة لذلك الطلب حسب مواصفات Binance.

5. اضبط ساعة الخادم عبر NTP. Binance يوقع الطلبات ويقيّد فارق التوقيت.
6. إذا فعّلت IP allowlist في Binance فأضف عنوان الخادم الثابت.

التكامل يستخدم:

- `POST /binancepay/openapi/v3/order` لإنشاء الفاتورة.
- `POST /binancepay/openapi/v2/order/query` لتأكيد أن الطلب `PAID`.
- `POST /binancepay/openapi/certificates` لجلب المفتاح العام الذي يتحقق من Webhook.
- HMAC-SHA512 للطلبات الصادرة وRSA/SHA-256 للإشعارات الواردة.

الوثائق الرسمية:

- [Create Order V3](https://developers.binance.com/en/docs/products/binance-pay-merchant/api-order-create-v3)
- [Query Order](https://developers.binance.com/en/docs/products/binance-pay-merchant/api-order-query-v2)
- [Webhook Common Rules](https://developers.binance.com/en/docs/products/binance-pay-merchant/webhook-common)
- [Order Notification](https://developers.binance.com/en/docs/products/binance-pay-merchant/order-notification)

### وحدة الرصيد

الإعداد الافتراضي ينشئ فاتورة `USDT` ويضيف العدد نفسه بوصفه دولارات محفظة؛ أي `10 USDT = $10` داخل المتجر. إن أردت تحمل فرق سعر USDT أو استخدام عملة أخرى، أضف خدمة تسعير وسجّل سعر التحويل لحظة إنشاء الفاتورة بدل افتراض 1:1.

## 4. إعداد وتشغيل المشروع

من مجلد المشروع:

```bash
cp .env.example .env
```

أنشئ مفتاح تشفير بيانات المخزون:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

أنشئ سر Telegram Webhook:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

عدّل `.env` وضع القيم الفعلية، وتأكد من تطابق كلمة مرور PostgreSQL في السطرين:

```dotenv
POSTGRES_PASSWORD=your_password
DATABASE_URL=postgresql+asyncpg://store:your_password@db:5432/store
```

ثم شغّل:

```bash
docker compose up -d --build
docker compose logs -f app
```

ينفذ حاوي التطبيق `alembic upgrade head` تلقائيًا قبل التشغيل. افحص الحالة عبر:

```text
https://bot.example.com/health
```

لا تغيّر `CREDENTIAL_ENCRYPTION_KEY` بعد إدخال مخزون؛ تغييره يجعل السجلات القديمة غير قابلة للفك. احتفظ بنسخة احتياطية آمنة منه خارج الخادم.

## 4.1 النشر على Railway

المشروع جاهز للنشر من المجلد الجذر عبر `Dockerfile` و`railway.toml`. لا تستخدم
`docker-compose.yml` داخل Railway؛ أنشئ الخدمات الثلاث داخل مشروع Railway واحد:

1. خدمة PostgreSQL مُدارة باسم `Postgres`.
2. خدمة Redis مُدارة باسم `Redis`.
3. خدمة التطبيق من مستودع GitHub الذي يحتوي هذا المشروع.

في خدمة التطبيق، انسخ مفاتيح `.env.railway.example` إلى تبويب **Variables**. استخدم
مراجع Railway التالية بدل نسخ كلمات مرور قواعد البيانات:

```dotenv
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}
```

بعد أول نشر، افتح **Settings → Networking → Generate Domain**، ثم ضع النطاق الناتج في:

```dotenv
PUBLIC_BASE_URL=https://your-app.up.railway.app
```

سيؤدي تعديل المتغير إلى إعادة النشر، وعند بدء التطبيق سيسجل Webhook Telegram تلقائيًا.
يشغّل Railway ترحيلات `alembic upgrade head` كأمر **Pre-deploy**، ويستخدم `/health`
لفحص التطبيق. يدعم الكود عنوان Railway من نوع `postgresql://` ويحوّله تلقائيًا إلى
مشغل `asyncpg`.

لا تضف `BOT_DOMAIN` أو `POSTGRES_PASSWORD` إلى خدمة التطبيق على Railway؛ هذان المتغيران
خاصان بتشغيل Docker Compose المحلي. ولا تضع القيم السرية في GitHub أو داخل الملفات.

بعد نجاح النشر اختبر:

```text
https://your-app.up.railway.app/health
```

ويجب أن تكون النتيجة `{"status":"ok"}`. ثم ضع عنوان Binance Pay Webhook:

```text
https://your-app.up.railway.app/webhooks/binance-pay
```

## 5. استخدام لوحة الإدارة

أرسل `/admin` من حساب موجود في `ADMIN_IDS`.

- **إضافة قسم:** أرسل اسم القسم.
- **إضافة منتج:**

  ```text
  رقم_القسم | اسم المنتج | السعر | الوصف
  ```

- **إضافة مخزون:** أرسل رقم المنتج في أول سطر، ثم حسابًا في كل سطر:

  ```text
  1
  first@example.com|StrongPassword|معلومة اختيارية
  second@example.com|AnotherPassword
  ```

- **تعديل السعر:**

  ```text
  رقم_المنتج | السعر_الجديد
  ```

الأوامر المباشرة متاحة أيضًا: `/add_category` و`/add_product` و`/add_stock` و`/set_price` و`/stats`.

## 6. كيف يمنع نظام الإحالة التلاعب؟

لا توفر Telegram Bot API إثباتًا مطلقًا أن كل حساب يخص إنسانًا مختلفًا، لذلك ينفذ المشروع القيود الممكنة عمليًا:

1. قبول رابط الإحالة عند إنشاء سجل المستخدم لأول مرة فقط.
2. منع الإحالة الذاتية وحسابات البوتات.
3. مفتاح أساسي فريد لكل Telegram ID.
4. عدم احتساب الدعوة إلا بعد ضغط العضو الجديد «تحقق من الاشتراك» ونجاح `getChatMember`.
5. حقل `referral_verified_at` يجعل الاحتساب مرة واحدة فقط.
6. دفتر محفظة بمفتاح فريد لكل كتلة مكافأة يمنع مضاعفتها.
7. التحقق من استمرار الاشتراك مرة أخرى قبل الشراء.

لرفع مستوى الحماية أكثر يمكن إضافة CAPTCHA، حد زمني أدنى قبل الاحتساب، أو اشتراط أول عملية شراء. تلك قواعد تجارية وليست دليلًا قاطعًا على الهوية.

## 7. الأمان والاعتمادية

- لا تُخزن كلمات المرور بنص صريح؛ تستخدم Fernet للتشفير وHMAC fingerprint لاكتشاف التكرار دون كشف البريد.
- لا يُحذف الحساب المباع من سجل التدقيق، لكنه يتحول إلى `sold` ولا يدخل أي استعلام مخزون لاحق.
- كل رصيد له سجل في `wallet_ledger`.
- لا يعتمد Webhook على محتوى الرسالة وحده؛ يتحقق من RSA والتوقيت والمبلغ والعملة و`prepayId` ثم يستعلم من Binance.
- Webhook غير متكرر؛ إعادة Binance للإشعار لا تضيف الرصيد مرتين.
- رسائل البيانات تستخدم `protect_content=True`، لكن لا يمكن منع المستخدم من تصوير الشاشة.
- نفذ نسخًا احتياطية مشفرة يومية لقاعدة PostgreSQL، وراقب السجلات ومساحة القرص.
- ضع قواعد واضحة للاستبدال والاسترداد؛ الكود لا ينفذ استرداد Binance تلقائيًا لأن ذلك يحتاج سياسة تجارية ومراجعة مخاطر مستقلة.

## 8. تشغيل محلي واختبارات

للاختبار دون Telegram Webhook يمكن ضبط:

```dotenv
BOT_MODE=polling
```

لكن Binance Pay Webhook يظل بحاجة إلى عنوان HTTPS عام للوصول إلى الخادم، لذلك استخدم وضع Webhook في الإنتاج.

تشغيل الاختبارات:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
ruff check .
```

## 9. تغييرات شائعة

- قيمة المكافأة: `REFERRAL_REWARD_USD`.
- عدد الدعوات: `REFERRAL_THRESHOLD`.
- حد الشحن: `MIN_TOPUP_USD` و`MAX_TOPUP_USD`.
- عملة الدفع: `BINANCE_CURRENCY`، بشرط أن تكون مدعومة لحساب التاجر.
- رسائل Live Feed: `app/services/outbox.py`.
- أزرار المتجر: `app/keyboards.py`.

قبل إطلاق المتجر فعليًا، نفذ دفعة صغيرة من حساب اختبار، وتأكد من ظهورها مرة واحدة في المحفظة، ثم اختبر شراء نسختين متزامنتين من المخزون ونسخة واحدة متبقية.
