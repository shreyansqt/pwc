# Model benchmarks for the PWC router (collected 2026-10-13)

## How to read this file

- A cell shows "value (I)" or "value (V)" and a footnote number in square brackets.
- **(I)** = independent source (Artificial Analysis, Vals AI, Scale SWE-bench Pro leaderboard, ARC Prize, Cursor's CursorBench, Bug Hunt Bench, ReviewBench, Collinear, Proximal).
- **(V)** = vendor source (the model maker's own announcement, system card or model card).
- An empty cell (—) means I found no number. I did not guess or interpolate any value.
- Effort setting is in the row label. A cell that used a different effort names that effort in the cell.
- "Relayed" footnotes: I read the number on a BenchLM aggregator page (benchlm.ai, updated 2026-10-13). BenchLM names the original source, and the footnote gives that original URL. I did not open the original page for these numbers. For relayed Artificial Analysis numbers, BenchLM does not state the effort setting.
- Artificial Analysis (AA) numbers come from the AA Intelligence Index **v4.3.2**. Its ten parts are AA-Briefcase v1.1, GDPval-AA v2.1, AutomationBench-AA, Terminal-Bench 4.0, SciCode, Humanity's Last Exam, GDP.pdf, CritPt, AA-Omniscience and AA-LCR v1.1. AA rounds to whole numbers on comparison pages.

### Important caveats

- **Antigravity models (Opus 4.6, Sonnet 4.6):** AA shows them only as "Non-reasoning, High", and marks the index value as *estimated* ("independent evaluation forthcoming"). The Antigravity CLI may serve a thinking variant. So their AA numbers probably understate them.
- **gpt-5.5:** AA lists two variants. "GPT-5.5 (xhigh)" scores 38. "GPT-5.5 Instant (June 2026)" scores 26. I used xhigh, because Codex serves the reasoning model.
- **Grok 4.7 Fast / grok-4.7-build-fast:** I found no benchmark of any kind, independent or vendor. One secondary blog says the Fast variant is about twice as fast and is limited to Cursor and Grok Build.
- **Conflicts I resolved:** BenchLM lists an AA Intelligence Index of 58.9 for GPT-5.6 Sol and attributes it to OpenAI. The AA page itself shows 47 (Max). I used 47. A Vellum blog claims AA scores of 61.2 (GPT-6 Astra) and 65.7 (Fable 5.1). The AA pages show 53 and 53. I used the AA pages.
- **SWE-bench Verified is archived.** Vals AI calls it archived. No current-generation model (Fable 5.1, Opus 5.5, Sonnet 5.5, any GPT-6) has an independent Verified score.
- **Aider Polyglot:** I found no 2026 result for any listed model.
- **LMArena** (arena.ai) blocked my fetch (HTTP 403). So the file has no human-preference writing scores.

---

## Table 1. Agentic coding (implementation)

Columns:
- **AA-II** = AA Intelligence Index v4.3.2 (composite; 25% agentic, 25% coding).
- **AA TB4** = Terminal-Bench 4.0 (66 tasks), run by AA with mini-swe-agent, pass@1 over 3 repeats.
- **TB vendor** = Terminal-Bench as the vendor reports it (version in cell).
- **TB2.1 Vals** = Terminal-Bench 2.1 run by Vals AI.
- **SWE-V** = SWE-bench Verified (Vals AI, mini-swe-agent unless noted; or vendor).
- **SWE-Pro** = SWE-bench Pro (Scale public leaderboard = I; vendor = V).
- **Cursor 4.0** = CursorBench 4.0 (Cursor, released 2026-09-10, page updated 2026-10-07).
- **AA Coding** = AA Coding Index (relayed, effort not stated).
- **LCB Vals** = LiveCodeBench run by Vals AI (relayed).
- **FrontierSWE** = Proximal FrontierSWE v2 (relayed). **DeepSWE** = DeepSWE (vendor unless noted).

| Model (effort) | AA-II | AA TB4 | TB vendor | TB2.1 Vals | SWE-V | SWE-Pro | Cursor 4.0 | AA Coding | LCB Vals | FrontierSWE v2 | DeepSWE |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Claude Fable 5.1 (Max) | 53 (I) [3] | 52 (I) [3] | TB4.0: 55.8 (V) [18] | 85.0 (I) [31] | — | 81.2 (V) [20] | 51.8 (I) [15] | 81.6 (I) [32] | 90.5 (I) [31] | 56.3 (I) [35] | 67.4 (V) [20] |
| Claude Opus 5.5 (Max) | 58 (I) [1]; xhigh 56 (I) [10] | 59.6 (I) [12]; xhigh 59.6 (I) [12] | TB4.0 xhigh: 66.4 (V) [18] | — | — | 89.9 (V) [19] | 57.8 (I) [15]; high 56.0 (I) [15] | — | — | 62.3 (I) [35] | 74.2 (V) [19] |
| Claude Sonnet 5.5 (Max) | 56 (I) [2] | 63.6 (I) [12] | TB4.0: 70.6 (V) [21] | — | — | 81.3 (V) [21] | 55.5 (I) [15]; xhigh 53.1 (I) [15] | — | — | 61.9 (I) [35] | 71.0 (V) [21] |
| Claude Opus 4.6 (AA: Non-reasoning High, estimated) | 26* (I) [7] | — | TB2.0: 65.4 (V) [26] | — | Thinking: 78.2 (I) [13]; 80.8 (V) [26] | Thinking: 51.9 (I) [14] | — | — | — | — | — |
| Claude Sonnet 4.6 (AA: Non-reasoning High, estimated) | 25* (I) [7] | — | TB2.0: 59.1 (V) [27] | 57.3 (I) [31] | 77.4 (I) [13]; 79.6 (V) [27] | — | — | — | 82.1 (I) [31] | — | — |
| gpt-6-astra (Max) | 53 (I) [1] | 59 (I) [1] | TB4.0 high: 57.9 (V) [18] | 87.3 (I) [31] | — | — | — | 76.9 (I) [32] | — | 65.5 (I) [35] | 74.1 (V) [22] |
| gpt-6.1-sol (Max) | 52 (I) [3] | 56 (I) [3] | — | — | — | — | — | — | — | — | 71.9 (V) [36] |
| gpt-6-sol (Max) | 48 (I) [4] | 44 (I) [4] | — | — | — | — | — | — | — | — | 68.8 (V) [36] |
| gpt-6-luna (Max) | 38 (I) [5] | 13 (I) [5] | — | — | — | — | — | — | — | — | 66.6 (V) [36] |
| gpt-5.6-sol (Max) | 47 (I) [5] | 40 (I) [5] | TB4.0: 37.3 (V) [18]; TB2.1: 91.9 (V) [23] | 85.8 (I) [31] | 96.2 (I) [13] | 64.6 (V) [23] | 41.7 (I) [15] | 77.4 (I) [32] | 82.6 (I) [31] | 32.2 (I) [35] | 72.7 (V) [23] |
| gpt-5.5 (xhigh) | 38 (I) [9] | 15 (I) [9] | TB2.0: 82 (I, official leaderboard) [37] | 76.4 (I) [31] | 82.6 (I) [13]; Codex harness 76.4 (I) [13] | 58.6 (V) [24] | — | 74.9 (I) [32] | 85.3 (I) [31] | — | — |
| gpt-oss-120b (High) | 12 (I) [8] | 0 (I) [8] | — | — | 33.6 (I) [13]; 62.4 (V) [25] | 16.2 (I) [14] | — | 30.4 (I) [32] | — | — | — |
| grok-4.7 (xhigh) | 46 (I) [2]; high 46, low 42 (I) [10] | 26 (I) [2] | TB4.0: 38.0 (V) [28] | 73.4 (I) [31] | — | — | 46.3 (I) [15] | — | — | 29.5 (I) [35] | 71.0 (V) [28] |
| grok-4.7-build-fast | — | — | — | — | — | — | — | — | — | — | — |
| grok-4.6 (High) | 44 (I) [6] | 21 (I) [6] | — | 78.3 (I) [31] | 95.6 (I) [13] | — | xhigh 41.4 (I) [15]; high 40.4 (I) [15] | 76.8 (I) [32] | 88.2 (I) [31] | 25.3 (I) [35] | 65.9 (V) [28] |
| grok-4.5 (High) | 39 (I) [6] | 11 (I) [6] | TB2.1: 83.3 (V) [29] | 67.8 (I) [31] | 86.6 (I) [13] | 64.7 (V) [29] | — | 72.5 (I) [32] | 87.4 (I) [31] | — | 53 (V) [29] |
| gemini-3.8-flash (High) | 41 (I) [10] | 20 (I) [4] | TB4.0: 19.1 (V) [30] | 81.3 (I) [31] | 80.0 (I) [13] | — | 39.6 (I) [15] | 76.3 (I) [32] | 89.5 (I) [31] | 19.6 (I) [35] | 73.8 (I) [39] |
| gemini-3.8-flash (Medium) | 40 (I) [10] | — | — | — | — | — | 37.3 (I) [15] | — | — | — | — |
| gemini-3.8-flash (Low) | 33 (I) [10] | — | — | — | — | — | — | — | — | — | — |
| gemini-3.1-pro-preview (reference) | 30 (I) [8] | 4 (I) [8] | — | 70.8 (I) [31] | 78.8 (I) [13] | Thinking: 46.1 (I) [14] | — | 68.8 (I) [32] | 88.5 (I) [31] | — | — |

Extra: AA "Terminal-Bench Hard" (older AA variant): gpt-5.6-sol Max 65.9 (I), Medium 62.9 (I) [38].

## Table 2. Reasoning and knowledge

- **AA-GPQA** = GPQA Diamond run by AA. **Vals GPQA** = GPQA Diamond run by Vals AI (relayed).
- **AA-HLE** = Humanity's Last Exam run by AA (no tools).
- **ARC-AGI-2 / ARC-AGI-3** = ARC Prize verified results (relayed) unless marked V.
- **CritPt** = AA physics research-reasoning test.

| Model (effort) | AA-GPQA | Vals GPQA | GPQA vendor | AA-HLE | HLE vendor | ARC-AGI-2 | ARC-AGI-3 | CritPt |
|---|---|---|---|---|---|---|---|---|
| Claude Fable 5.1 (Max) | 93.7 (I) [32] | 93.4 (I) [31] | — | 59 (I) [3] | no tools 60.9; with tools 65.6 (V) [20][18] | 90 (V) [20] | — | 30 (I) [3] |
| Claude Opus 5.5 (Max) | — | — | — | 61 (I) [1] | no tools 64.4 (V) [19]; with tools 67.7 (V) [18] | 91.7 (I) [33] | — | 32 (I) [1] |
| Claude Sonnet 5.5 (Max) | — | — | — | 55 (I) [2] | no tools 56.9; with tools 64.5 (V) [21] | — | — | 31 (I) [2] |
| Claude Opus 4.6 (Non-reasoning High) | 84.0 (I) [32] | — | 91.3 (V) [26] | 19 (I) [7] | 53 (V, tool use not stated) [26] | — | — | 3 (I) [7] |
| Claude Sonnet 4.6 (Non-reasoning High) | 79.9 (I) [32] | 85.6 (I) [31] | 89.9 (V) [27] | 13 (I) [7] | 49 (V) [27] | Max: 58.3 (I) [33] | — | 1 (I) [7] |
| gpt-6-astra (Max) | 96.1 (I) [11]; xhigh 96.3 (I) [11] | — | 96.0 (V) [22] | 55 (I) [1] | with tools 57.2 (V) [18] | 95 (I) [33] | 62.7 (I) [33] | 32 (I) [1] |
| gpt-6.1-sol (Max) | — | — | — | 53 (I) [3] | — | 94.2 (I) [33] | 52.7 (I) [33] | 32 (I) [3] |
| gpt-6-sol (Max) | — | — | — | 48 (I) [4] | — | 89.6 (I) [33] | 4.6 (I) [33] | 31 (I) [4] |
| gpt-6-luna (Max) | — | — | — | 39 (I) [5] | — | 59.3 (I) [33] | 0.1 (I) [33] | 19 (I) [5] |
| gpt-5.6-sol (Max) | 94.1 (I) [32] | 95.2 (I) [31] | 94.6 (V) [23] | 49 (I) [5] | — | 92.5 (I) [33] | 7.8 (I) [33] | 32 (I) [5] |
| gpt-5.5 (xhigh) | 93.5 (I) [32] | 93.2 (I) [31] | 93.6 (V) [24] | 46 (I) [9] | no tools 41.4; with tools 52.2 (V) [24] | 85 (V) [24] | 0.4 (I) [33] | 27 (I) [9] |
| gpt-oss-120b (High) | 78.2 (I) [32] | — | 80.1 (V) [25] | 20 (I) [8] | 14.9 (V) [25] | — | — | 1 (I) [8] |
| grok-4.7 (xhigh) | — | — | — | 43 (I) [2] | — | — | — | 18 (I) [2] |
| grok-4.7-build-fast | — | — | — | — | — | — | — | — |
| grok-4.6 (High) | 94.9 (I) [32] | 94.7 (I) [31] | — | 43 (I) [6] | — | 67.1 (I) [33] | 2.1 (I) [33] | 17 (I) [6] |
| grok-4.5 (High) | 93.1 (I) [32] | 92.9 (I) [31] | — | 43 (I) [6] | — | 52.6 (I) [33] | 0.3 (I) [33] | 15 (I) [6] |
| gemini-3.8-flash (High) | 95.3 (I) [11] | 94.4 (I) [31] | — | 48 (I) [4] | HLE-Verified 54.9 (V) [30] | 89.2 (I) [33] | 10.4 (I) [33] | 18 (I) [4] |
| gemini-3.1-pro-preview | 94.1 (I) [32] | 95.5 (I) [31] | — | 47 (I) [8] | — | 77.1 (V) [40] | 0.4 (I) [33] | 18 (I) [8] |

## Table 3. Long context, instruction following, and work output

- **AA-LCR** = AA Long Context Reasoning v1.1.
- **MRCR v2** = OpenAI multi-round coreference test (vendor only; context band in cell).
- **AA-IFBench** = IFBench run by AA (relayed). AA no longer includes IFBench in the index, so current models have no value.
- **GDPval-AA** = AA's run of GDPval v2.1, an Elo rating on real professional work products (documents, analyses).
- **Briefcase** = AA-Briefcase v1.1, an Elo rating on knowledge-work tasks.
- **Omniscience** = AA-Omniscience index (knowledge accuracy minus hallucination penalty; range -100 to 100).

| Model (effort) | AA-LCR | MRCR v2 | AA-IFBench | GDPval-AA (Elo) | Briefcase (Elo) | Omniscience |
|---|---|---|---|---|---|---|
| Claude Fable 5.1 (Max) | 85 (I) [3] | — | — | 1756 (I) [3] | 1675 (I) [3] | 43 (I) [3] |
| Claude Opus 5.5 (Max) | 85 (I) [1] | — | — | 1866 (I) [1] | 1807 (I) [1] | 46 (I) [1] |
| Claude Sonnet 5.5 (Max) | 83 (I) [2] | — | — | 1838 (I) [2] | 1823 (I) [2] | 32 (I) [2] |
| Claude Opus 4.6 (Non-reasoning High) | 67 (I) [7] | — | 44.6 (I) [32] | — | — | 2 (I) [7] |
| Claude Sonnet 4.6 (Non-reasoning High) | 68 (I) [7] | — | 41.2 (I) [32] | — | — | -4 (I) [7] |
| gpt-6-astra (Max) | 81 (I) [1] | 256K-512K: 100.0; 512K-1M: 96.3 (V) [22] | — | 1572 (I) [1] | 1571 (I) [1] | 43 (I) [1] |
| gpt-6.1-sol (Max) | 83 (I) [3] | — | — | 1593 (I) [3] | 1561 (I) [3] | 42 (I) [3] |
| gpt-6-sol (Max) | 84 (I) [4] | — | — | 1508 (I) [4] | 1480 (I) [4] | 27 (I) [4] |
| gpt-6-luna (Max) | 83 (I) [5] | — | — | 1430 (I) [5] | 1336 (I) [5] | 1 (I) [5] |
| gpt-5.6-sol (Max) | 84 (I) [5] | — | 72.7 (I) [32] | 1609 (I) [5] | 1480 (I) [5] | 22 (I) [5] |
| gpt-5.5 (xhigh) | 84 (I) [9] | 64K-128K: 83.1; 128K-256K: 87.5 (V) [24] | 75.9 (I) [32] | 1350 (I) [9] | 1137 (I) [9] | 21 (I) [9] |
| gpt-oss-120b (High) | 52 (I) [8] | — | 69.0 (I) [32] | 608 (I) [8] | 0 (I) [8] | -49 (I) [8] |
| grok-4.7 (xhigh) | 77 (I) [2] | — | — | 1712 (I) [2] | 1644 (I) [2] | 32 (I) [2] |
| grok-4.7-build-fast | — | — | — | — | — | — |
| grok-4.6 (High) | 80 (I) [6] | — | — | 1619 (I) [6] | 1535 (I) [6] | 30 (I) [6] |
| grok-4.5 (High) | 79 (I) [6] | — | — | 1386 (I) [6] | 1281 (I) [6] | 25 (I) [6] |
| gemini-3.8-flash (High) | 81 (I) [4] | — | — | 1436 (I) [4] | 1203 (I) [4] | 30 (I) [4] |
| gemini-3.1-pro-preview | 82 (I) [8] | — | 77.1 (I) [32] | 791 (I) [8] | 456 (I) [8] | 32 (I) [8] |

## Table 4. Code review and bug finding

No code-review benchmark covers all models. Three tests come closest:
- **ReviewBench** (GitHub, 219 pull requests, judged by Claude Sonnet 5): grounded F1 of review comments. It lists only OpenAI models run through Codex, plus commercial review products whose models are not disclosed.
- **Bug Hunt Bench**: 105 bugs planted in two real repositories; the score counts planted bugs fixed (out of 105). Each model runs in its own agent CLI. The board warns that 3-run spreads reach 14 points, so the top rows cannot be ordered with confidence.
- **CWE-bench v1** (Collinear): security-weakness detection (relayed).

| Model | ReviewBench F1 | Bug Hunt Bench (fixes / 105) | CWE-bench v1 |
|---|---|---|---|
| Claude Fable 5.1 | — | Max: 43 (I, single run) [16] | 58.0 (I) [34] |
| Claude Opus 5.5 | — | Max: 41.7; xhigh: 36; high: 31.7 (I, mean of 3) [16] | 67.0 (I) [34] |
| Claude Sonnet 5.5 | — | Max: 51.3 (runs 57/54/43); xhigh: 36.0 (I) [16] | — |
| Claude Opus 4.6 | — | — | — |
| Claude Sonnet 4.6 | — | — | — |
| gpt-6-astra | — | Max: 45.0 (I, mean of 3); xhigh: 43 (I, single run) [16] | 68.0 (I) [34] |
| gpt-6.1-sol | — | Max: 44.3; xhigh: 42.7; high: 36.5 (I) [16] | — |
| gpt-6-sol | — | Max: 29.3; high: 20 (I) [16] | 52.0 (I) [34] |
| gpt-6-luna | — | 18.3 (I, effort not stated, relayed) [16] | — |
| gpt-5.6-sol | Max: 30.4; xhigh: 29.5; high: 26.8 (I) [17] | Max: 43.5 (mean of 2); xhigh: 39 (I) [16] | — |
| gpt-5.5 | xhigh: 18.8; high: 16.1 (I) [17] | — | — |
| gpt-oss-120b | — | — | — |
| grok-4.7 | — | 28.8 (I, effort not stated, relayed) [16] | 68.0 (I) [34] |
| grok-4.7-build-fast | — | — | — |
| grok-4.6 | — | 27.0 (I, effort not stated, relayed) [16] | 57.0 (I) [34] |
| grok-4.5 | — | — | — |
| gemini-3.8-flash | — | 18.0 (I, effort not stated, relayed) [16] | — |
| gemini-3.1-pro-preview | — | — | — |

ReviewBench context: the best entry overall is Copilot Code Review (Balanced) at F1 40.1. Codex with GPT-5.6 Sol (Max) ranks 4th of 28.

---

## Footnotes

All pages were retrieved on 2026-10-13. "Direct" means I read the page myself. "Relayed" means I read the number on the BenchLM page named in brackets (updated 2026-10-13).

1. AA comparison, Claude Opus 5.5 (Max, Default Fallback) vs GPT-6 Astra (Max). https://artificialanalysis.ai/models/comparisons/claude-opus-5-5-vs-gpt-6-astra — direct.
2. AA comparison, Claude Sonnet 5.5 (Max, Default Fallback) vs Grok 4.7 (xhigh). https://artificialanalysis.ai/models/comparisons/claude-sonnet-5-5-vs-grok-4-7 — direct.
3. AA comparison, Claude Fable 5.1 (Max, Default Fallback) vs GPT-6.1 Sol (Max). https://artificialanalysis.ai/models/comparisons/claude-fable-5-1-vs-gpt-6-1-sol — direct.
4. AA comparison, GPT-6 Sol (Max) vs Gemini 3.8 Flash (High). https://artificialanalysis.ai/models/comparisons/gpt-6-sol-vs-gemini-3-8-flash — direct.
5. AA comparison, GPT-6 Luna (Max) vs GPT-5.6 Sol (Max). https://artificialanalysis.ai/models/comparisons/gpt-6-luna-vs-gpt-5-6-sol — direct.
6. AA comparison, Grok 4.6 (High) vs Grok 4.5 (High). https://artificialanalysis.ai/models/comparisons/grok-4-6-vs-grok-4-5 — direct.
7. AA comparison, Claude Opus 4.6 vs Claude Sonnet 4.6 (both Non-reasoning, High; index marked estimated). https://artificialanalysis.ai/models/comparisons/claude-opus-4-6-vs-claude-sonnet-4-6 — direct.
8. AA comparison, Gemini 3.1 Pro Preview vs gpt-oss-120b (High). https://artificialanalysis.ai/models/comparisons/gemini-3-1-pro-preview-vs-gpt-oss-120b — direct.
9. AA comparison, GPT-5.5 (xhigh) vs GPT-6 Astra (Max). https://artificialanalysis.ai/models/comparisons/gpt-5-5-vs-gpt-6-astra — direct.
10. AA model leaderboard (Intelligence Index for every effort setting). https://artificialanalysis.ai/leaderboards/models — direct.
11. AA GPQA Diamond leaderboard. https://artificialanalysis.ai/evaluations/gpqa-diamond — direct.
12. AA Terminal-Bench 4.0 leaderboard (66 tasks, mini-swe-agent, pass@1 over 3 repeats). https://artificialanalysis.ai/evaluations/terminalbench-4-0 — direct.
13. Vals AI SWE-bench Verified leaderboard (archived benchmark, mainly mini-swe-agent; page updated 2026-09-01). https://www.vals.ai/benchmarks/swebench — direct.
14. Scale AI SWE-bench Pro public leaderboard (mini-swe-agent; no dates on page). https://labs.scale.com/leaderboard/swe_bench_pro_public — direct.
15. Cursor CursorBench 4.0 (set released 2026-09-10; page updated 2026-10-07). https://cursor.com/cursorbench — direct.
16. Bug Hunt Bench public data (run dates 2026-08-28 to 2026-09-30 for the rows with dates). https://bughunt.productcompass.pm/data/benchmark.json — direct for Claude and GPT rows; relayed for gpt-6-luna, grok-4.7, grok-4.6 and gemini-3.8-flash.
17. ReviewBench leaderboard data (snapshot dates 2026-06-29 to 2026-10-08). https://review-bench.ai/api/leaderboard — direct; per-row F1 from https://benchlm.ai/md/benchmarks/reviewbench.md.
18. VENDOR. Anthropic, "Claude Opus 5.5" announcement, 2026-09-22. https://www.anthropic.com/news/claude-opus-5-5 — direct. Opus 5.5 uses max effort, except Terminal-Bench 4.0 at xhigh. The table also gives Fable 5.1 (TB4.0 55.8, HLE with tools 65.6), GPT-6 Astra (TB4.0 57.9 at high, HLE with tools 57.2) and GPT-5.6 Sol (TB4.0 37.3), "as reported by OpenAI".
19. VENDOR. Anthropic, Claude Opus 5.5 System Card. https://www-cdn.anthropic.com/fc1b44717c85dc068bc6ba5024219938094694bd/Claude%20Opus%205.5%20System%20Card.pdf — relayed [benchlm.ai/md/models/claude-opus-5-5.md].
20. VENDOR. Anthropic, Claude Fable 5.1 and Mythos 5.1 System Card. https://www-cdn.anthropic.com/0339e6a7c5c7b87f5c07798616dc32c215d14235/Claude%20Fable%205.1%20&%20Claude%20Mythos%205.1%20System%20Card.pdf — relayed [benchlm.ai/md/models/claude-fable-5-1.md].
21. VENDOR. Anthropic, Claude Sonnet 5.5 announcement (https://www.anthropic.com/claude-sonnet-5-5) and system card (https://www-cdn.anthropic.com/870c8f525702625d2c62fc6dd04c857e3250bec1/Claude%20Sonnet%205.5%20System%20Card.pdf) — relayed [benchlm.ai/md/models/claude-sonnet-5-5.md].
22. VENDOR. OpenAI, GPT-6 Astra announcement (released 2026-09-03 per secondary coverage). https://openai.com/index/gpt-6-astra/ — relayed [benchlm.ai/md/models/gpt-6-astra.md].
23. VENDOR. OpenAI, GPT-5.6. https://openai.com/index/gpt-5-6/ — relayed [benchlm.ai/md/models/gpt-5-6-sol.md].
24. VENDOR. OpenAI, Introducing GPT-5.5. https://openai.com/index/introducing-gpt-5-5/ — relayed [benchlm.ai/md/models/gpt-5-5.md].
25. VENDOR. OpenAI, gpt-oss model card. https://cdn.openai.com/pdf/419b6906-9da6-406c-a19d-1bb078ac7637/oai_gpt-oss_model_card.pdf — relayed [benchlm.ai/md/models/gpt-oss-120b.md].
26. VENDOR. Anthropic, Claude Opus 4.6 system card. https://www-cdn.anthropic.com/14e4fb01875d2a69f646fa5e574dea2b1c0ff7b5.pdf — relayed [benchlm.ai/md/models/claude-opus-4-6.md].
27. VENDOR. Anthropic, Claude Sonnet 4.6 system card. https://www-cdn.anthropic.com/78073f739564e986ff3e28522761a7a0b4484f84.pdf — relayed [benchlm.ai/md/models/claude-sonnet-4-6.md].
28. VENDOR. xAI, Grok 4.7 launch post (launch 2026-09-21 per secondary coverage; one secondary table shows 37.6 for TB4.0 instead of 38.0). https://x.ai/news/grok-4-7 ; Grok 4.6 DeepSWE from https://x.ai/news/grok-4-6 — relayed [benchlm.ai/md/models/grok-4-7.md, grok-4-6.md].
29. VENDOR. xAI, Grok 4.5 launch post. https://x.ai/news/grok-4-5 — relayed [benchlm.ai/md/models/grok-4-5.md].
30. VENDOR. Google DeepMind, Gemini 3.8 Flash model card. https://deepmind.google/models/model-cards/gemini-3-8-flash/ — relayed [benchlm.ai/md/models/gemini-3-8-flash.md].
31. INDEPENDENT. Vals AI model pages (Terminal-Bench 2.1, LiveCodeBench, GPQA Diamond) — relayed. URLs: https://www.vals.ai/models/anthropic_claude-fable-5-1 · https://www.vals.ai/models/anthropic_claude-sonnet-4-6 · https://www.vals.ai/models/openai_gpt-6-astra · https://www.vals.ai/models/openai_gpt-5.6-sol · https://www.vals.ai/models/openai_gpt-5.5 · https://www.vals.ai/models/grok_grok-4.7 · https://www.vals.ai/models/grok_grok-4.6 · https://www.vals.ai/models/grok_grok-4.5 · https://www.vals.ai/models/google_gemini-3.8-flash · https://www.vals.ai/models/google_gemini-3.1-pro-preview
32. INDEPENDENT. AA model pages (Coding Index, GPQA Diamond, IFBench), effort not stated — relayed. URLs: https://artificialanalysis.ai/models/claude-fable-5-1 · /models/gpt-6-astra · /models/gpt-5-6-sol · /models/gpt-5-5 · /models/gpt-oss-120b · /models/claude-opus-4-6 · /models/claude-sonnet-4-6 · /models/grok-4-6 · /models/grok-4-5 · /models/gemini-3-8-flash · /models/gemini-3-1-pro-preview
33. INDEPENDENT. ARC Prize verified results — relayed. URLs: https://arcprize.org/results/anthropic-claude-opus-5-5 · /results/openai-gpt-6-astra · /results/openai-gpt-6-1-sol · /results/openai-gpt-6-sol (Max) · /results/openai-gpt-6-luna · /results/openai-gpt-5-6 · /results/xai-grok-4-6 · /results/xai-grok-4-5 · /results/google-gemini-3-8-flash · https://arcprize.org/leaderboard (Sonnet 4.6 Max, GPT-5.5, Grok 4.5 ARC-AGI-2/3, Gemini 3.1 Pro ARC-AGI-3)
34. INDEPENDENT. Collinear CWE-bench v1. https://cwe-bench.com/ — relayed.
35. INDEPENDENT. Proximal FrontierSWE v2. https://www.frontierswe.com/ — relayed.
36. VENDOR. OpenAI, Introducing GPT-6.1 Sol (https://openai.com/index/introducing-gpt-6-1-sol/) and Introducing GPT-6 Sol and Luna (https://openai.com/index/introducing-gpt-6-sol-and-luna/) — relayed.
37. INDEPENDENT. Terminal-Bench 2.0 official leaderboard. https://www.tbench.ai/leaderboard/terminal-bench/2.0 — relayed. (The live tbench.ai page now shows only Terminal-Bench 4.0, and its rows load dynamically, so I could not read them.)
38. INDEPENDENT. AA Terminal-Bench Hard. https://artificialanalysis.ai/evaluations/terminalbench-hard — direct.
39. INDEPENDENT. DeepSWE v1.1 leaderboard (Datacurve). https://deepswe.datacurve.ai/ — relayed.
40. VENDOR. Google, Gemini 3.1 Pro blog. https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-1-pro/ — relayed.

---

## Ranking by domain

Each ranking uses groups, because small score gaps are inside the noise of most of these tests.

### Implementation (agentic coding)

Main evidence: AA Terminal-Bench 4.0 (one harness, one version, 15 of 18 models), CursorBench 4.0, AA Intelligence Index. Vendor SWE-bench Pro and Terminal-Bench numbers agree with the order.

1. **Top:** Claude Opus 5.5, Claude Sonnet 5.5, gpt-6-astra. AA TB4 60 / 64 / 59. Opus 5.5 and Sonnet 5.5 lead CursorBench 4.0 (57.8, 55.5). Astra is not on CursorBench.
2. **Strong:** gpt-6.1-sol (AA TB4 56), Claude Fable 5.1 (AA TB4 52, CursorBench 51.8, AA Coding Index 81.6, the highest relayed Coding Index).
3. **Mid:** gpt-6-sol (AA TB4 44), gpt-5.6-sol (40; CursorBench 41.7), grok-4.7 xhigh (26; CursorBench 46.3).
4. **Lower:** grok-4.6 (21), gemini-3.8-flash high (20), gpt-5.5 xhigh (15), gpt-6-luna (13), grok-4.5 (11).
5. **Weakest:** gemini-3.1-pro-preview (4), gpt-oss-120b (0; Scale SWE-bench Pro 16.2).

Not placeable on the same scale: Claude Opus 4.6 and Sonnet 4.6. Their only independent agentic coding numbers are older tests (Scale SWE-bench Pro: Opus 4.6 51.9, above Gemini 3.1 Pro 46.1; Vals SWE-bench Verified 78.2 / 77.4). They are roughly level with Gemini 3.1 Pro on those older tests. That suggests the lower groups, but it is an inference across benchmark generations.

**Evidence strength: strong** for the top three groups. Terminal-Bench 4.0 and CursorBench 4.0 are recent, independent and agree. **Weak** for grok-4.7-build-fast (no data) and the Antigravity Claude models.

### Code review

Main evidence: Bug Hunt Bench (finding and fixing hidden bugs), CWE-bench v1, ReviewBench (OpenAI models only). No benchmark covers code review across all vendors.

1. **Top cluster (cannot be ordered):** Claude Sonnet 5.5 (51.3), gpt-6-astra (45.0), gpt-6.1-sol (44.3), gpt-5.6-sol (43.5), Claude Fable 5.1 (43), Claude Opus 5.5 (41.7). All at Max effort. The board says spreads up to 14 points make this order unreliable. On CWE-bench, Astra (68), Opus 5.5 (67) lead Fable 5.1 (58).
2. **Mid:** gpt-6-sol (29.3; CWE 52), grok-4.7 (28.8; CWE 68), grok-4.6 (27.0; CWE 57).
3. **Lower:** gpt-6-luna (18.3), gemini-3.8-flash (18.0). On ReviewBench, gpt-5.5 xhigh (18.8) is well below gpt-5.6-sol Max (30.4).
4. **No code-review data:** Opus 4.6, Sonnet 4.6, gpt-oss-120b, grok-4.5, grok-4.7-build-fast, gemini-3.1-pro-preview. Use their implementation rank as a proxy.

Effort matters a lot here. Opus 5.5 drops from 41.7 (Max) to 31.7 (high). gpt-5.6-sol drops from 30.4 to 26.8 ReviewBench F1.

**Evidence strength: weak to moderate.** Bug Hunt Bench is small (105 bugs, 1–3 runs). ReviewBench excludes Claude, Grok and Gemini.

### Research-writing (research memos, long reasoning, writing)

Main evidence: AA Intelligence Index, AA-HLE, GDPval-AA and AA-Briefcase (Elo ratings on real professional documents), AA-LCR, AA-Omniscience. All from one independent source on one version.

1. **Top:** Claude Opus 5.5 (index 58, HLE 61, GDPval 1866, Briefcase 1807, LCR 85, Omniscience 46).
2. **Very strong:** Claude Sonnet 5.5 (56, GDPval 1838, Briefcase 1823), Claude Fable 5.1 (53, HLE 59, GDPval 1756, LCR 85, Omniscience 43).
3. **Strong:** gpt-6-astra (53, HLE 55, GDPval 1572, GPQA 96.1, ARC-AGI-2 95), gpt-6.1-sol (52, HLE 53, GDPval 1593).
4. **Mid:** grok-4.7 (46; GDPval 1712, which is high for its index), gpt-6-sol (48), gpt-5.6-sol (47), grok-4.6 (44).
5. **Lower:** gemini-3.8-flash high (41), grok-4.5 (39), gpt-5.5 xhigh (38), gpt-6-luna (38; Omniscience 1, which means many hallucinations).
6. **Weakest:** gemini-3.1-pro-preview (30; GDPval 791), Opus 4.6 and Sonnet 4.6 (26* and 25*, non-reasoning estimates only), gpt-oss-120b (12; Omniscience -49).

**Evidence strength: strong** for reasoning and knowledge-work output. **No direct writing-quality score** (for example, human preference for prose) was available, because the LMArena fetch failed.

### Ops-comms (short messages, instruction following)

Main evidence: weak. IFBench is the only instruction-following test, and AA measured it only for older models: gemini-3.1-pro-preview 77.1, gpt-5.5 75.9, gpt-5.6-sol 72.7, gpt-oss-120b 69.0, Opus 4.6 44.6 and Sonnet 4.6 41.2 (both non-reasoning). No current-generation model has an IFBench or IFEval score. AA-Omniscience (hallucination penalty) is a useful second signal for factual short messages.

Suggested reading of the evidence:
1. Any model in the top three research-writing groups can do this domain. The cost per AA index task differs a lot: gpt-6.1-sol Max $0.72, Opus 5.5 Max $5.98 (AA leaderboard [10]).
2. **Cheap and adequate:** gpt-5.6-sol and gpt-5.5 (good IFBench); gemini-3.8-flash (index 41, Omniscience 30).
3. **Avoid for facts:** gpt-6-luna (Omniscience 1) and gpt-oss-120b (Omniscience -49).

**Evidence strength: weak.** The router tier for this domain rests on proxies, not on a direct test.

---

## Gaps (little or no independent data)

- **grok-4.7-build-fast (Grok 4.7 Fast):** no benchmark at all, vendor or independent.
- **Claude Opus 4.6 and Sonnet 4.6 (Antigravity):** AA shows only "Non-reasoning, High" estimates. No Terminal-Bench 4.0, CursorBench 4.0, GDPval-AA or code-review data. The served variant through Antigravity is unknown.
- **gemini-3.8-flash medium and low:** only the AA index (40, 33) and CursorBench 4.0 medium (37.3). All other numbers are for high effort.
- **gpt-6.1-sol, gpt-6-sol, gpt-6-luna:** no SWE-bench (any), no CursorBench, no GPQA, no LiveCodeBench.
- **gpt-oss-120b:** only AA numbers, Vals SWE-bench Verified and Scale SWE-bench Pro. Nothing on current agentic tests except AA (where it scores 0 on Terminal-Bench 4.0).
- **Code review:** no benchmark covers all vendors. ReviewBench has no Claude, Grok or Gemini entries.
- **Instruction following:** no IFBench or IFEval for any current-generation model.
- **SWE-bench Verified:** archived; no independent score for any current-generation model.
- **Aider Polyglot:** no 2026 data found.
- **MRCR:** vendor numbers only (gpt-6-astra, gpt-5.5).
- **Relayed AA numbers** (Coding Index, GPQA, IFBench): BenchLM does not give the effort setting. A direct read of each AA model page would confirm it.
