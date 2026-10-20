# Paste into Codex in your existing project folder

Continue in this project. Read AGENTS.md and the current README, TEST_RESULTS, pipeline.py, diagnosis.py, API code and frontend/docs/API_CONTRACT.md first.

I have added the provided What the Doc?! React frontend under frontend/. Preserve the orange/sloth workshop design. Do not rebuild it in a different style. Keep all newer backend files and tests; this ZIP contains no replacement engine.

First run npm ci, npm test and npm run build in frontend/. Inspect the actual backend contract. Reconcile src/api.js with the existing API, then connect the React screens end to end. The proposed contract is only a sketch; the real diagnosis schema and failure codes are authoritative.

Replace simulated file selection/analysis with real document import, paragraph/effective-style evidence, asynchronous diagnosis polling, authoritative rendered page previews and explicit review. Replace demo JSON downloads with accepted DOCX downloads. Keep a clearly separated sample mode for demonstrations; never silently fall back to it when live calls fail.

Preserve original documents and enforce approval/integrity on the server. Do not treat browser state as authorization. Show clarification only for a valid needs-clarification diagnosis; malformed model output stays invalid_response. Display no-supported-repair, runtime unavailable, validation failure, rendering failure, expired job and tampered candidate honestly. Do not invent percentage progress. CPU inference can be slow.

Make paragraph IDs come from the backend. Do not reuse hard-coded sample indices. Replace HTML Paper with real render images; keep the evidence selector usable without assuming page coordinates. Support original/candidate page-count differences and missing previews.

Implement one complete vertical slice before expanding scope. Verify upload -> selected paragraph -> diagnosis -> review -> accept/reject -> download, with injected diagnosis tests plus one separately reported live run when the runtime is available. Preserve the existing deterministic suite. Run browser desktop/mobile/accessibility checks if browser tools are available.

Do not add MongoDB, Sentry, Tinker, a paid host or public deployment yet. Keep this local. Do not claim Word verification, renderer sandboxing or real-document validation unless actually performed. Report remaining gaps and the exact startup commands.
