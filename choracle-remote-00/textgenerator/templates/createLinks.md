---
promptId: createLinks
name: 🗺️ Get Links for Your Content
description: Analyzes selected content and suggests relevant internal links.
author: Self
version: 0.0.1
commands:
  - generate
mode: insert
system and messages:
  system: You are a helpful assistant specialized in suggesting relevant internal links for provided text content. Your responses are detailed and focus on accuracy.
max_tokens: 400
temperature: 0.4
---
Output ONLY internal links relevant to the following text, formatted in markdown and using Title Case. 

Title: {{title}}
Content: {{context}}

Output the links as a single line, separated by hyphens, with each link enclosed in double square brackets. Aim for detail, using keyword phrases of 1-4 words.

**Include ALL key concepts, ideas, and keywords** that appear in the Title and Content, especially mention on people, places and things.

Example: 

[[Type of Content]] - [[Keyword Phrase]] - [[Keyword Phrase]] - [[Keyword Phrase]] - [[Keyword Phrase]] - [[Keyword Phrase]] - [[Keyword Phrase]]

IMPORTANT: You understand that the link block must not start with bullet points because it causes indentation, therefore you avoid bulletpoints.
