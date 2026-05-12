#!/usr/bin/env python3
"""Build guide/index.html — a single self-contained HTML page with all
screenshots embedded as base64 data URIs.

Run from the repo root: `python3 guide/build.py`. The output is checked
into git as the canonical guide artifact (served at `/guide` by the local
server). PNGs in this directory are the source-of-truth images; rebuild
this script after capturing fresh screenshots.
"""
from __future__ import annotations

import base64
from pathlib import Path
from textwrap import dedent


GUIDE = Path(__file__).parent


def img(filename: str, alt: str) -> str:
    data = (GUIDE / filename).read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f'<img src="data:image/png;base64,{b64}" alt="{alt}">'


# (anchor, title, image-file, alt, paragraphs[]). title=None continues the
# previous section's heading (used for multi-screenshot sections).
SECTIONS: list[tuple[str, str | None, str, str, list[str]]] = [
    ("project-list", "Project list", "01-project-list.png", "Project list",
     ["The home page lists every project on disk, with segment count, language count, and last-updated time. The header buttons open dialogs for creating a single project or bulk-importing from CSV."]),
    ("new-project", "New project", "02-new-project-dialog.png", "New project dialog",
     ["Pick a type (<strong>Poll</strong> = one question with N indexed options; <strong>Announcement</strong> = flat body segments; <strong>IVR menu</strong> = branching call flow), name the project, and create. Languages, paces, templates, and lexicon can all be edited later."]),
    ("import-csv", "Import projects from CSV", "03-import-csv-dialog.png", "Import CSV dialog",
     ["Bulk-create polls or announcements from a single CSV. The <code>group_id</code> column groups rows into projects; expand &ldquo;CSV schema reference&rdquo; for the full column list."]),
    ("project-editor", "Project editor", "04-project-editor.png", "Project editor",
     [
         "Each segment shows its source text, the rendered IVR wrapper, and per-language translation cells. Tags like &ldquo;preset on&rdquo; and &ldquo;needs translation&rdquo; make it obvious what still needs work.",
         "Click <strong>Generate</strong> on the segment header to translate + synthesize every selected language in one pass. The per-cell ⟳ button re-rolls just one language; ✎ opens an editor for the translated text (see <a href=\"#manual-edit\">Hand-editing translations</a> below).",
     ]),
    ("project-settings", "Project settings", "05-project-settings.png", "Project settings",
     [
         "The collapsed disclosure expands into Languages (with per-language pace overrides), Edit workflow, Pronunciation lexicon (global plus per-language), and Templates (the IVR wrappers around questions and options).",
         "<strong>Auto-regenerate on edit</strong> is off by default — edits to a segment's English text are saved after a 5-second pause but no regen fires. Click the segment's <strong>Generate</strong> button when you're ready. Flip the toggle on if you'd rather have the old behavior where edits auto-cascade into translation + synthesis.",
     ]),
    ("manual-edit", "Hand-editing translations", "13-manual-edit-editor.png", "Translation editor open in the Hindi cell",
     [
         "Each language cell has a ✎ button that opens an inline editor on the translated text. Type whatever you want — fix a name, swap in a regional phrasing, or replace the auto-translation entirely. <strong>Save &amp; synthesize</strong> stores the wording and re-renders just that language's audio.",
     ]),
    ("manual-edit-saved", None, "14-manual-edit-saved.png", "Manual pill after saving the hand-edited translation",
     [
         "After saving, the cell shows a <strong>manual</strong> pill so it's clear the translation isn't auto-generated. Subsequent <strong>Generate</strong> clicks leave hand-edited slots alone — auto-translate only fills in the langs that don't already have a translation.",
         "<p class=\"sub\">Editing the English text, swapping templates, changing the lexicon, or toggling the preset clears the manual flag — at that point the source has shifted and the manual wording is stale. To revert one slot to auto-translation, open the editor and save with an empty textarea; the next regen will translate fresh.</p>",
         "<p class=\"sub\">With rotations active, each rotation row in the cell has its own ✎ button — option translations differ per rotation (the &ldquo;press {n}&rdquo; number changes) so they're edited independently.</p>",
     ]),
    ("merge", "Merging audio for IVR upload (poll only)", "10-merge-section.png", "Merge section — empty state",
     [
         "Below the segments grid on every poll project, &ldquo;Merge audio&rdquo; assembles the question + every option (in canonical order) into a single MP3 per language — the format most IVR systems want to ingest. Three knobs across the top:",
         "<ul>"
         "<li><strong>Gap (s)</strong> — silence inserted between segments. Defaults to 1.0&nbsp;s; bump it up for slower-paced flows or down for tight ones.</li>"
         "<li><strong>Beep at end</strong> — appends a short 800&nbsp;Hz pulse so the IVR knows the prompt has finished. On by default.</li>"
         "<li><strong>Include preamble</strong> — picks between the templated lead-in (&ldquo;Namaskar, we are calling from Prashnam…&rdquo;) and the bare-body version of the question. Toggling this changes which variant the Merge button produces.</li>"
         "</ul>",
         "All three settings persist per project — the next merge starts with whatever you last used.",
     ]),
    ("merge-after", None, "11-merge-section-after.png", "Merge section — after merging both languages",
     [
         "&ldquo;Merge all languages&rdquo; runs every selected language in one pass and inline <code>&lt;audio&gt;</code> players appear with the result. Per-language <strong>Merge</strong> buttons re-roll just one row when you've fiddled with that language alone. The variant label next to each language name (e.g. &ldquo;(with preamble)&rdquo;) makes it obvious which file the player is loading.",
         "Merged files land under <code>projects/&lt;id&gt;/merged/&lt;lang&gt;_with_preamble.mp3</code> (or <code>_no_preamble.mp3</code>) and are bundled into the project zip alongside the per-segment audio.",
     ]),
    ("merge-gain", "Per-language gain", "12-merge-gain-bumped.png", "Hindi nudged to +3 dB",
     [
         "TTS voices don't all hit the same loudness — every clip is normalized to &minus;16 LUFS during synthesis, but if one language still sounds quieter than the rest you can rescue it with the per-language <strong>Gain</strong> slider (capped at &plusmn;6&nbsp;dB). Release the slider and the row auto-re-merges; the audio player picks up the new file automatically. The slider value is saved per project, so the next merge applies it without any extra clicks.",
         "<p class=\"sub\">Any time you re-roll a segment's audio after merging, the affected language row turns amber and the status line reads &ldquo;out of date — re-merge after regenerating&rdquo;. Hit <strong>Merge</strong> on that row (or <strong>Merge all languages</strong>) to refresh.</p>",
     ]),
    ("onboarding", "Onboarding wizard", "06-onboarding-wizard.png", "Onboarding wizard",
     ["First-time setup is just two clicks: pick an engine, then download. The &ldquo;Run on this computer&rdquo; engine ships the AI4Bharat models locally — no Hugging Face account, no token, no T&amp;Cs click-through (we mirror the weights ungated under <a href=\"https://huggingface.co/naklitechie\"><code>naklitechie/*</code></a>). &ldquo;Sarvam.ai (cloud)&rdquo; uses an API key instead."]),
    ("help", "Help", "07-help-modal.png", "Help modal",
     ["The &ldquo;?&rdquo; button in the topbar opens a Quick start checklist plus pointers to where projects, lexicons, and templates live on disk, and how to reset onboarding."]),
    ("ivr-dag", "IVR DAG editor", "08-ivr-dag-editor.png", "IVR DAG editor",
     ["IVR projects render as a node-graph. Five segment types — <code>prompt</code>, <code>menu</code>, <code>response</code>, <code>bridge</code>, <code>terminator</code> — wired by DTMF edges (<code>1</code>–<code>9</code>, <code>0</code>, <code>*</code>, <code>#</code>) plus <code>timeout</code> and <code>invalid</code> fall-throughs. Drag nodes to move; drag from a port to wire an edge. Click a node to edit its text + audio in the segment editor below."]),
    ("walk-sim", "IVR walk simulator", "09-walk-simulator.png", "IVR walk simulator",
     ["&ldquo;▶ Walk&rdquo; opens a 12-key DTMF keypad (plus <code>timeout</code> / <code>invalid</code> chips) that plays the active node's audio in your chosen language. Pressing a key follows the matching edge; a breadcrumb trail shows the path. Stops on a terminator or an unmapped key. End-to-end dry runs without a phone in the loop."]),
]


CSS = dedent("""
    :root {
      --bg: #f6f1e1;
      --card: #ffffff;
      --line: #e6dfca;
      --line-strong: #cfcabb;
      --fg: #1b1b1b;
      --muted: #6b6258;
      --accent: #1a3a78;
      --brand: #d9341c;
    }
    * { box-sizing: border-box; }
    html, body { margin: 0; padding: 0; background: var(--bg); color: var(--fg);
      font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }
    header.top { background: #fff; border-bottom: 1px solid var(--line);
      padding: 14px 24px; display: flex; align-items: baseline; gap: 18px; }
    header.top .brand { color: var(--brand); font-weight: 700; font-size: 17px; text-decoration: none; }
    header.top .sub { color: var(--muted); font-size: 13px; }
    main { max-width: 940px; margin: 0 auto; padding: 28px 24px 80px; }
    h1 { font-size: 26px; margin: 0 0 6px; }
    .lede { color: var(--muted); margin-top: 0; }
    nav.toc { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
      padding: 14px 18px; margin: 22px 0 32px; }
    nav.toc h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .04em;
      color: var(--muted); margin: 0 0 8px; font-weight: 600; }
    nav.toc ul { list-style: none; margin: 0; padding: 0; columns: 2; column-gap: 32px; }
    nav.toc li { break-inside: avoid; padding: 2px 0; }
    nav.toc a { color: var(--accent); text-decoration: none; }
    nav.toc a:hover { text-decoration: underline; }
    section { margin: 36px 0; }
    section h2 { font-size: 19px; margin: 0 0 10px; }
    section img { max-width: 100%; height: auto; display: block; margin: 10px 0 14px;
      border: 1px solid var(--line); border-radius: 8px;
      box-shadow: 0 2px 6px rgba(0,0,0,.04); background: #fff; }
    section p { margin: 8px 0; }
    section p.sub { color: var(--muted); font-size: 14px; }
    section ul { margin: 8px 0 12px 18px; padding: 0; }
    section ul li { padding: 3px 0; }
    code { background: #efe9d7; padding: 1px 5px; border-radius: 4px; font-size: 88%; }
    a { color: var(--accent); }
""").strip()


def render() -> str:
    parts: list[str] = []
    parts.append("<!doctype html>")
    parts.append("<html lang=\"en\">")
    parts.append("<head>")
    parts.append("<meta charset=\"utf-8\">")
    parts.append("<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">")
    parts.append("<title>prashnam-voice — visual guide</title>")
    parts.append(f"<style>{CSS}</style>")
    parts.append("</head>")
    parts.append("<body>")
    parts.append("<header class=\"top\">")
    parts.append("  <a class=\"brand\" href=\"/\">prashnam-voice</a>")
    parts.append("  <span class=\"sub\">Visual guide</span>")
    parts.append("</header>")
    parts.append("<main>")
    parts.append("<h1>prashnam-voice — visual guide</h1>")
    parts.append("<p class=\"lede\">A visual tour of the main features. Every screenshot is captured against the running web app.</p>")

    parts.append("<nav class=\"toc\"><h2>Contents</h2><ul>")
    for anchor, title, _img, _alt, _body in SECTIONS:
        if title is None:
            continue
        parts.append(f"  <li><a href=\"#{anchor}\">{title}</a></li>")
    parts.append("</ul></nav>")

    for anchor, title, img_file, alt, paragraphs in SECTIONS:
        parts.append(f"<section id=\"{anchor}\">")
        if title:
            parts.append(f"  <h2>{title}</h2>")
        parts.append("  " + img(img_file, alt))
        for p in paragraphs:
            stripped = p.strip()
            if stripped.startswith(("<ul>", "<p")):
                parts.append("  " + p)
            else:
                parts.append(f"  <p>{p}</p>")
        parts.append("</section>")

    parts.append("</main></body></html>")
    return "\n".join(parts)


def main() -> None:
    out = GUIDE / "index.html"
    html = render()
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} — {len(html):,} bytes")


if __name__ == "__main__":
    main()
