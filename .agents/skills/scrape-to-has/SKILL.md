---
name: scrape-to-has
description: Search jobs using the existing /scrape and /setup configuration, then publish one topic per vacancy to Hub Agentic Space. Use for /scrape-to-has or requests to publish scraped vacancies to the Hub.
---

Read and execute `workflows/scrape-to-has.md` from the repository root.
Reuse `.claude/skills/job-scraper/SKILL.md` as the canonical search workflow;
do not duplicate its rules or maintain separate profile/search configuration.
Honor the user's focus, broad, health, dry-run and limit arguments.
In interactive Codex, collect in this session and then invoke the deterministic
Python publisher as specified. Never recursively launch the collector.
