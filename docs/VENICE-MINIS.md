# Venice → Minis: direct official API

Venice is a **separate provider**, not another name for NoTrack and not routed through this project's web-chat adapter. The direct route avoids this adapter's HTTP-body and 1,980-character instruction limits. It does not remove Venice's own model, account, billing, context or rate limits.

## Prerequisites

1. Create a genuine key at <https://venice.ai/settings/api>.
2. Confirm account credit and model eligibility. A readable model catalog is **not** proof that a key works; no unconditional free inference is promised.
3. In Minis → Settings → Environment Variables, set `VENICE_API_KEY`. Do not paste it into chat, issues, URLs or screenshots.

Minis configuration (use the template below, then approve the in-app confirmation):

```json
{
  "label": "Venice Official",
  "providerType": "openAI",
  "credentialType": "apiKey",
  "customBaseURL": "https://api.venice.ai/api/v1",
  "appendV1Suffix": false,
  "useResponsesAPI": false,
  "azureMode": false,
  "apiKey": "$$VENICE_API_KEY",
  "isEnabled": true
}
```

`$$VENICE_API_KEY` is Minis's insertion-time environment-variable reference, not a dummy credential. Outside Minis, clients must use the actual secret through their own secure environment. The Chat Completions choice is intentional; it is not a claim that Venice has no Responses API.

After saving, discover models from the provider, add a **separate** group, and leave the existing default group unchanged until tests pass. Choose a model's exact current ID and check its `supportsFunctionCalling`, `availableContextTokens`, and `maxCompletionTokens` in the catalog. The model label alone is not proof of tool compatibility.

## Public catalog snapshot — 2026-11-07

`GET https://api.venice.ai/api/v1/models?type=text` returned HTTP 200 and 141 entries without a key. Selected provider-reported entries:

| Model ID | Context tokens | Max completion tokens |
|---|---:|---:|
| `venice-uncensored-1-2` | 128,000 | 8,192 |
| `mistral-small-3-2-24b-instruct` | 256,000 | 16,384 |
| `qwen3-coder-480b-a35b-instruct-turbo` | 256,000 | 65,536 |

Catalog entries, limits, pricing and eligibility can change. These are not observed inference results. Some model privacy modes or subscription requirements differ.

## Verification checklist before declaring connected

- Verify the key with the authenticated `/api_keys/rate_limits` endpoint, not `/models`: the latter is public even without a valid key. During this release's unauthenticated check, rate-limits returned HTTP 402 `Authentication required` with wallet-auth alternatives; no payment or wallet action was performed.
- Run the **normal Minis model test**, including `max_completion_tokens: 128`, `stream: true`, and `stream_options: {"include_usage": true}`. Do not call a special stripped-down bypass and report that as full compatibility.
- Confirm useful nonempty content, no SSE error, and normal stream completion. Venice can emit a final usage chunk with empty `choices`; clients must tolerate it.
- Test a Persian question and an output longer than the old short web budget if that is needed.
- If tools are needed, select a model whose catalog reports function calling and test an actual tool round trip separately. A text reply does not prove an Agent session works.
- Respect 402 balances/spend caps, 429 cooldown and per-model limits; never rotate identities to evade limits. Do not invent token usage or silently discard tool schemas/system messages.

Official docs:
- <https://docs.venice.ai/guides/getting-started/openai-migration>
- <https://docs.venice.ai/api-reference/endpoint/chat/completions>
- <https://docs.venice.ai/guides/features/function-calling>
- <https://docs.venice.ai/guides/getting-started/generating-api-key>

## خلاصهٔ فارسی

Venice را مستقیم و به‌صورت **Provider جداگانه در Minis** وصل کنید؛ درخواستش نباید از واسط وب NoTrack بگذرد. در غیر این صورت همان محدودیت‌های انتقال، ابزار و کانتکست واسط دوباره اعمال می‌شوند.

- کلید واقعی را در حساب Venice بسازید و در Environment Variables مینیس با نام `VENICE_API_KEY` قرار دهید؛ نمونه یا کلید ساختگی کار نمی‌کند.
- نوع `OpenAI`، نشانی `https://api.venice.ai/api/v1`، افزودن خودکار `/v1` خاموش، و `Responses API` خاموش باشد.
- مدل `venice-uncensored-1-2` در فهرست عمومی فعلی وجود دارد، اما این به‌تنهایی اعتبار کلید، موجودی، دسترسی حساب یا پاسخ واقعی را اثبات نمی‌کند.
- API رسمی طبق مستندات `max_completion_tokens`، گزینهٔ `include_usage` و ابزار در مدل‌های سازگار را دارد. با همان درخواست تست Minis آزمایش کنید؛ تست ابزار را هم جدا انجام دهید.
- در زمان انتشار این راهنما کلید Venice در محیط موجود نبود؛ **اتصال، موجودی و inference آن تأیید نشده است**. خواندن فهرست مدل‌ها با اتصال عملیاتی تفاوت دارد.
