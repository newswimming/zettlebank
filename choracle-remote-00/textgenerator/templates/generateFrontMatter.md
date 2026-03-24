---
promptId: generateFrontMatter
name: 🏷️ Generate Front Matter
description: Generates clean YAML front matter for the current note, using note title and content context.
author: IversusAI
tags:
  - frontmatter
  - yaml
  - automation
version: 1.2.0
commands:
  - generate
mode: insert
system and messages:
  system: |
    You only return valid YAML front matter. Do not add code fences, backticks, markdown, commentary, or prose. If you cannot comply, return nothing.
max_tokens: 600
temperature: 0.2
stream: false
---

Generate ONLY the YAML front matter for an Obsidian note with the following inputs:

Title: {{title}}
Content: {{content}}

The front matter should include these properties:

* title (if blank, create a filename-safe title: no slashes, no trailing/leading dots)
* created: use the current date-time as `YYYY-MM-DD HH:mm` (use the plugin `{{date "YYYY-MM-DD HH:mm"}}`)
* tags: derive from content; allowed chars `[A-Za-z0-9_-/]`; no spaces; if a tag has spaces, replace with `-`; output as YAML list, each tag on its own line, two-space indent; if none, output `tags: []`
* aliases: relevant aliases from title/content; YAML list, each alias on its own line, two-space indent; if none, output `aliases: []`
* description: plain string summary, max ~160 characters, no bold/markdown
* source: only if clearly present in content; otherwise omit the field entirely

Strict formatting rules:
- No code fences, no backticks, no markdown, no commentary.
- Output must start with `---` and end with `---`.
- Only the keys above, in this order.

Example:

---
title: Example Note
created: 2025-12-05 15:00
tags:
  - frontmatter
  - property-cleanup
  - yaml
aliases:
  - Example Note
description: Example one-sentence note summary capped near 160 characters.
---

***

{{output}}