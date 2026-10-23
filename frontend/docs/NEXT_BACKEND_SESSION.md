# Next backend session prompt

Continue directly in this project folder. Read AGENTS.md, README.md, TEST_RESULTS.md, IDEATION.md and relevant code first.

Current reported state: 136 deterministic tests passed with no model calls; Gemma e2b live evaluation passed 8/10 cases with median 84 seconds (historical, one run per case; the current repeated measurement is TEST_RESULTS.md section 18); one fully rendered live repair reached awaiting_review, accepted:false, with all eight preservation invariants passing. diagnosis.py defines the strict schema; pipeline.py runs diagnosis through validated proposal and previews. Known gaps: malformed evidence for ambiguous complaints and failure to respect overridden_by, blocked by engine validation. gemma4:e4b is installed but untested. This folder was not a Git repository. A separate React frontend is available; do not build a competing UI.

Goal: assess the larger model and prepare the backend for React integration.

1. Recheck the deterministic suite. Preserve validators, bounds, invariants, originals and explicit approval.
2. Inspect live_eval.py CLI help, then run the installed gemma4:e4b on the same ten cases. Save results separately from both prompt revisions. No model downloads or paid services.
3. Compare both models case by case: schema validity, abstention, overridden_by, latency and preservation. Ten cases are not a general accuracy estimate.
4. Investigate the two failures. Make only justified prompt or schema-handling changes. Never silently convert malformed output to success or weaken validation. Add focused regression tests; keep baseline and revised live results separate.
5. Inspect the application architecture. If no HTTP layer exists, add a small FastAPI adapter around the pipeline, not a duplicate engine.
6. Support upload, asynchronous status, paragraph/effective-style evidence, diagnosis/proposal, rendered previews, explicit accept/reject and accepted-candidate download. Revalidate candidate integrity at approval and download. Completed analysis never implies approval.
7. Use opaque IDs and server-controlled paths; reject traversal, invalid/oversized uploads. Bind localhost by default. Keep documents and previews out of routine logs. Do not claim renderer sandboxing.
8. Map actual diagnosis statuses and all seven failure codes to clear responses. Keep invalid_response distinct from valid clarification. Preserve debugging evidence without logging document content casually.
9. Keep fixture/demo responses separate and stamped correctly. No silent mock fallback when Gemma is unavailable.
10. Document actual request/response shapes, local startup, long-running inference polling and protected preview serving. Reconcile frontend/docs/API_CONTRACT.md to the real implementation.
11. Test important API and approval boundaries with injected diagnosis. Run a live slice if possible and report live and deterministic verification separately.

Do not add MongoDB, Sentry, Tinker, cloud deployment or auth services yet. Do not initialize Git without asking. Do not publicly expose this local document-processing service. Finish with exact changes, commands, results, model comparison, limitations and API contract location. Be explicit about anything not checked in Word or with real documents.
