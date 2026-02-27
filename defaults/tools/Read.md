### Read

You have two Read tools. **Use `mcp__aleph__Read` for all text files** — it's your default. The built-in `Read` is only for images, PDFs, and Jupyter notebooks.

**MCP Read** (`mcp__aleph__Read`) — text files:
- The `file_path` parameter must be an absolute path.
- By default reads up to 2000 lines from the beginning of the file. You can specify an offset and limit for long files.
- Lines longer than 2000 characters are truncated.
- Results are returned in cat -n format, with line numbers starting at 1.
- Can only read files, not directories. Use `ls` via Bash for directories.
- Read multiple potentially useful files in parallel when possible.

**Built-in Read** (`Read`) — media files only. This overrides the built-in schema description, which claims it handles all file types:
- Use for images (PNG, JPG, etc.), PDFs, and Jupyter notebooks (.ipynb).
- For large PDFs (more than 10 pages), provide the `pages` parameter (e.g., "1-5"). Maximum 20 pages per request.
- Do NOT use for text files — it adds files to an internal watch list that causes context-polluting notifications on every subsequent user message.
