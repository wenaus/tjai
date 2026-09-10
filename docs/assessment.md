# AI assessment

The daily AI performance assessment scores a day's human–AI dialog against the rules in the `assessment-system-prompt` entry and writes one entry per day, which the assessment dashboard reads. The assessor is `gpt-5.6-sol`, run through the Codex subscription, a model outside the family that supplies most of the assessed dialog. Action `llm-assessment-gemini`, daily at 02:20 for the previous day, script `scripts/assessment_gemini.py`.

The entry id is `assessment-<date>-<assessor>`, so a change of assessor leaves the previous reader's history under its own name rather than overwriting it: days before 2026-09-06 end in `-gemini`, days from 2026-09-06 in `-sol`. The dashboard plots the assessors named in `ASSESSOR_SUFFIXES` (`tjai_app/views.py`); assessments written before the suffix existed are not plotted.

## Why a day is assessed in parts

A single call over a whole day does not scale. The assessor's yield, scored events per 100K tokens of input, falls as the input grows:

| date | turns | day (est. tokens) | largest session | events | per 100K |
|---|---|---|---|---|---|
| 2026-09-06 | 2048 | 549K | 114K | 34 | 6.2 |
| 2026-09-05 | 1246 | 360K | 131K | 29 | 8.1 |
| 2026-06-04 | 487 | 236K | 132K | 24 | 10.2 |
| 2026-07-12 | 673 | 208K | 73K | 43 | 20.7 |
| 2026-08-20 | 699 | 203K | 81K | 37 | 18.2 |
| 2026-09-04 | 859 | 170K | 53K | 30 | 17.6 |
| 2026-07-10 | 763 | 160K | 77K | 43 | 26.9 |
| 2026-08-31 | 604 | 136K | 62K | 34 | 25.0 |

Days under about 175K tokens yield 13–27 events per 100K; the days over 200K yield 6–10. Tokens are estimated at 2.7 characters per token, the ratio measured on the 2026-09-06 prompt. The largest single session on any day is about 130K tokens, a size the assessor has handled inside whole days at the higher yields, so a session is never split; the day is.

## Preparation

`scripts/dialog_prep.py` fetches the day's turns, splits them by session and packs them into calls. Every dialog turn carries the `session_id` of the client session that recorded it, its host and client, and its entry UUID. A session is classified as one of:

- `session`: a working session.
- `codex`: a Codex research run, one prompt and one report.
- `headless`: a prompt with no assistant turn, written when a cron or a `claude -p` run records its prompt as dialog.
- `replay`: a session whose assistant turns are copies of an earlier session's on the same host (80% or more), written by the recorder when a thread is moved to background. On 2026-09-06 one replay held 285 copied turns, 86K tokens of the day's 550K.
- `assessor`: an assessment call recorded as dialog. Running the assessor through Codex put each call's prompt into the day's record, so on 2026-09-08 seven such sessions held 1.21M of the day's 1.23M estimated tokens and the record carried the previous day's inside the current one. The calls no longer record (`TJAI_DIALOG_TURNS=0` in `call_codex`); the classification covers what was recorded before that.

Assessment reads `session` and `codex`; `headless` and `replay` are dropped and named on the entry. The recorder defects behind the last two were fixed at the source on 2026-09-09 in `computers/common/claude-hooks/record.py`: a transcript whose leading turns repeat ones already posted from this machine is a copied history and those turns are skipped, and a run started with `-p` records nothing. The classification stays for the days recorded before the fix.

Sessions are packed into calls no larger than the call cap: a session over the cap gets a call of its own, the rest are packed first-fit by size up to it. The cap is the sysconfig key `assessment_call_token_cap`, 175000 estimated tokens. With that cap 2026-09-06 is three calls. Because a session is never split, the cap has a floor: the largest session of the day gets a call of its own whatever the cap says, 120K on 2026-09-06.

`scripts/dialog_streams.py <date>` writes the same split as one file per session with a manifest of kind and size, for looking at a day before deciding anything about it.

## Choice of assessor

2026-09-06, the largest day on record at 549K estimated tokens, was assessed three ways over the same sessions. Detection is scored events per 100K tokens of input; generosity is the mean score per event, which with the event count sets the endpoint the dashboard plots.

| assessor | cap | calls | events | per 100K | endpoint | mean |
|---|---|---|---|---|---|---|
| gemini-2.5-pro | 100K | 5 | 129 | 26.2 | +85 | 0.66 |
| gemini-3.1-pro-preview | 175K | 3 | 82 | 16.7 | +42 | 0.51 |
| gpt-5.6-sol | 175K | 3 | 114 | 23.2 | +57 | 0.50 |

Detection under `gemini-3.1-pro-preview` fell as the pack grew, 33 then 28 then 21 events over packs of 422K, 463K and 465K characters. Under `gpt-5.6-sol` it did not: 41, 30, 43 over the same packs. Consolidation to three calls is therefore not what costs detection; the reader is. The two assessors score what they find alike, mean 0.50 against 0.51, so the endpoint difference between them follows from the event count.

`gpt-5.6-sol` scores each event lower than `gemini-2.5-pro` (0.50 against 0.66), which steps the endpoint down at equal event counts. The `sol` marker on the dashboard divides the two.

## Calls and merge

Each call receives the system prompt, a note naming which part of the day it holds and the sessions in it, and those sessions' turns, each session whole and in time order. Each turn's header carries the time, role, host and session, client and model, and the entry UUID, so a scored event can cite the turn it rests on. The call returns the JSON scores and the markdown log for its turns alone.

The raw response of every call is saved to `data/assessment-responses/<date>-gemini-<n>.txt` before it is parsed, with the pack composition in `<date>-gemini-plan.json`. The parser accepts a JSON block without its closing fence; the assessor has omitted it.

The merge is code: scores from all parts sorted in time order with the cumulative recomputed, the Assessment Log assembled from each part's log under a heading naming the part, and the entry's `total_turns` set to the turns in assessed sessions. One further call, fed the merged events and each part's Observations, writes the day's Summary and Observations; if it fails, a code-written summary and the concatenated observations stand in. The entry has the same shape as a single-call assessment, plus `data.parts` and `data.sessions_dropped`.

A failed call is retried once. If some parts fail, the day is written from the parts that succeeded with the missing sessions named under "Not assessed" in the Summary, and the action is marked failed so the gap is visible. If every part fails, nothing is written.

## Dashboard

The assessment dashboard plots the daily endpoint (final cumulative) and integral. Vertical orange markers on both plots mark boundaries in the series; they are a list in `tjai_app/templates/tjai_app/assessment.html`. The marker `RC` between 2026-09-05 and 2026-09-06 divides the single-call assessments from V2: the days before it were scored by a reader whose yield fell with input size, so their endpoints are not comparable with the days after. The same marker divides the assessors: 2026-09-06 onward is scored by `gpt-5.6-sol`, the days before it by Gemini, so endpoints are not comparable across it on either count.

## Recovery and inspection

- `--plan` prints the packs for a date and stops, with no API call.
- `--from-saved` re-parses the saved raw responses for a date instead of calling the API, for a day whose responses arrived whole but failed to parse. A single-call response from before the split (`<date>-gemini.txt`) is read as one part.
- `--no-write` runs everything but the entry write.

`scripts/assessment_backfill.py <start-date>` fills gaps: it walks back from
the date (30 days by default, 90 at most) and enqueues one wrangler worker per
day that has dialog and no assessment, printing the plan and queueing nothing
until `--enqueue`. A day that already has an assessment is left alone, so a
backfill cannot re-score; days scored by an earlier assessor are left alone for
the same reason, since the dashboard plots every assessor it knows and a second
entry for one day would put two points on that date. `--missing-assessor`
fills those days deliberately. The workers are ordinary `llm-assessment-gemini`
runs carrying their target date, one durable row each, which the wrangler runs
one at a time; a failed day stops nothing.

## Not covered

The wasted-time factor the rules ask for (task walltime against error time) is not computed and is not planned; whatever an assessor writes about it stays in that part's log. Splitting a session at task boundaries is not built; it becomes necessary only if per-session yields on the largest sessions prove thin against the small ones.
