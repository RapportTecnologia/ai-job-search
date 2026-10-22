# /scrape-to-has - Search jobs and publish one Hub topic per vacancy

Arguments: `[focus|broad|health] [--dry-run] [--limit N]`.

Read and execute `workflows/scrape-to-has.md` from the project root.
This is the `/scrape` workflow plus Hub publication, using the same search
configuration and portal skills. Do not launch a nested Codex collector from
Claude: perform the collection phase in this session, then call the Python
publisher with the handoff file as specified in the workflow.

User arguments: $ARGUMENTS
