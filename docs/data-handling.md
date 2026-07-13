# Data handling: which models may see what

Researched 2026-07-13. This exists because the routing engine has to answer a
question that has nothing to do with cost or capability: **may this model see my
client's production customer data?** The `data_ok` column in the model table is that
answer, and this document is the basis for it — so the reasoning outlives the
decision, and so a future refresh (or a future me) can't quietly reverse it by
guessing.

## The situation this is written for

A **freelancer**, no enterprise agreements, working on a client's system that holds
**German banking customer data** (GDPR applies). The tools in play are all
**consumer subscriptions**: Claude Max/Pro, ChatGPT Plus (for Codex), and a personal
OpenRouter API key.

The first version of the model table assumed "Anthropic and OpenAI are trusted
because there's a commercial relationship; the OpenRouter models aren't." **That was
false.** There is no commercial agreement with any of them. The flag was encoding a
contract that doesn't exist.

## What the terms actually say

| Tool (as used here) | Trains by default? | Retention | Where |
|---|---|---|---|
| **Claude Pro/Max** (incl. Claude Code) | Mandatory choice since 2025-10-08 — not silently on | Training on: **5 years**. Off: **30 days** | US |
| **Codex via ChatGPT Plus** | **YES — on by default** (consumer policy, *not* the API policy) | Consumer retention | US |
| **OpenRouter** (the router itself) | **No** — does not store prompts unless you opt in | Metadata only | US |
| **DeepSeek** first-party | **Yes** — "to train and improve our technology" | "As long as necessary" | **China (PRC law)** |
| **Z.AI / GLM** first-party | Ambiguous; DPA says content is not stored | Real-time only (per DPA) | Singapore |
| **Moonshot / Kimi** first-party | **Yes, by default** (ToS §4) | Unspecified | Singapore |

### The three findings that actually change behavior

**1. No consumer plan carries a Data Processing Agreement.** GDPR Art. 28 requires one
with *each* processor handling the data. Claude Pro/Max and ChatGPT Plus do not come
with one. **This is the real blocker, and it applies to the "safe" providers too** —
it is not a reason to prefer Anthropic/OpenAI over the cheap models, because none of
them clear it. It is a reason to think hard before putting customer data into *any*
prompt.

**2. Codex on ChatGPT Plus trains on your data by default**, and has a **second,
Codex-only toggle** that the main ChatGPT setting does not cover — OpenAI's words:
"adjusting your settings in the ChatGPT interface or privacy portal will *not* affect
these full-environment Codex settings."

**3. Anthropic's opt-out has carve-outs that survive it.** Even with training off:
safety-flagged conversations are retained up to 2 years (no definition of what
triggers a flag, no notification), and a thumbs-up/down or `/feedback` attaches the
whole conversation for 5 years.

## OpenRouter can be made zero-retention — and it fails closed

This is the useful discovery. OpenRouter is a **broker**: it forwards each request to
whichever upstream host serves that model, and the default (`data_collection: "allow"`)
will happily route to a host that logs and trains. For `deepseek-v4-pro` the
first-party endpoint is DeepSeek's own, in the PRC.

Three settings fix that:

```jsonc
"provider": {
  "data_collection": "deny",   // never route to a provider that stores prompts
  "zdr": true,                 // only endpoints on OpenRouter's zero-data-retention list
  "allow_fallbacks": false     // ERROR rather than silently fall back to a
                               // non-compliant provider — a silent downgrade is
                               // exactly the leak you'd never notice
}
```

Verified against `https://openrouter.ai/api/v1/endpoints/zdr` (2026-07-13):

| model | first-party endpoint zero-retention? | effect of `zdr: true` |
|---|---|---|
| `deepseek/deepseek-v4-pro` | **No** (DeepSeek, PRC) | **routes around it** — 11 Western ZDR hosts (Fireworks, DeepInfra, BaseTen, Novita, …) |
| `z-ai/glm-5.2` | Yes | 20 ZDR endpoints |
| `moonshotai/kimi-k2.6` | Yes | 17 ZDR endpoints |

**The enforcement is real, not decorative.** Pinning DeepSeek's own PRC endpoint while
requiring ZDR is refused outright ("No endpoints found") rather than being silently
served. And a live request with ZDR on was served by **Novita**, not DeepSeek.

Configured in `~/.config/opencode/opencode.jsonc`. **Also set it account-wide** at
<https://openrouter.ai/settings/privacy> — the account setting is the real guarantee,
because it cannot be bypassed by a harness that ignores per-request provider options.

## What this means for routing

`data_ok = false` on the OpenRouter rows is **not** "these are the dodgy ones." Given
that no provider here has a DPA, it is a conservative default: keep the client's
customer data on the smallest number of surfaces, and don't add a new processor
without a documented reason.

The honest position is stronger than the flag: **production customer data should
ideally not enter any of these prompts.** Work from schemas, anonymized fixtures, and
synthetic reproductions. Most tasks that *look* like they need prod data don't —
reviewing a diff, implementing a feature, refining a ticket. See the `prod-data`
definition in `/pwc-find-work`: it means **customer records will literally enter the
prompt**, not "this code runs near a database."

To change a model's clearance, put it in the overlay *with the basis*:

```
# in ~/.config/pwc/model-table.json, under "overlay":
"opencode/glm-5.2": {"data_ok": true, "note": "why — the documented basis, dated"}
```

## Local settings to check (not managed by PWC)

- **Claude**: claude.ai → Settings → Privacy → turn **Model Improvement off**
  (5-year → 30-day retention). Also `export CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1`
  and `export DISABLE_FEEDBACK_COMMAND=1` — feedback survives the training opt-out.
- **Codex**: ChatGPT → Settings → Data Controls → "Improve the model for everyone"
  **off**, *and* the separate Codex full-environment training toggle. The first does
  not cover the second.
- **OpenRouter**: <https://openrouter.ai/settings/privacy> → data collection **deny**.
  Never enable the 1%-discount logging option (it grants a perpetual, irrevocable
  commercial licence to your prompts).

## Sources

- [Anthropic — Updates to Consumer Terms](https://www.anthropic.com/news/updates-to-our-consumer-terms) ·
  [Is my data used for training?](https://privacy.claude.com/en/articles/10023580-is-my-data-used-for-model-training) ·
  [Retention](https://privacy.claude.com/en/articles/10023548-how-long-do-you-store-personal-data) ·
  [Claude Code data usage](https://code.claude.com/docs/en/data-usage)
- [OpenAI — How your data is used to improve model performance](https://help.openai.com/en/articles/5722486-how-your-data-is-used-to-improve-model-performance) ·
  [Using Codex with your ChatGPT plan](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan)
- [OpenRouter — Privacy](https://openrouter.ai/docs/guides/privacy) ·
  [Zero Data Retention](https://openrouter.ai/docs/guides/features/zdr) ·
  [Provider routing](https://openrouter.ai/docs/guides/routing/provider-selection) ·
  [ZDR endpoint list](https://openrouter.ai/api/v1/endpoints/zdr)
- [DeepSeek Privacy Policy](https://cdn.deepseek.com/policies/en-US/deepseek-privacy-policy.html) ·
  [Z.AI](https://docs.z.ai/legal-agreement/privacy-policy) ·
  [Kimi ToS](https://platform.kimi.ai/docs/agreement/modeluse)

**Caveat on sources:** OpenAI blocks automated fetching, so its help-centre pages were
read via the Internet Archive (June 2026 snapshots). Re-confirm in a browser before
relying on the Codex details. Z.AI's no-training claim rests on a DPA clause whose
applicability to a self-serve OpenRouter-routed user is **unverified** — treat it as
weaker than Moonshot's explicit (and unfavourable) statement.
