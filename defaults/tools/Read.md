### Read

Reads a file from the filesystem. The `file_path` parameter must be an absolute path.

- By default reads up to 2000 lines from the beginning of the file. You can specify an offset and limit for long files.
- Lines longer than 2000 characters are truncated.
- Results are returned in cat -n format, with line numbers starting at 1.
- Can read images (PNG, JPG, etc.) — contents are presented visually.
- Can read PDF files. For large PDFs (more than 10 pages), you must provide the `pages` parameter to read specific page ranges (e.g., "1-5"). Maximum 20 pages per request.
- Can read Jupyter notebooks (.ipynb) — returns all cells with their outputs.
- Can only read files, not directories. Use `ls` via Bash for directories.
- Read multiple potentially useful files in parallel when possible.
