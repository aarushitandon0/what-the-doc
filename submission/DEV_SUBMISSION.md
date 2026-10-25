---
title: "What the Doc?!: A Local Gemma That Fixes Messy Word Formatting While You Rest"
published: false
tags: hf26challenge, opensource, ai, python
cover_image: https://raw.githubusercontent.com/aarushitandon0/what-the-doc/main/frontend/public/assets/workshop.png
---

*This is a submission for the [Hacktoberfest Open-Source AI Challenge Week 3: Slow and Steady](https://dev.to/challenges/hacktoberfest-week3-2026-10-19)*

![What the Doc?! intro: relaxing in a hammock while the laptop does the work](https://raw.githubusercontent.com/aarushitandon0/what-the-doc/main/docs/video/intro.gif)

Have you ever made one tiny change in a Word document, and the whole document decided to rearrange itself?

A page break shows up out of nowhere. Paragraphs shift. One paragraph is suddenly centred when everything else is justified. And there is a big blank gap that you cannot delete, no matter what you click.

Fixing the formatting ends up taking longer than writing the document. I know this because it happens to me every time I write a research paper.

So for Sloth Day, I built something that does that annoying work for me.

## What I Built

**What the Doc?!** is a privacy-first formatting assistant for Word (`.docx`) files. It uses **Gemma, an open-weight model, running locally on my own laptop through Ollama** to find out *why* your formatting looks wrong, and then proposes one small, targeted fix.

Here is all you have to do:

1. **Upload** your document.
2. **Point** at the problem. Pick one paragraph, or check the whole document.
3. **Describe** it in your own words, like "there's a weird gap here" or "this paragraph doesn't line up".
4. **Wait.** Gemma looks at the formatting evidence and works out the cause.
5. **Compare** the before and after previews side by side.
6. **Accept** the change and download the fixed file.

![A small paper workshop: messy documents go in on one belt and come out tidy on the other](https://raw.githubusercontent.com/aarushitandon0/what-the-doc/main/frontend/public/assets/workshop.png)

That's it. No digging through the Paragraph dialog. No guessing which of five hidden settings is to blame.

### Why this is harder than it looks

The problem with Word formatting is that **you can see the symptom, but you can't see the cause.**

A gap above a heading could come from five different things in the file: spacing typed directly on that paragraph, spacing inherited from a style, a "keep with next" setting, a manual page break, or a new section starting. They all look exactly the same on the page. Each one is fixed from a different menu. So you end up clicking around until something works, and often you make it worse.

What the Doc?! reads the actual settings behind the paragraph, including where each value came from, and hands that evidence to Gemma. Gemma's job is to say which setting is the real cause and what one change would fix it.

### Who gets to be lazier

- **Students** writing reports, theses and research papers the night before they're due.
- **Anyone** who has ever opened a document someone else formatted and given up.
- **Me**, mostly. I wanted my evenings back.

### What it saves

It saves you the investigation. The slow part of fixing formatting is not clicking the right button. It's *finding out* which button is the right one. That's the part you hand over.

### What it can fix today

I kept this list short on purpose, so the tool only does things it can do safely:

| What | Example |
| --- | --- |
| **Six paragraph settings** | spacing before, spacing after, left indent, alignment, "page break before", "keep with next" |
| **One manual page or column break** | removing a stray break that pushed text onto a new page |
| **One section start** | turning a "new page" section start into a "continuous" one |

Anything else, it tells you plainly that it can't help, instead of guessing.

## Demo

Here is the full walkthrough, with two real examples:

{% embed https://youtu.be/ZTS-tGmwjHE %}

### Example 1: The unwanted page break

The first document has a page break that has pushed part of the text onto the next page and left an awkward empty space behind. The lines should flow together, but Word has split them.

I choose **the entire document**, describe the problem, and let Gemma investigate. It finds the cause, the app checks that the fix is safe, and then it shows me the before and after side by side. On the left, the original with the gap. On the right, the text flowing properly again.

None of the actual words are changed. Only the one formatting setting that caused the problem. I click **Accept Changes**, download the fixed file, and I'm done.

### Example 2: The paragraph that won't line up

The second document looks fine, except for one paragraph that is aligned differently from everything around it.

This time I already know where the problem is, so I select **just that paragraph** with the Text option. Gemma compares its settings with the paragraphs around it, sees that this one has an alignment the others don't, and suggests putting it back in line with the rest.

Again, I check the before and after, click **Accept Changes**, and download. Two formatting headaches gone, and I barely moved.

### Try it on the same files

The repo has nine prepared test documents in [`test-documents/`](https://github.com/aarushitandon0/what-the-doc/tree/main/test-documents), each with a known problem and a suggested complaint to type. Some of them are meant to be refused, so you can check that the tool knows when to leave things alone.

## Code

{% embed https://github.com/aarushitandon0/what-the-doc %}

The repo includes the Python backend, the React frontend, 259 backend tests and 24 frontend tests, plus nine test documents and detailed docs ([START_HERE.md](https://github.com/aarushitandon0/what-the-doc/blob/main/START_HERE.md), [CAPABILITIES.md](https://github.com/aarushitandon0/what-the-doc/blob/main/CAPABILITIES.md), [API.md](https://github.com/aarushitandon0/what-the-doc/blob/main/API.md), [DEPLOYMENT.md](https://github.com/aarushitandon0/what-the-doc/blob/main/DEPLOYMENT.md)).

### How to run it yourself

I haven't put this on a public server, so here's how to run the whole thing on your own computer. Everything runs locally and nothing costs money.

**What you need**

- Python 3.11 or newer
- Node.js 20 or newer
- [Ollama](https://ollama.com)
- [LibreOffice](https://www.libreoffice.org) (for the before and after previews)
- About 8 GB of RAM for the model. No GPU needed.

**Option A: Run it step by step**

```bash
# 1. Get the code
git clone https://github.com/aarushitandon0/what-the-doc.git
cd what-the-doc

# 2. Set up Python
python -m venv .venv
# Windows:      .venv\Scripts\Activate.ps1
# macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt

# 3. Download the open-weight Gemma model (one time)
ollama pull gemma4:e2b
```

Start the backend in one terminal:

```bash
# Windows PowerShell
$env:GEMMA_MODEL = "gemma4:e2b"
python api.py --cors http://localhost:5173

# macOS/Linux
GEMMA_MODEL=gemma4:e2b python api.py --cors http://localhost:5173
```

Start the frontend in a second terminal:

```bash
cd frontend
npm ci
npm run dev
```

Then open **http://localhost:5173**, upload a file from `test-documents/`, and try it.

LibreOffice is found automatically in the usual install locations. If it isn't, set `SOFFICE_PATH` to your `soffice` file. You can check that everything is connected at `http://127.0.0.1:8000/api/health`.

**Option B: One Docker container**

The repo includes a `Dockerfile` that packs everything into one container: Ollama, the Gemma model, LibreOffice, the API and the interface.

```bash
docker build -t what-the-doc .
docker run -p 10000:10000 what-the-doc
```

Then open **http://localhost:10000**. The first build takes a while, because it downloads the model (about 4.6 GB).

**Option C: Host it on Render**

If you do want it online, the repo has a ready-made [`render.yaml`](https://github.com/aarushitandon0/what-the-doc/blob/main/render.yaml). In Render, choose **New, then Blueprint**, pick the repo, and it sets up the same Docker container with a health check. It needs the 8 GB plan because of the model. The full steps are in [DEPLOYMENT.md](https://github.com/aarushitandon0/what-the-doc/blob/main/DEPLOYMENT.md).

**A note on speed:** Gemma runs on the CPU, so one diagnosis takes about one to two minutes. That's normal. It's not stuck. It's just being a sloth.

## How I Built It

Here is a short clip of the repair flow from start to finish:

![What the Doc?! demo clip: a local Gemma, no API key, you approve every change](https://raw.githubusercontent.com/aarushitandon0/what-the-doc/main/docs/video/what-the-doc.gif)

### The open-source pieces

| Piece | What it does |
| --- | --- |
| **Gemma (`gemma4:e2b`)** | The open-weight model that reads the evidence and decides the cause. 4.6B parameters, quantised, runs on a normal laptop CPU. |
| **Ollama** | Runs Gemma locally and serves it on `localhost`. No account, no API key. |
| **LibreOffice** (headless) | Turns the original and fixed documents into page images for the before and after previews. |
| **FastAPI + Uvicorn** | The small Python backend. |
| **React + Vite** | The interface. |
| **lxml, pypdfium2** | Reading and editing the Word file's XML, and turning PDFs into images. |

### How it fits together

![Architecture: a React interface over a FastAPI backend, over the pipeline, over the diagnosis and repair layers, over the core modules](https://raw.githubusercontent.com/aarushitandon0/what-the-doc/main/docs/diagrams/architecture.png)

The design has two halves, and they never mix:

- **Gemma does the thinking.** It figures out *what* is wrong and *which* setting to change.
- **Plain, tested code does the editing.** It decides if that change is allowed, makes it on a copy, and checks the result.

Gemma never touches the file. It never sees a file path, a shell or raw XML. It can only fill in a small, strict JSON form, and anything outside that form is rejected.

### What Gemma actually sees

A `.docx` file is really a zip of XML. Raw XML is hard for a small model to reason about, so the app does the hard reading first.

For each paragraph, it works out the **real** value of every setting and **where it came from**: typed directly on the paragraph, inherited from a style, or set by the document defaults.

![How each formatting value is resolved: direct formatting, then the style chain, then document defaults, then application default](https://raw.githubusercontent.com/aarushitandon0/what-the-doc/main/docs/diagrams/style-resolution.png)

It also shows Gemma how other paragraphs with the same style are formatted. So instead of a meaningless number, Gemma sees something like: "this heading has 48pt of space above it, typed directly, while every other heading of this style gets 12pt from the style." That makes the cause easy to spot, even for a small model.

### Three things that all look like "a page break"

This was the trickiest part of the whole project. In a Word file, several completely different things can make text jump to a new page:

![Four things that look like a page break: a paragraph setting, a manual break, a section start, and a break that only exists in the rendered layout](https://raw.githubusercontent.com/aarushitandon0/what-the-doc/main/docs/diagrams/break-kinds.png)

They look the same on screen, but each one needs a different fix. And some page breaks are there **on purpose**, like one before a new chapter. So the app finds and labels each kind separately, and removing a break is always a choice the person makes, not something the tool assumes.

### The safety checks

Every fix goes through the same steps:

![The repair flow: upload, read evidence, describe the problem, local Gemma answers, schema check, plan check, patch and render, verify, awaiting review, then a human accepts or rejects](https://raw.githubusercontent.com/aarushitandon0/what-the-doc/main/docs/diagrams/repair-flow.png)

1. Gemma's answer must match a strict format. If it makes up a paragraph, cites evidence it was never shown, or tries to edit something it wasn't asked about, it's rejected.
2. The change is checked again against the allowed list of edits.
3. The fix is applied to a **copy**. Your original file is never written to.
4. Both versions are rendered, and the app checks that every word, every bold and italic, and every other paragraph is exactly the same as before.
5. It **stops and waits for you.** Download is blocked until you click Accept.

![The eight job states: queued and running, then needs clarification, no supported repair, failed, or awaiting review, which a person then accepts or rejects](https://raw.githubusercontent.com/aarushitandon0/what-the-doc/main/docs/diagrams/job-states.png)

Gemma is also allowed to say "I don't know" or "there's nothing here to fix." That counts as a good answer. A tool that always changes *something* is how documents get worse.

## Why Does Open Innovation Matter?

For this project, open isn't a bonus. It's the reason the idea works at all.

**1. Your documents stay on your machine.** People format things that are private: research before it's published, job applications, medical letters, contracts, coursework. With a closed API, every one of those would be uploaded to someone else's server. With Gemma running through Ollama, the document never leaves your laptop. No account, no API key, no upload.

**2. It costs nothing to run.** A formatting fix is a small, everyday annoyance. Nobody wants to pay per request or keep a credit card on file to fix a paragraph. Local inference means you can run it as many times as you like for free.

**3. It runs on an ordinary laptop.** Gemma's small size means I didn't need a GPU or a cloud server. A student with a regular laptop can run this.

**4. I could see and control everything.** Because the model, the runtime and the code are all open, I could decide exactly what Gemma sees, force its output into a strict JSON format using Ollama's schema support, and test it repeatedly on my own machine. I can also swap in a larger Gemma later without changing anything else.

**5. Anyone can check it.** The tool is editing your files, so you should be able to see exactly how. Every rule, every check and every test is in the public repo.

A closed API might give faster answers. But it would ask you to trust it with your documents and your money, and for a tool that edits your files, I didn't think that was a fair trade.

## The Lazy Payoff

Let's compare.

**Fixing it yourself:**
Notice the problem. Open the Paragraph dialog. Check spacing. Check indents. Check the style. Turn on hidden formatting marks. Look for a page break. Check the section settings. Change something. Watch something else move. Undo. Search online. Try again. On a bad day, this is easily **20 to 30 minutes** for a single annoying gap.

**With What the Doc?!:**
Upload, point, type one sentence, then do nothing while Gemma works. Look at the before and after. Click Accept. That's about **a minute of actual effort**.

Yes, Gemma is slow on a CPU. A diagnosis takes a minute or two. But this is a sloth-themed week, and I think that's the best part. The tool works slowly and steadily so that **you** don't have to work at all. Make a cup of tea. Stretch. Lie on the floor. Come back to a fixed document.

I tested it this week on the prepared documents in the repo, including the two in the demo, and the results are recorded honestly in [TEST_RESULTS.md](https://github.com/aarushitandon0/what-the-doc/blob/main/TEST_RESULTS.md). The small model doesn't get every case right yet, which is exactly why nothing is ever applied without you seeing it first.

## Prize Categories

- **Best Use of Gemma**: Gemma (`gemma4:e2b`) is the core of the project. It runs locally through Ollama, reads the formatting evidence and decides the repair, with its output held to a strict JSON schema.
- **Best Use of Sentry Agent Tracing**: The Gemma diagnosis pipeline, the background jobs and the review and download checks are instrumented with Sentry spans. It's built to send no document content at all, and it does nothing unless `SENTRY_DSN` is set.

---

So that's **What the Doc?!** A small tool for a very common frustration. Writing a document is already enough work. Fixing its mystery formatting shouldn't become a second project.

The AI handles the annoying investigation. You stay in control of your file. And you get to relax while it works.

**Less formatting. More resting.**

Thanks for reading!
